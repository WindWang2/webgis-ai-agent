/**
 * applySourcePatch — 把 source-diff 的 'incremental' 通道真正接到
 * MapLibre v5 ``GeoJSONSource.updateData``（extreme-scale v2）。
 *
 * 背景：source-diff.ts（ADR-0163）已确定性产出 added/updated/removedIds，
 * 但 renderer 只消费 'unchanged' 判定 —— 引用不同、内容小变的集合仍整包
 * setData（50k 要素层 ~100ms jank）。本模块封住最后一公里，回退链：
 *
 *   unchanged            → 跳过（引用升级，不触发重解析）
 *   incremental
 *     + updateData 可用
 *     + prev/next 全员稳定 id（updateData 契约：source 必须唯一 id）
 *     + 规模 ≤ maxDiffFeatures
 *                        → source.updateData({remove, add, update})
 *   其余                 → 整包 setData（fail-safe，绝不丢要素）
 *
 * 决策理由（reason）是封闭词表，供观测账本披露 —— 不诚实裁决不落账。
 */

import {
  diffFeatureCollection,
  INCREMENTAL_CHURN_THRESHOLD,
} from '@/lib/mapspec-runtime/source-diff';
import type { FeatureCollectionLike } from '@/lib/mapspec-runtime/source-diff';

/** diff 的规模上界（与 renderer 的 SOURCE_DIFF_MAX_FEATURES 同值契约）。 */
export const PATCH_DIFF_MAX_FEATURES = 2000;

/** MapLibre GeoJSONSourceDiff 的特征级更新载荷（结构子集，避免类型依赖）。 */
export interface FeatureDiffUpdate {
  id: string | number;
  newGeometry?: GeoJSON.Geometry | null;
  removeAllProperties?: boolean;
  addOrUpdateProperties?: Array<{ key: string; value: unknown }>;
}

export interface GeoJsonSourcePatchTarget {
  setData(data: unknown): unknown;
  updateData?(diff: {
    removeAll?: boolean;
    remove?: Array<string | number>;
    add?: unknown[];
    update?: FeatureDiffUpdate[];
  }): unknown;
}

export type PatchOp = 'none' | 'unchanged' | 'updateData' | 'setData';

export interface PatchApplication {
  op: PatchOp;
  added: number;
  updated: number;
  removed: number;
  reason:
    | 'initial'
    | 'same-reference'
    | 'content-identical'
    | 'applied'
    | 'unstable-identity'
    | 'updateData-unsupported'
    | 'churn-above-threshold'
    | 'diff-cap'
    | 'major-change';
}

function hasStableIds(prev: FeatureCollectionLike, next: FeatureCollectionLike): boolean {
  const check = (fc: FeatureCollectionLike) =>
    (fc.features ?? []).every(
      (f) => f.id !== undefined && f.id !== null && String(f.id).length > 0,
    );
  return check(prev) && check(next);
}

/**
 * 对一个已挂载的 GeoJSON source 应用 next 数据，自动选择增量或整包。
 * 幂等：同输入恒同操作序列。不触碰 renderer 的账本（调用方负责）。
 */
export function applySourcePatch(
  source: GeoJsonSourcePatchTarget,
  prev: FeatureCollectionLike | undefined,
  next: FeatureCollectionLike,
  opts: { maxDiffFeatures?: number } = {},
): PatchApplication {
  const cap = opts.maxDiffFeatures ?? PATCH_DIFF_MAX_FEATURES;

  if (!prev) {
    source.setData(next);
    return { op: 'setData', added: 0, updated: 0, removed: 0, reason: 'initial' };
  }
  if (prev === (next as unknown)) {
    return { op: 'unchanged', added: 0, updated: 0, removed: 0, reason: 'same-reference' };
  }

  const prevN = prev.features?.length ?? 0;
  const nextN = next.features?.length ?? 0;
  if (prevN > cap || nextN > cap) {
    source.setData(next);
    return { op: 'setData', added: 0, updated: 0, removed: 0, reason: 'diff-cap' };
  }

  const diff = diffFeatureCollection(prev, next);
  if (diff.strategy === 'unchanged') {
    return {
      op: 'unchanged',
      added: 0,
      updated: 0,
      removed: 0,
      reason: 'content-identical',
    };
  }

  if (diff.strategy !== 'incremental') {
    source.setData(next);
    return {
      op: 'setData',
      added: diff.added.length,
      updated: diff.updated.length,
      removed: diff.removedIds.length,
      reason: 'churn-above-threshold',
    };
  }

  if (typeof source.updateData !== 'function') {
    source.setData(next);
    return {
      op: 'setData',
      added: diff.added.length,
      updated: diff.updated.length,
      removed: diff.removedIds.length,
      reason: 'updateData-unsupported',
    };
  }

  if (!hasStableIds(prev, next)) {
    // updateData 契约要求 source 全员唯一 id —— 任一无 id 要素都让增量
    // 语义不可信（index 兜底身份在增删后漂移），回退整包，绝不丢/重要素。
    source.setData(next);
    return {
      op: 'setData',
      added: diff.added.length,
      updated: diff.updated.length,
      removed: diff.removedIds.length,
      reason: 'unstable-identity',
    };
  }

  const updates: FeatureDiffUpdate[] = diff.updated.map((f) => ({
    id: f.id as string | number,
    newGeometry: (f.geometry ?? null) as GeoJSON.Geometry | null,
    removeAllProperties: true,
    addOrUpdateProperties: Object.entries(f.properties ?? {}).map(([key, value]) => ({
      key,
      value,
    })),
  }));

  source.updateData({
    remove: diff.removedIds as Array<string | number>,
    add: diff.added,
    update: updates,
  });

  return {
    op: 'updateData',
    added: diff.added.length,
    updated: diff.updated.length,
    removed: diff.removedIds.length,
    reason: 'applied',
  };
}

// 语义护栏：INCREMENTAL_CHURN_THRESHOLD 是 source-diff 的权威阈值，
// 这里仅引用以保持同源（防止两处阈值漂移）。
void INCREMENTAL_CHURN_THRESHOLD;
