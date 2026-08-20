import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/transport', () => ({
  apiFetch: apiFetchMock,
  isApiError: (e: unknown) => e instanceof Error && (e as any).name === 'ApiError',
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

// Import after mocks
import { useHudStore } from './useHudStore';
import { ensureLayerData } from './layer-data';

function makeMVTLayer(id = 'ref:big-1', featureCount = 100_000) {
  return {
    id,
    name: 'Big',
    type: 'vector' as const,
    visible: true,
    opacity: 1,
    source: { type: 'FeatureCollection', features: [] } as any,
    _refId: id,
    _tileUrl: `http://localhost:8000/api/v1/layers/data/${id}/tiles/{z}/{x}/{y}.mvt?session_id=sid-aaa`,
    _descriptor: {
      ref_id: id,
      feature_count: featureCount,
      point_count: featureCount,
      geometry_types: ['Point'],
      bbox: [0, 0, 1, 1],
      mvt_capable: true,
      estimated_bytes: featureCount * 100 + 1024,
      content_hash: null,
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useHudStore.setState({ layers: [] });
  // mock session id source: ensureLayerData will need session id. We expose via global or via layer-data's internal getSessionId.
  // For now, set a global sid used by the seam: it reads from sessionStorage or fallback.
  // We'll mock apiFetch to succeed.
});

describe('ensureLayerData seam (#667)', () => {
  it('exists as single seam', async () => {
    expect(typeof ensureLayerData).toBe('function');
  });

  it('selection-detail does NOT download full FC (uses single-feature endpoint)', async () => {
    const layer = makeMVTLayer();
    useHudStore.getState().addLayer(layer as any);
    // Set selected feature with usable id
    useHudStore.setState({
      selectedFeature: {
        layerId: layer.id,
        point: [0, 0],
        properties: { id: 'feat-42', name: 'x' },
        selectedAt: Date.now(),
        featureId: 'feat-42',
      } as any,
    });
    apiFetchMock.mockResolvedValueOnce({
      type: 'Feature',
      id: 'feat-42',
      geometry: { type: 'Point', coordinates: [0, 0] },
      properties: { id: 'feat-42' },
    });

    const result = await ensureLayerData(layer.id, 'selection-detail');

    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const url = apiFetchMock.mock.calls[0][0] as string;
    expect(url).toContain(`/feature/feat-42`);
    expect(url).not.toContain('/layers/data/ref:big-1?session_id'); // not the full FC url
    expect(result).toBeTruthy();
  });

  it('selection-detail without usable id falls back honestly (no full FC fetch)', async () => {
    const layer = makeMVTLayer();
    useHudStore.getState().addLayer(layer as any);
    useHudStore.setState({
      selectedFeature: {
        layerId: layer.id,
        point: [0, 0],
        properties: { name: 'no-id' },
        selectedAt: Date.now(),
        // no featureId, and no id-like prop
      } as any,
    });

    const result: any = await ensureLayerData(layer.id, 'selection-detail');

    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(result?.status).toBe('fallback');
  });

  it('filter / export-vector / attribute-table hydrate full FC on demand and cache', async () => {
    const layer = makeMVTLayer();
    useHudStore.getState().addLayer(layer as any);
    const fc = { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} }] };
    apiFetchMock.mockResolvedValueOnce(fc);

    const _r1 = await ensureLayerData(layer.id, 'filter');
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect((apiFetchMock.mock.calls[0][0] as string)).toContain('/layers/data/');
    expect(useHudStore.getState().layers.find(l => l.id === layer.id)?.source).toEqual(fc);

    // second call cached — no second fetch
    const r2 = await ensureLayerData(layer.id, 'filter');
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(r2).toBeTruthy();
  });

  it('selection-detail with numeric id 0 is treated as usable (not falsy-dropped)', async () => {
    const layer = makeMVTLayer();
    useHudStore.getState().addLayer(layer as any);
    useHudStore.setState({
      selectedFeature: {
        layerId: layer.id,
        point: [0, 0],
        properties: { id: 0, name: 'zero' },
        selectedAt: Date.now(),
        featureId: 0,
      } as any,
    });
    apiFetchMock.mockResolvedValueOnce({
      type: 'Feature',
      id: 0,
      geometry: { type: 'Point', coordinates: [0, 0] },
      properties: { id: 0 },
    });
    const result: any = await ensureLayerData(layer.id, 'selection-detail');
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect((apiFetchMock.mock.calls[0][0] as string)).toContain('/feature/0');
    expect(result.status).toBe('single-feature');
  });

  it('full-hydration reasons share one in-flight key (no double fetch)', async () => {
    const layer = makeMVTLayer();
    useHudStore.getState().addLayer(layer as any);
    let resolveFc!: (v: any) => void;
    apiFetchMock.mockReturnValueOnce(new Promise((res) => { resolveFc = res; }));
    const fc = { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} }] };
    const p1 = ensureLayerData(layer.id, 'filter');
    const p2 = ensureLayerData(layer.id, 'export-vector');
    // second call should reuse the same promise — only one fetch
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    resolveFc(fc);
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1.status).toBe('hydrated');
    expect(r2.status).toBe('hydrated');
    // third call after hydration is cached without fetch
    apiFetchMock.mockClear();
    const r3 = await ensureLayerData(layer.id, 'attribute-table');
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(r3.status).toBe('already-hydrated');
  });
});
