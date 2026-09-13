/**
 * Source Diff — 图层数据增量更新决策（V11 W3.5，ADR-0163）。
 *
 * 现状（审计 F31 的延伸）：``renderer.ts`` 只做**引用相等**跳过 —— 内容
 * 相同但引用不同（服务端重发、会话恢复、状态回放）的集合仍然全量
 * ``setData``（50k 要素 ~100ms jank）。本模块提供确定性 diff：
 *
 * - **unchanged**：内容逐要素相同 → 调用方跳过 setData（引用不同也跳）；
 * - **incremental**：变更率 ≤ ``INCREMENTAL_CHURN_THRESHOLD`` → 调用方走
 *   增量通道（``source.updateData`` 支持时按 add/update/remove 应用；
 *   不支持时回退全量，diff 记录仍有效）；
 * - **full_setdata**：大改（新数据/高替换率）→ 全量 setData。
 *
 * 纯函数、确定性：要素身份 = ``feature.id``（无 id 的要素按
 * ``index`` 兜底），内容签名 = 规范化 JSON（key 序归一）。
 */

/** 增量通道的替换率上界：超过即回退全量（经验值，测试锁定）。 */
export const INCREMENTAL_CHURN_THRESHOLD = 0.3;

interface FeatureLike {
  id?: string | number;
  geometry?: unknown;
  properties?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface FeatureCollectionLike {
  type: 'FeatureCollection';
  features: FeatureLike[];
}

export type DiffStrategy = 'unchanged' | 'incremental' | 'full_setdata';

export interface SourceDiff {
  /** 仅存在于 next 的要素（按 next 序）。 */
  added: FeatureLike[];
  /** 两边都有但内容变化的要素（按 next 序）。 */
  updated: FeatureLike[];
  /** 仅存在于 prev 的要素 id（按 prev 序）。 */
  removedIds: Array<string | number>;
  /** 内容完全相同的要素数。 */
  unchangedCount: number;
  /** (added+updated+removed) / max(prevN, nextN)。 */
  churnRatio: number;
  strategy: DiffStrategy;
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      out[k] = sortKeys((value as Record<string, unknown>)[k]);
    }
    return out;
  }
  return value === undefined ? null : value;
}

function canonical(feature: FeatureLike): string {
  return JSON.stringify(sortKeys(feature));
}

/**
 * 前后两个 FeatureCollection 的确定性 diff（同输入恒同输出）。
 */
export function diffFeatureCollection(
  prev: FeatureCollectionLike,
  next: FeatureCollectionLike,
): SourceDiff {
  const prevFeats = prev?.features ?? [];
  const nextFeats = next?.features ?? [];

  const prevMap = new Map<string, { feature: FeatureLike; sig: string }>();
  prevFeats.forEach((f, i) => {
    const key = String(f.id ?? `__idx_${i}`);
    prevMap.set(key, { feature: f, sig: canonical(f) });
  });

  const added: FeatureLike[] = [];
  const updated: FeatureLike[] = [];
  const seenKeys = new Set<string>();
  let unchangedCount = 0;

  nextFeats.forEach((f, i) => {
    const key = String(f.id ?? `__idx_${i}`);
    seenKeys.add(key);
    const sig = canonical(f);
    const before = prevMap.get(key);
    if (!before) {
      added.push(f);
    } else if (before.sig !== sig) {
      updated.push(f);
    } else {
      unchangedCount += 1;
    }
  });

  const removedIds: Array<string | number> = [];
  prevFeats.forEach((f, i) => {
    const key = String(f.id ?? `__idx_${i}`);
    if (!seenKeys.has(key)) removedIds.push(f.id ?? key);
  });

  const churn = added.length + updated.length + removedIds.length;
  const denom = Math.max(prevFeats.length, nextFeats.length);
  const churnRatio = denom ? churn / denom : 0;

  let strategy: DiffStrategy;
  if (churn === 0) {
    strategy = 'unchanged';
  } else if (churnRatio <= INCREMENTAL_CHURN_THRESHOLD) {
    strategy = 'incremental';
  } else {
    strategy = 'full_setdata';
  }

  return { added, updated, removedIds, unchangedCount, churnRatio, strategy };
}
