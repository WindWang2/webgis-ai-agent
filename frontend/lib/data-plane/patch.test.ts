import { describe, it, expect, vi } from 'vitest';

import { applySourcePatch } from '@/lib/data-plane/patch';
import type { GeoJsonSourcePatchTarget } from '@/lib/data-plane/patch';

/**
 * applySourcePatch 契约（extreme-scale v2 / milestone M3-incremental）：
 *
 * source-diff（ADR-0163）已产出 added/updated/removedIds，但 renderer 只
 * 消费 'unchanged' —— 本模块把 'incremental' 通道接到 MapLibre v5 的
 * ``GeoJSONSource.updateData``，且回退链安全：
 *   unchanged → 跳过；incremental + 全员稳定 id + updateData 可用 → 增量；
 *   其余（无 id / 超 diff 上限 / 不支持 updateData / 大改）→ 整包 setData。
 */

function feat(id: number, x = 116): GeoJSON.Feature {
  return {
    type: 'Feature',
    id,
    geometry: { type: 'Point', coordinates: [x, 39.9] },
    properties: { id },
  };
}

function fc(...feats: GeoJSON.Feature[]) {
  return { type: 'FeatureCollection' as const, features: feats };
}

function makeSource(supportsUpdateData = true): GeoJsonSourcePatchTarget & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    setData: vi.fn((d: unknown) => {
      calls.push('setData');
      return d;
    }),
    ...(supportsUpdateData
      ? {
          updateData: vi.fn((diff: unknown) => {
            calls.push('updateData');
            return diff;
          }),
        }
      : {}),
  };
}

describe('applySourcePatch — decision matrix', () => {
  it('no previous data → full setData (initial mount)', () => {
    const src = makeSource();
    const app = applySourcePatch(src, undefined, fc(feat(1)));
    expect(app.op).toBe('setData');
    expect(app.reason).toBe('initial');
    expect(src.calls).toEqual(['setData']);
  });

  it('same reference → unchanged, nothing called', () => {
    const src = makeSource();
    const data = fc(feat(1));
    const app = applySourcePatch(src, data, data);
    expect(app.op).toBe('unchanged');
    expect(src.calls).toEqual([]);
  });

  it('identical content, different reference → unchanged (content diff)', () => {
    const src = makeSource();
    const app = applySourcePatch(src, fc(feat(1)), fc(feat(1)));
    expect(app.op).toBe('unchanged');
    expect(src.calls).toEqual([]);
  });

  it('small churn with stable ids → updateData with remove/add/update', () => {
    const src = makeSource();
    // 20 要素集动 3 个（churn 0.15 ≤ 0.3 阈值）——2 要素集任何变更都会
    // 超阈值走整包（那是 ADR-0163 的正确语义，不是本模块的回退）。
    const base = Array.from({ length: 20 }, (_, i) => feat(i));
    const prev = fc(...base);
    const next = fc(
      ...base.map((f) => (f.id === 2 ? feat(2, 117) : f)).filter((f) => f.id !== 3),
      feat(100),
    );
    const app = applySourcePatch(src, prev, next);
    expect(app.op).toBe('updateData');
    expect(app.added).toBe(1);
    expect(app.updated).toBe(1);
    expect(app.removed).toBe(1);
    expect(src.calls).toEqual(['updateData']);
    const diff = (src.updateData as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      remove?: Array<string | number>;
      add?: unknown[];
      update?: Array<{ id: string | number; removeAllProperties?: boolean }>;
    };
    expect(diff.remove).toEqual([3]);
    expect(diff.add).toHaveLength(1);
    expect(diff.update?.[0].id).toBe(2);
    expect(diff.update?.[0].removeAllProperties).toBe(true);
  });

  it('feature without id anywhere → full setData (unstable identity)', () => {
    const src = makeSource();
    const noId = { type: 'Feature', geometry: feat(9).geometry, properties: {} } as GeoJSON.Feature;
    const base = Array.from({ length: 20 }, (_, i) => feat(i + 10));
    const prev = fc(...base, noId);
    const next = fc(...base, noId, feat(200));
    const app = applySourcePatch(src, prev, next);
    expect(app.op).toBe('setData');
    expect(app.reason).toBe('unstable-identity');
    expect(src.calls).toEqual(['setData']);
  });

  it('source without updateData support → full setData', () => {
    const src = makeSource(false);
    const base = Array.from({ length: 20 }, (_, i) => feat(i));
    const app = applySourcePatch(src, fc(...base), fc(...base, feat(50)));
    expect(app.op).toBe('setData');
    expect(app.reason).toBe('updateData-unsupported');
    expect(src.calls).toEqual(['setData']);
  });

  it('high churn (> threshold) → full setData (diff 高替换率语义沿用 ADR-0163)', () => {
    const src = makeSource();
    const prev = fc(...Array.from({ length: 10 }, (_, i) => feat(i)));
    const next = fc(...Array.from({ length: 10 }, (_, i) => feat(i + 100)));
    const app = applySourcePatch(src, prev, next);
    expect(app.op).toBe('setData');
    expect(app.reason).toBe('churn-above-threshold');
  });

  it('over maxDiffFeatures → full setData without diffing cost blowup', () => {
    const src = makeSource();
    const big = Array.from({ length: 3000 }, (_, i) => feat(i));
    const app = applySourcePatch(src, fc(...big), fc(...big.slice(1)));
    expect(app.op).toBe('setData');
    expect(app.reason).toBe('diff-cap');
  });

  it('null geometry features are diff-safe (no crash, stable path)', () => {
    const src = makeSource();
    const nullGeom = { type: 'Feature', id: 7, geometry: null, properties: {} } as GeoJSON.Feature;
    const app = applySourcePatch(src, fc(nullGeom), fc(nullGeom, feat(8)));
    expect(['updateData', 'setData']).toContain(app.op);
  });
});
