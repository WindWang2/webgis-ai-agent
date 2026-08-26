import * as renderer from '@/lib/map-kit/renderer';
import { devOnly } from '@/lib/utils/logger';
import {
  clearPendingPresentation,
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  mergePendingPresentation,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { ApiError, apiFetch } from '@/lib/api/transport';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
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
 *   resolve identity → desired（HUD store + pending presentation）
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
}

export interface VisibilityTransactionResult extends MapCommandResult {
  result?: {
    confirmed?: boolean;
    store_updated?: boolean;
    target_ids?: string[];
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
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    if (data.mapspec) commitMapSpecDocument(data.mapspec);
    clearPendingPresentation(specLayerId);
    return 'committed';
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) {
      devOnly.warn('[visibility-transaction] durability commit failed:', err);
      clearPendingPresentation(specLayerId);
      return 'lost';
    }
    // superseded：收敛 revision + 服务端真相；若真相已含期望值（并发同值
    // 写）→ 完成；否则调用方带新 revision 重试一次。
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    if (superseded.mapspec) commitMapSpecDocument(superseded.mapspec);
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
  if (first !== 'retry') return first as 'committed' | 'reflected' | 'lost';
  const second = await postPresentationOnce(specLayerId, patch);
  if (second !== 'lost') return second as 'committed' | 'reflected' | 'lost';
  // 重试仍失败：重新落 pending —— reconcile 继续表达本地期望真相，
  // 不静默丢决策（服务端偏差由下一次用户/agent 突变或修复循环收敛）。
  mergePendingPresentation(specLayerId, patch);
  return 'lost';
}

// 全局串行队列：跨事务（finalize 的 N 层 + 逐层命令 + 用户 toggle）也逐笔
// 顺序提交——每笔从游标读到前一笔推进后的 revision。
let durabilityChain: Promise<void> = Promise.resolve();

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
  durabilityChain = durabilityChain
    .then(async () => {
      for (const { specLayerId } of targets) {
        await postPresentationWithRetry(specLayerId, patch);
      }
    })
    .catch((err) => {
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
  const targetIds = resolveLayerTargetsByRef(layerId, getHudState);
  if (targetIds.length === 0) {
    return { status: 'failed', error: 'target_not_found' };
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
  }

  // 6. postcondition：读回验证（只对本次请求要改的属性比对）
  if (matched.length === 0) {
    // Store-only：runtime reconcile 所有 → 诚实 store_updated（后端视作
    // 未收敛，observation 循环续证）。
    return {
      status: 'succeeded',
      result: { store_updated: true, target_ids: targetIds },
    };
  }
  const want = wantVisibility(visible);
  for (const id of matched) {
    if (!map.getLayer?.(id)) {
      return {
        status: storeMatched.length > 0 ? 'succeeded' : 'failed',
        error: storeMatched.length > 0 ? undefined : 'mutation_failed',
        result: storeMatched.length > 0 ? { store_updated: true, target_ids: targetIds } : undefined,
      };
    }
    if (
      want !== undefined &&
      map.getLayoutProperty?.(id, 'visibility') !== want
    ) {
      return {
        status: storeMatched.length > 0 ? 'succeeded' : 'failed',
        error: storeMatched.length > 0 ? undefined : 'mutation_failed',
        result: storeMatched.length > 0 ? { store_updated: true, target_ids: targetIds } : undefined,
      };
    }
  }
  return {
    status: 'succeeded',
    result: { confirmed: true, target_ids: targetIds },
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
