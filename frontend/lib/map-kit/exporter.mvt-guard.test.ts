import { describe, it, expect, vi, beforeEach } from 'vitest';

const hydrateMock = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('@/lib/store/layer-data', async (importOriginal) => {
  const actual: any = await importOriginal();
  return { ...actual, hydrateMvtLayers: hydrateMock };
});
vi.mock('@/lib/store/useHudStore', () => {
  const layers: any[] = [];
  return {
    useHudStore: {
      getState: () => ({ layers }),
      setState: () => {},
    },
  };
});

// Avoid importing heavy mapspec compiler and svg layout — mock them
vi.mock('../mapspec-compiler/mapspec-to-svg', () => ({
  compileMapSpecToSvg: vi.fn(() => '<svg><g class="mapspec-vector-layers"><circle cx="10" cy="10" r="5" fill="#ff0000"/></g></svg>'),
}));
vi.mock('./svg-marginalia', () => ({
  renderSvgPrintLayout: vi.fn(() => '<svg><!-- MAP_CONTENT_HERE --></svg>'),
}));

import { useHudStore } from '@/lib/store/useHudStore';

function mvtLayer(id = 'ref:big-1', hasFeatures: boolean) {
  return {
    id,
    name: 'Big',
    type: 'vector' as const,
    visible: true,
    opacity: 1,
    source: hasFeatures
      ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} }] }
      : { type: 'FeatureCollection', features: [] },
    _refId: id,
    _tileUrl: `http://localhost:8000/api/v1/layers/data/${id}/tiles/{z}/{x}/{y}.mvt?session_id=sid`,
    _descriptor: {
      ref_id: id,
      feature_count: 10000,
      point_count: 10000,
      geometry_types: ['Point'],
      bbox: [0, 0, 1, 1],
      mvt_capable: true,
      estimated_bytes: 1000000,
      content_hash: null,
    },
  };
}

describe('vector export guard on MVT layers (#668)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('generateMapSpecVectorSvgString fails loudly when MVT layer still has no features after hydration', async () => {
    const layer = mvtLayer('ref:big-1', false); // empty features
    // make useHudStore return the empty layer
    (useHudStore.getState as any) = () => ({ layers: [layer] });
    // hydrate resolves but does not populate features (simulates failed hydration)
    hydrateMock.mockResolvedValueOnce(undefined);

    const { generateMapSpecVectorSvgString } = await import('./exporter');
    const mapspec: any = {
      version: '1.0',
      sources: { 'ref:big-1': { type: 'vector', tiles: [layer._tileUrl] } },
      layers: [{ id: 'ref:big-1__point', source: 'ref:big-1', type: 'circle' }],
    };

    await expect(generateMapSpecVectorSvgString(mapspec, { width: 200, height: 100 })).rejects.toThrow(/feature|hydrat|MVT/i);
  });

  it('succeeds when MVT layer has features after hydration', async () => {
    const layer = mvtLayer('ref:big-1', true);
    (useHudStore.getState as any) = () => ({ layers: [layer] });
    hydrateMock.mockResolvedValueOnce(undefined);
    const { generateMapSpecVectorSvgString } = await import('./exporter');
    const mapspec: any = {
      version: '1.0',
      sources: { 'ref:big-1': { type: 'vector', tiles: [layer._tileUrl] } },
      layers: [{ id: 'ref:big-1__point', source: 'ref:big-1', type: 'circle' }],
    };
    const svg = await generateMapSpecVectorSvgString(mapspec, { width: 200, height: 100 });
    expect(svg).toContain('<svg');
  });

  it('hydration rejection also fails loudly', async () => {
    const layer = mvtLayer('ref:big-1', false);
    (useHudStore.getState as any) = () => ({ layers: [layer] });
    hydrateMock.mockRejectedValueOnce(new Error('network'));
    const { generateMapSpecVectorSvgString } = await import('./exporter');
    const mapspec: any = { version: '1.0', sources: { 'ref:big-1': { type: 'vector', tiles: [layer._tileUrl] } }, layers: [] };
    await expect(generateMapSpecVectorSvgString(mapspec, {})).rejects.toThrow();
  });
});
