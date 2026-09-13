/**
 * Source Diff 测试（V11 W3.5，ADR-0163）：确定性 diff + 策略决策 +
 * renderer unchanged 跳过语义。
 */
import { describe, it, expect } from 'vitest';
import {
  diffFeatureCollection,
  INCREMENTAL_CHURN_THRESHOLD,
  type FeatureCollectionLike,
} from './source-diff';

function fc(features: Array<{ id: number; properties: Record<string, unknown> }>): FeatureCollectionLike {
  return {
    type: 'FeatureCollection',
    features: features.map((f) => ({
      type: 'Feature',
      id: f.id,
      properties: f.properties,
      geometry: { type: 'Point', coordinates: [116, 39] },
    })),
  };
}

describe('source diff（W3.5 增量更新决策）', () => {
  it('内容相同但引用不同 → unchanged（引用升级、跳过 setData）', () => {
    const a = fc([{ id: 1, properties: { v: 1 } }, { id: 2, properties: { v: 2 } }]);
    const b = fc([{ id: 1, properties: { v: 1 } }, { id: 2, properties: { v: 2 } }]);
    expect(a).not.toBe(b);
    const diff = diffFeatureCollection(a, b);
    expect(diff.strategy).toBe('unchanged');
    expect(diff.churnRatio).toBe(0);
  });

  it('key 序不同的同内容要素 → unchanged（签名规范化）', () => {
    const a: FeatureCollectionLike = {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', id: 1,
        properties: { a: 1, b: 2 },
        geometry: { type: 'Point', coordinates: [116, 39] },
      }],
    };
    const b: FeatureCollectionLike = {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', id: 1,
        properties: { b: 2, a: 1 },
        geometry: { type: 'Point', coordinates: [116, 39] },
      }],
    };
    expect(diffFeatureCollection(a, b).strategy).toBe('unchanged');
  });

  it('低替换率 → incremental（add/update/remove 分档清晰）', () => {
    // 10 要素改 2（1 更新 + 1 增）→ churn 0.2 ≤ 0.3 阈值
    const prev = fc(Array.from({ length: 10 }, (_, i) => ({ id: i + 1, properties: { v: i + 1 } })));
    const nextFeatures = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, properties: { v: i + 1 } }));
    nextFeatures[1] = { id: 2, properties: { v: 22 } };      // updated
    nextFeatures.push({ id: 11, properties: { v: 11 } });    // added
    const next = fc(nextFeatures.map((f) => ({ id: f.id, properties: f.properties })));
    const diff = diffFeatureCollection(prev, next);
    expect(diff.strategy).toBe('incremental');
    expect(diff.added.map((f) => f.id)).toEqual([11]);
    expect(diff.updated.map((f) => f.id)).toEqual([2]);
    expect(diff.unchangedCount).toBe(9);
    expect(diff.churnRatio).toBeCloseTo(2 / 11, 4);
    expect(diff.churnRatio).toBeLessThanOrEqual(INCREMENTAL_CHURN_THRESHOLD);
  });

  it('高替换率 → full_setdata', () => {
    const prev = fc([
      { id: 1, properties: { v: 1 } },
      { id: 2, properties: { v: 2 } },
    ]);
    const next = fc([
      { id: 3, properties: { v: 3 } },
      { id: 4, properties: { v: 4 } },
    ]);
    expect(diffFeatureCollection(prev, next).strategy).toBe('full_setdata');
  });

  it('确定性：同输入两次 diff 相等', () => {
    const prev = fc([{ id: 1, properties: { v: 1 } }]);
    const next = fc([{ id: 1, properties: { v: 2 } }]);
    expect(diffFeatureCollection(prev, next)).toEqual(diffFeatureCollection(prev, next));
  });
});
