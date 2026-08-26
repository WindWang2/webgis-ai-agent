import * as renderer from '@/lib/map-kit/renderer';
import { devOnly } from '@/lib/utils/logger';
import { mergePendingPresentation } from '@/lib/mapspec/session-cursor';
import { commitLayerPresentation } from '@/lib/mapspec/user-mutation';
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
 *   → durability（后端 MapSpec patch_layer_presentation 提交，CAS；
 *     superseded 时用户最新交互优先——服务端真相回灌）
 *   → postcondition（getLayoutProperty 读回验证）
 *   → evidence（confirmed / store_updated，绝不假成功）。
 *
 * durability 提交是 fire-and-forget：runtime 已生效的突变不因后端提交
 * 失败而回滚；失败保留 pending presentation（下一次 reconcile 再收敛）。
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

async function commitDurability(
  targets: { storeId: string; specLayerId: string }[],
  visible?: boolean | null,
  opacity?: number | null,
): Promise<'committed' | 'pending'> {
  let durability: 'committed' | 'pending' = 'committed';
  // 顺序提交（非并行）：每次 commitLayerPresentation 从游标读
  // expected_revision，首笔成功推进 revision 后，下一笔读到新值——
  // 并行会全部读到同一旧 revision，除首笔外全被 409 拒（多目标丢失）。
  for (const { specLayerId } of targets) {
    try {
      await commitLayerPresentation({
        layerId: specLayerId,
        ...(visible != null ? { visible: Boolean(visible) } : {}),
        ...(opacity != null ? { opacity: Number(opacity) } : {}),
      });
    } catch (err) {
      // runtime 已生效——durability 失败不回滚地图，保留 pending 由
      // 下一次 reconcile/用户操作收敛。
      devOnly.warn('[visibility-transaction] durability commit failed:', err);
      durability = 'pending';
    }
  }
  return durability;
}

/**
 * 应用一次可见性事务。同步返回读回验证后的结果；durability 提交在后台
 * 进行（不阻塞 ack——ack 语义只覆盖可同步验证的 runtime 真相）。
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

  // 5. durability：后端 desired state 提交（fire-and-forget，agent 路径
  //    此前缺失——reload 后 Agent 可见性决策丢失的根因）。
  if (input.durable !== false && (visible != null || opacity != null)) {
    void commitDurability(targetSpecPairs, visible, opacity);
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
    }, timeoutMs);
  });
}
