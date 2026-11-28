import { describe, expect, it } from 'vitest';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { Layer } from '@/lib/types/layer';
import { composeLiveMapSpec, hasUsableHudRefPayload } from './live-spec';

function hudLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'L1',
    name: 'Schools',
    type: 'vector',
    visible: true,
    opacity: 1,
    group: 'analysis',
    source: {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [0, 0] } }],
    } as any,
    ...overrides,
  };
}

function committedSpec(): MapSpec {
  return {
    version: '1.0',
    sources: {
      L1: { type: 'geojson', url: 'ref:geojson-abc' },
    },
    layers: [
      {
        id: 'L1',
        source: 'L1',
        type: 'circle',
        layout: { visibility: 'visible' },
        paint: { 'circle-color': '#00f', opacity: 1 },
      },
    ],
  };
}

describe('composeLiveMapSpec', () => {
  it('keeps committed visibility when HUD hides a layer without a pending mutation', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      { layers: [hudLayer({ visible: false })], processLayers: {}, activeFilters: {}, is3D: false },
      {},
    );
    expect(spec.layers[0].layout?.visibility).toBe('visible');
  });

  it('applies a pending visibility overlay onto the committed MapSpec', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      { layers: [hudLayer({ visible: false })], processLayers: {}, activeFilters: {}, is3D: false },
      { L1: { visible: false } },
    );
    expect(spec.layers[0].layout?.visibility).toBe('none');
  });

  it('injects HUD source payloads so the live map can still render refs', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      { layers: [hudLayer()], processLayers: {}, activeFilters: {}, is3D: false },
      {},
    );
    const source = spec.sources.L1 as { type: string; url?: string; inlineData?: { features?: unknown[] } };
    expect(source.inlineData?.features).toHaveLength(1);
    expect(source.url).toBeUndefined();
  });

  it('maps a HUD runtime id onto the committed source via _mapspecLayerId', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      {
        layers: [hudLayer({ id: 'ref:geojson-abc', _mapspecLayerId: 'L1' })],
        processLayers: {},
        activeFilters: {},
        is3D: false,
      },
      {},
    );
    const source = spec.sources.L1 as { type: string; url?: string; inlineData?: { features?: unknown[] } };
    expect(source.inlineData?.features).toHaveLength(1);
    expect(source.url).toBeUndefined();
  });

  it('preserves HUD-only analysis layers alongside a committed MapSpec', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      { layers: [hudLayer(), hudLayer({ id: 'HUD_ONLY' })], processLayers: {}, activeFilters: {}, is3D: false },
      {},
    );
    expect(spec.layers.map((layer) => layer.id)).toEqual(['L1', 'HUD_ONLY__point']);
    expect(spec.layers[0].layout?.visibility).toBe('visible');
    expect(spec.layers[1].layout?.visibility).toBe('visible');
  });

  it('shadows a custom- prefixed HUD layer whose committed layer id is the stripped id', () => {
    const committed: MapSpec = {
      version: '1.0',
      sources: { poi: { type: 'geojson', url: 'ref:geojson-poi' } },
      layers: [
        { id: 'poi', source: 'poi', type: 'circle', layout: { visibility: 'visible' } },
      ],
    };
    const spec = composeLiveMapSpec(
      committed,
      { layers: [hudLayer({ id: 'custom-poi' })], processLayers: {}, activeFilters: {}, is3D: false },
      {},
    );
    // custom- 前缀剥离后的别名与 committed 层同源 → 不得再渲染第二份。
    expect(spec.layers.map((layer) => layer.id)).toEqual(['poi']);
  });

  it('drops a pending-removed committed layer from the live spec', () => {
    const spec = composeLiveMapSpec(
      committedSpec(),
      { layers: [], processLayers: {}, activeFilters: {}, is3D: false },
      {},
      ['L1'],
    );
    expect(spec.layers).toEqual([]);
  });
});

describe('hasUsableHudRefPayload', () => {
  it('不把空 FeatureCollection 当成已解析 ref', () => {
    expect(hasUsableHudRefPayload(hudLayer({ source: { type: 'FeatureCollection', features: [] } }))).toBe(false);
    expect(hasUsableHudRefPayload(hudLayer())).toBe(true);
    // Layer['source'] 判别联合未收窄 tiles 模板形态（_tileUrl 是类型化通道）；
    // 运行时 shape 不变，仅类型断言与文件内既有 `as any` 同口径。
    expect(hasUsableHudRefPayload(hudLayer({ type: 'tile', source: { tiles: ['https://tiles.invalid/{z}/{x}/{y}.mvt'] } as unknown as Layer['source'] }))).toBe(true);
  });
});
