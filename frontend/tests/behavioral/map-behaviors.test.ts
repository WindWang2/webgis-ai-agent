/**
 * Quality V3（Epic 10 W14）— map layer lifecycle & MapSpec reconcile
 * behavioral evidence.
 *
 * 跨模块行为序列（不是单元测试的重复）：以「用户改图」的完整生命周期为
 * 单位驱动 —— 初始加载 → 增层 → 改样式 → 重排 → 删层 → 删源，断言
 * reconciler 产出补丁序列与 live-spec 的提交语义。每个 describe 挂
 * `@behavior:<id>` 标签，由 scripts/gen_frontend_behavior.py 聚合为
 * docs/integration/frontend-behavior.json（字节闸）。
 */
import { describe, it, expect } from 'vitest';
import { diffSpecs } from '../../lib/mapspec-compiler/reconciler';
import { MapSpec } from '../../lib/mapspec-compiler/types';

function specWith(layers: MapSpec['layers'], sources: MapSpec['sources']): MapSpec {
  return { version: '1.0', sources, layers } as MapSpec;
}

const geojsonSource = (features: unknown[] = []) => ({
  type: 'geojson' as const,
  inlineData: { type: 'FeatureCollection' as const, features },
});

describe('@behavior:map-layer-lifecycle add → style change → remove (full sequence)', () => {
  it('first application reports every layer/source as add', () => {
    const base = specWith(
      [{ id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } }],
      { s1: geojsonSource() },
    );
    const patch = diffSpecs(null, base);
    expect(patch.layers.map((l) => l.kind)).toEqual(['add']);
    expect(patch.sources.map((s) => s.kind)).toEqual(['add']);
  });

  it('style mutation emits update only for the touched layer', () => {
    const prev = specWith(
      [
        { id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } },
        { id: 'l2', source: 's1', type: 'circle', paint: { color: '#0f0', radius: 3 } },
      ],
      { s1: geojsonSource() },
    );
    const next = specWith(
      [
        { id: 'l1', source: 's1', type: 'circle', paint: { color: '#00f', radius: 5 } },
        { id: 'l2', source: 's1', type: 'circle', paint: { color: '#0f0', radius: 3 } },
      ],
      { s1: geojsonSource() },
    );
    const patch = diffSpecs(prev, next);
    expect(patch.layers).toHaveLength(1);
    // 契约（reconciler.ts）：paint 变化 = recompile（层重建），非就地 update
    expect(patch.layers[0]).toMatchObject({ id: 'l1', kind: 'recompile' });
    expect(patch.sources).toHaveLength(0);
  });

  it('layer removal emits remove and keeps siblings untouched', () => {
    const prev = specWith(
      [
        { id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } },
        { id: 'l2', source: 's1', type: 'circle', paint: { color: '#0f0', radius: 3 } },
      ],
      { s1: geojsonSource() },
    );
    const next = specWith(
      [{ id: 'l2', source: 's1', type: 'circle', paint: { color: '#0f0', radius: 3 } }],
      { s1: geojsonSource() },
    );
    const patch = diffSpecs(prev, next);
    expect(patch.layers).toEqual([{ id: 'l1', kind: 'remove' }]);
  });

  it('source removal cascades: removed source reported, dependent layers removed', () => {
    const prev = specWith(
      [{ id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } }],
      { s1: geojsonSource() },
    );
    const next = specWith([], {});
    const patch = diffSpecs(prev, next);
    expect(patch.sources.map((s) => ({ id: s.id, kind: s.kind })))
      .toEqual([{ id: 's1', kind: 'remove' }]);
    expect(patch.layers).toEqual([{ id: 'l1', kind: 'remove' }]);
  });
});

describe('@behavior:mapspec-reconcile z-order and data mutations are patch-minimal', () => {
  it('reordering layers emits reorder-aware change set without touching sources', () => {
    const mk = (ids: string[]) => specWith(
      ids.map((id) => ({ id, source: 's1', type: 'circle', paint: { color: '#f00', radius: 4 } })),
      { s1: geojsonSource() },
    );
    const patch = diffSpecs(mk(['a', 'b']), mk(['b', 'a']));
    // 每个存活层都因 z 序变化需要 update（顺序敏感的确定性应用）
    expect(patch.sources).toHaveLength(0);
    expect(patch.layers.every((l) => l.kind === 'update')).toBe(true);
  });

  it('data mutation (source feature change) emits source update with next payload', () => {
    const prev = specWith(
      [{ id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } }],
      { s1: geojsonSource([]) },
    );
    const feature = {
      type: 'Feature',
      properties: { id: 1 },
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
    };
    const next = specWith(
      [{ id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } }],
      { s1: geojsonSource([feature]) },
    );
    const patch = diffSpecs(prev, next);
    expect(patch.sources).toHaveLength(1);
    expect(patch.sources[0].kind).toBe('update');
    // 源数据变化带动依赖层 recompile（层由源驱动，契约如此）
    expect(patch.layers).toEqual([
      expect.objectContaining({ id: 'l1', kind: 'recompile' }),
    ]);
  });

  it('identical specs produce an empty patch (no MapLibre churn)', () => {
    const spec = specWith(
      [{ id: 'l1', source: 's1', type: 'circle', paint: { color: '#f00', radius: 5 } }],
      { s1: geojsonSource() },
    );
    const patch = diffSpecs(spec, spec);
    expect(patch.sources).toHaveLength(0);
    expect(patch.layers).toHaveLength(0);
  });
});
