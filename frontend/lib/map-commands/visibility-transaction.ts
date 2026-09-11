import * as renderer from '@/lib/map-kit/renderer';
import { devOnly } from '@/lib/utils/logger';
import { enqueueUserMutation } from '@/lib/mapspec/user-mutation';
import {
  clearPendingPresentation,
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  mergePendingPresentation,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { ApiError, apiFetch } from '@/lib/api/transport';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
import { LOCK_CONFLICT_ERROR, partitionByLock } from '@/lib/workbench/layer-lock';
import { journalOnly, presentationCommand } from '@/lib/workbench/undo';
import type { MapCommandContext, MapCommandResult } from './types';
import {
  matchMapLayers,
  resolveLayerTargetsByRef,
  specLayerIdOf,
  type HudLayerLike,
} from './layer-identity';

/**
 * LayerVisibilityTransaction —— 可见性突变的单一事务（Goal C/D）。
 *
 * 每次可见性变更走同一深接口，杜绝「UI 一套 / Agent 一套 / finalize 一套」：
 *   resolve identity → lock gate（V5/W2：锁定层从目标集中剔除，全部被锁
 *   → typed layer_locked 冲突）→ desired（HUD store + pending presentation）
 *   → runtime（MapLibre setLayoutProperty 即时生效）
 *   → durability（后端 MapSpec patch_layer_presentation 提交，CAS）
 *   → postcondition（getLayoutProperty 读回验证）
 *   → evidence（confirmed / store_updated，绝不假成功）。
 *
 * durability 全局串行队列（review P1 修复）：finalize 突发 N 层 × 每层独立
 * fire-and-forget 会并发读同一游标 revision → 除首笔外全 409，且 superseded
 * 收敛会回滚本地 hide 决策。所有持久化提交进同一条 promise 链，逐笔读取
 * 前一笔推进后的 revision；superseded 时检查服务端真相是否已含期望值，
 * 不含则带新 revision 重试一次（仍失败 → 保留 pending，reconcile 兜底）。
 * runtime 已生效的突变不因后端提交失败而回滚。
 */

export interface VisibilityTransactionInput {
  layerId: string;
  visible?: boolean | null;
  opacity?: number | null;
  name?: string;
  color?: string;
  /** false = 跳过后端持久化（restore 内部路径已持真相时）。 */
  durable?: boolean;
  /**
   * false = 豁免 lock 门（仅限用户自身路径 —— 手动面板与批量操作先于本
   * 事务自查 lock；agent 通道缺省 true，锁定即 typed 冲突）。
   */
  respectLock?: boolean;
}

export interface VisibilityTransactionResult extends MapCommandResult {
  result?: {
    confirmed?: boolean;
    store_updated?: boolean;
    target_ids?: string[];
    /** 被 lock 门剔除的目标（部分冲突时非空 —— 不静默吞目标）。 */
    locked_layer_ids?: string[];
  };
}

/** #609: JSON null = "不修改该属性"——判存在性必须用 `!= null`。 */
function wantVisibility(visible: boolean | null | undefined): 'visible' | 'none' | undefined {
  return visible != null ? (visible ? 'visible' : 'none') : undefined;
}

interface MutationResponse {
  success?: boolean;
  mutation_revision?: number;
  mapspec?: { layers?: unknown[] } & Record<string, unknown>;
  correction_hint?: string;
}

function supersededFromError(err: unknown): MutationResponse | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: MutationResponse } | MutationResponse | null;
  if (!body || typeof body !== 'object') return null;
  if ('detail' in body && body.detail && typeof body.detail === 'object') {
    return body.detail;
  }
  return null;
}

interface PresentationPatch {
  visible?: boolean;
  opacity?: number;
}

function serverReflectsPatch(
  mapspec: { layers?: unknown[] } & Record<string, unknown> | undefined,
  specLayerId: string,
  patch: PresentationPatch,
): boolean {
  if (!mapspec) return false;
  const pres = presentationFromMapSpec(mapspec as never, specLayerId);
  if (patch.visible !== undefined && pres.visible !== patch.visible) return false;
  if (patch.opacity !== undefined && pres.opacity !== patch.opacity) return false;
  return true;
}

/**
 * 单笔 presentation 持久化（带一次 superseded 重试）。
 * 返回值仅供诊断；调用方不依赖其结果（fire-and-forget 语义）。
 */
async function postPresentationOnce(
  specLayerId: string,
  patch: PresentationPatch,
): Promise<'committed' | 'reflected' | 'retry' | 'lost'> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return 'lost';
  // #1201：POST 在飞期间可能切会话 —— 旧会话的迟到响应不得把 revision 与
  // committed spec 写进新会话游标（姊妹路径 user-mutation.ts Review R1
  // MAJOR-1 同款守卫；此前仅入队时检查一次）。
  const enqueuedSessionId = sessionId;
  const sessionStillCurrent = () =>
    getMapSpecSessionCursor().sessionId === enqueuedSessionId;
  try {
    const data = await apiFetch<MutationResponse>(
      `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
      {
        method: 'POST',
        body: {
          intent: 'patch_layer_presentation',
          expected_revision: revision,
          layer_id: specLayerId,
          ...patch,
        },
        ownerToken,
        label: 'Layer visibility durability commit',
      },
    );
    if (!sessionStillCurrent()) return 'lost';
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    if (data.mapspec) commitMapSpecDocument(data.mapspec, data.mutation_revision);
    clearPendingPresentation(specLayerId);
    return 'committed';
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) {
      devOnly.warn('[visibility-transaction] durability commit failed:', err);
      // #1078(G-2): 首试非 409 失败不清 pending —— 与 double-superseded 分支
      // 同原则（ST-P3-2）：compose 继续表达本地期望（store 行已隐藏 +
      // setLayoutProperty 已应用），清掉 pending 会让下一个 reconcile 把层
      // 复活成服务端旧态（面板藏/地图显示，且修复环因指纹被事务自身清掉
      // 而拒收）。重试由下一次 reconcile/pending 消费驱动。
      return 'lost';
    }
    // superseded：收敛 revision + 服务端真相；若真相已含期望值（并发同值
    // 写）→ 完成；否则调用方带新 revision 重试一次。
    // #1201：superseded 响应同样过会话复核（会话已切 → 整笔丢弃）。
    if (!sessionStillCurrent()) return 'lost';
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    if (superseded.mapspec) {
      commitMapSpecDocument(superseded.mapspec, superseded.mutation_revision);
    }
    clearPendingPresentation(specLayerId);
    return serverReflectsPatch(superseded.mapspec, specLayerId, patch)
      ? 'reflected'
      : 'retry';
  }
}

async function postPresentationWithRetry(
  specLayerId: string,
  patch: PresentationPatch,
): Promise<'committed' | 'reflected' | 'lost'> {
  const first = await postPresentationOnce(specLayerId, patch);
  if (first !== 'retry') return first;
  const second = await postPresentationOnce(specLayerId, patch);
  if (second === 'committed' || second === 'reflected') return second;
  // 'lost' 或再次 superseded（'retry'）：重新落 pending —— reconcile 继续表达
  // 本地期望真相，不静默丢决策（服务端偏差由下一次用户/agent 突变或修复
  // 循环收敛）。此前 'retry' 分支被类型断言吞掉且 pending 已清——agent
  // 隐藏决策在双 superseded 时无声丢失（ST-P3-2）。
  // #1078(G-2): 'lost'（首试非 409 失败）同样保留 pending —— 首试失败
  // 分支已不再清除，这里对二次 'lost' 补落，保证 pending 一定在。
  mergePendingPresentation(specLayerId, patch);
  return 'lost';
}

// durability 串行链与用户 mutation 共享（ST-P1-2）：同一 MapSpec mutation
// 端点的全部 CAS 写（用户 presentation / 视图 / 删除 + agent 可见性
// durability）逐笔排队，每笔读到前一笔推进后的 revision——跨来源并发
// 不再互 409。
function enqueueDurability(
  targets: { storeId: string; specLayerId: string }[],
  visible?: boolean | null,
  opacity?: number | null,
): void {
  const patch: PresentationPatch = {
    ...(visible != null ? { visible: Boolean(visible) } : {}),
    ...(opacity != null ? { opacity: Number(opacity) } : {}),
  };
  if (Object.keys(patch).length === 0) return;
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  void enqueueUserMutation(async () => {
    if (!enqueuedSessionId || getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
    for (const { specLayerId } of targets) {
      await postPresentationWithRetry(specLayerId, patch);
    }
  }).catch((err) => {
    // 队列自身绝不因单笔失败断裂。
    devOnly.warn('[visibility-transaction] durability queue error:', err);
  });
}

/**
 * 应用一次可见性事务。同步返回读回验证后的结果；durality 提交在全局
 * 串行队列后台进行（不阻塞 ack——ack 语义只覆盖可同步验证的 runtime 真相）。
 */
export function applyLayerVisibilityTransaction(
  ctx: MapCommandContext,
  input: VisibilityTransactionInput,
): VisibilityTransactionResult {
  const { map, getHudState } = ctx;
  const { layerId, visible, opacity, name, color } = input;

  // 1. 身份解析（ref → 多 spec 层目标，group 语义）
  const resolvedIds = resolveLayerTargetsByRef(layerId, getHudState);
  if (resolvedIds.length === 0) {
    return { status: 'failed', error: 'target_not_found' };
  }

  // 1.5 lock 门（V5/W2）：agent 通道（缺省）锁定目标即剔除；全部被锁 →
  // typed layer_locked 冲突（ack.error 机器可读，用户解锁是唯一 override）。
  const lockPartition = input.respectLock === false
    ? { allowed: [...resolvedIds], locked: [] as string[] }
    : partitionByLock(resolvedIds);
  const lockedTargets = lockPartition.locked;
  if (lockPartition.allowed.length === 0) {
    // W2/W9：typed 冲突入 journal（who/what 审计 —— agent 被用户锁拦截）。
    journalOnly({
      type: 'lock_conflict',
      label: `Agent 显隐操作被用户锁拦截：${lockedTargets.join(', ')}`,
      actor: 'agent',
    });
    return {
      status: 'failed',
      error: LOCK_CONFLICT_ERROR,
      result: { locked_layer_ids: lockedTargets, target_ids: resolvedIds },
    };
  }
  const targetIds = lockPartition.allowed;
  // 部分冲突时各出口 result 附加 locked_layer_ids（不静默）。
  const withLocked = (
    result: VisibilityTransactionResult['result'],
  ): VisibilityTransactionResult['result'] =>
    lockedTargets.length > 0 ? { ...result, locked_layer_ids: lockedTargets } : result;

  // V5/W4：agent 突变的 undo 载荷必须在 store 更新前捕获（步骤 3 会改写
  // visible/opacity）。用户路径（respectLock=false）不在此记录 —— 已由
  // toggle/opacity 提交函数记录，避免双重入栈。
  const undoBefore: { visible?: boolean; opacity?: number } = {};
  if (input.respectLock !== false && input.durable !== false) {
    if (visible != null) {
      const cur = (getHudState().layers ?? []).find(
        (l: HudLayerLike) => l.id === targetIds[0],
      ) as { visible?: boolean } | undefined;
      undoBefore.visible = cur?.visible !== false;
    }
    if (opacity != null) {
      const cur = (getHudState().layers ?? []).find(
        (l: HudLayerLike) => l.id === targetIds[0],
      ) as { opacity?: number } | undefined;
      undoBefore.opacity = cur?.opacity ?? 1;
    }
  }

  // 2. MapLibre 命中（双方案；目标在地图与 store 都不存在 → 真未命中）
  const matched = Array.from(new Set(targetIds.flatMap((id) => matchMapLayers(map, id))));
  const storeMatched = matched.filter(
    (id) => targetIds.some((t) => id === t || id.startsWith(`${t}__`)),
  );

  // 3. desired：store 更新 + pending presentation（多目标同值）
  const storeUpdates: Record<string, unknown> = {};
  if (visible != null) storeUpdates.visible = visible;
  if (opacity != null) storeUpdates.opacity = opacity;
  if (name !== undefined) storeUpdates.name = name;
  if (color !== undefined) {
    const existing = (getHudState().layers ?? []).find((l: HudLayerLike) => l.id === targetIds[0]);
    storeUpdates.style = { ...((existing as { style?: Record<string, unknown> } | undefined)?.style ?? {}), color };
  }
  const targetSpecPairs = targetIds.map((storeId) => {
    const hudLayer = (getHudState().layers ?? []).find((l: HudLayerLike) => l.id === storeId);
    return { storeId, specLayerId: specLayerIdOf(hudLayer, storeId) };
  });
  if (Object.keys(storeUpdates).length > 0) {
    for (const { storeId, specLayerId } of targetSpecPairs) {
      getHudState().updateLayer?.(storeId, storeUpdates);
      if (specLayerId && (visible != null || opacity != null)) {
        mergePendingPresentation(specLayerId, {
          ...(visible != null ? { visible: Boolean(visible) } : {}),
          ...(opacity != null ? { opacity: Number(opacity) } : {}),
        });
      }
    }
  }

  // 4. runtime：即时 MapLibre 突变
  if (matched.length > 0) {
    for (const id of matched) {
      renderer.updateLayerStyle(map, id, {
        visibility: wantVisibility(visible),
        // null 必须归一为 undefined：renderer 以 `opacity !== undefined` 判断
        opacity: opacity != null ? Number(opacity) : undefined,
        color: color as string | undefined,
      });
    }
  }

  // 5. durability：后端 desired state 提交（全局串行队列 + superseded
  //    重试；agent 路径此前缺失——reload 后 Agent 可见性决策丢失的根因）。
  if (input.durable !== false && (visible != null || opacity != null)) {
    enqueueDurability(targetSpecPairs, visible, opacity);
    // V5/W4 + R1-M3：agent 突变入 undo 栈。undoBefore 只对单目标层捕获
    // （一 ref 多层的多目标事务只覆盖首层会造成"半撤销"）—— 多目标走
    // journalOnly（诚实可逆性元数据：不可整单撤销就不入 undo 栈）。
    if (input.respectLock !== false) {
      const after = {
        ...(visible != null ? { visible: Boolean(visible) } : {}),
        ...(opacity != null ? { opacity: Number(opacity) } : {}),
      };
      if (targetIds.length === 1) {
        presentationCommand(
          `Agent 调整 ${targetIds[0]} 显示状态`,
          targetIds[0],
          'agent',
          undoBefore,
          after,
        );
      } else {
        journalOnly({
          type: 'toggle',
          label: `Agent 批量调整 ${targetIds.length} 层显示状态`,
          detail: `图层: ${targetIds.slice(0, 3).join(', ')}${targetIds.length > 3 ? ' …' : ''}`,
          actor: 'agent',
        });
      }
    }
  }

  // 6. postcondition：读回验证（只对本次请求要改的属性比对）
  if (matched.length === 0) {
    // Store-only：runtime reconcile 所有 → 诚实 store_updated（后端视作
    // 未收敛，observation 循环续证）。
    return {
      status: 'succeeded',
      result: withLocked({ store_updated: true, target_ids: targetIds }),
    };
  }
  const want = wantVisibility(visible);
  for (const id of matched) {
    if (!map.getLayer?.(id)) {
      return {
        status: storeMatched.length > 0 ? 'succeeded' : 'failed',
        error: storeMatched.length > 0 ? undefined : 'mutation_failed',
        result: withLocked(
          storeMatched.length > 0 ? { store_updated: true, target_ids: targetIds } : undefined,
        ),
      };
    }
    if (
      want !== undefined &&
      map.getLayoutProperty?.(id, 'visibility') !== want
    ) {
      return {
        status: storeMatched.length > 0 ? 'succeeded' : 'failed',
        error: storeMatched.length > 0 ? undefined : 'mutation_failed',
        result: withLocked(
          storeMatched.length > 0 ? { store_updated: true, target_ids: targetIds } : undefined,
        ),
      };
    }
  }
  return {
    status: 'succeeded',
    result: withLocked({ confirmed: true, target_ids: targetIds }),
  };
}

/**
 * bounded repair（finalize 的 store-owned 兜底）：等一个短周期后重验一次
 * （reconcile 去抖内），仍不一致则再应用一次期望值——最多一次，绝不循环。
 * 返回最终验证结果（供延迟 ack）。
 */
export function boundedVisibilityRepair(
  ctx: MapCommandContext,
  targets: { layerId: string; visible: boolean }[],
  timeoutMs = 400,
): Promise<{ confirmed: string[]; unresolved: string[] }> {
  return new Promise((resolve) => {
    setTimeout(() => {
      // 定时器回调内所有 map 访问有界包裹——面板卸载/地图释放后不产生
      // uncaught error（review P3）。
      try {
        const confirmed: string[] = [];
        const unresolved: string[] = [];
        for (const { layerId, visible } of targets) {
          const want = visible ? 'visible' : 'none';
          const matched = matchMapLayers(ctx.map, layerId);
          let ok = matched.length > 0;
          for (const id of matched) {
            if (!ctx.map.getLayer?.(id) || ctx.map.getLayoutProperty?.(id, 'visibility') !== want) {
              // 一次有界修复：仍存在的图层重应用期望值（重验在 ack 之外，
              // observation 循环最终裁决）。
              try {
                renderer.updateLayerStyle(ctx.map, id, { visibility: want });
              } catch (err) {
                devOnly.warn('[visibility-transaction] bounded repair failed:', err);
              }
              ok = ctx.map.getLayoutProperty?.(id, 'visibility') === want;
            }
          }
          (ok ? confirmed : unresolved).push(layerId);
        }
        resolve({ confirmed, unresolved });
      } catch (err) {
        devOnly.warn('[visibility-transaction] bounded repair crashed:', err);
        resolve({ confirmed: [], unresolved: targets.map((t) => t.layerId) });
      }
    }, timeoutMs);
  });
}
