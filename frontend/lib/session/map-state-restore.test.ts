import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { ApiError } from '@/lib/api/transport';
import { devOnly } from '@/lib/utils/logger';
import { getCommittedMapSpec, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  buildLayerFromRestored,
  restoreSessionMapLayers,
  selectCameraToRestore,
  selectLayersToRestore,
  syncSpecLayersToStore,
} from './map-state-restore';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  useHudStore.getState().clearLayers();
  useHudStore.setState({ baseLayer: 'Carto 深色' });
  useToastStore.setState({ toasts: [] });
  setMapSpecSessionCursor(undefined, 0, null);
  vi.mocked(devOnly.error).mockClear();
});

describe('selectLayersToRestore', () => {
  it('prefers the current-generation cartographic observation', () => {
    const state = {
      _current_cartographic_fingerprint: 'carto-sha256:new',
      _cartographic_observation: {
        mapspec_fingerprint: 'carto-sha256:new',
        layers: [{ id: 'obs' }],
      },
      layers: [{ id: 'legacy' }],
    };
    expect(selectLayersToRestore(state as any).map((l: any) => l.id)).toEqual(['obs']);
  });

  it('falls back to persisted layers when the observation is stale or absent', () => {
    const stale = {
      _current_cartographic_fingerprint: 'carto-sha256:new',
      _cartographic_observation: {
        mapspec_fingerprint: 'carto-sha256:old',
        layers: [{ id: 'obs' }],
      },
      layers: [{ id: 'legacy' }],
    };
    expect(selectLayersToRestore(stale as any).map((l: any) => l.id)).toEqual(['legacy']);
    expect(selectLayersToRestore({ layers: [{ id: 'legacy' }] } as any).map((l: any) => l.id)).toEqual(['legacy']);
  });
});

describe('selectCameraToRestore', () => {
  it('returns MapSpec.view only when it was explicit framing', () => {
    expect(selectCameraToRestore({
      viewport: { center: [1, 2], zoom: 8 },
      mapspec: { view: { center: [114.3, 30.5], zoom: 10, framed: true } },
    })).toEqual({ center: [114.3, 30.5], zoom: 10, bearing: undefined, pitch: undefined });
  });

  it('ignores viewport hints and unframed suggested views', () => {
    expect(selectCameraToRestore({
      viewport: { center: [1, 2], zoom: 8 },
      mapspec: { view: { center: [0, 0], zoom: 2 } },
    })).toBeNull();
    expect(selectCameraToRestore({
      viewport: { center: [1, 2], zoom: 8 },
    })).toBeNull();
  });
});

describe('buildLayerFromRestored', () => {
  it('maps runtime_store_id, ref tile url and mapspec metadata', () => {
    const layer = buildLayerFromRestored(
      {
        id: 'mapspec-layer',
        _refId: 'ref:geojson-1',
        runtime_store_id: 'ref:geojson-1',
        name: '分析结果',
        type: 'vector',
        visible: true,
        opacity: 0.8,
        projection_fingerprint: 'proj-fp',
        intent_generation: 3,
      },
      'sid-1',
      'carto-fp',
    );
    expect(layer.id).toBe('ref:geojson-1');
    expect(layer.name).toBe('分析结果');
    expect(layer.type).toBe('vector');
    expect(layer.opacity).toBe(0.8);
    expect(layer._tileUrl).toBe(
      'http://localhost:8000/api/v1/layers/data/ref:geojson-1/tiles/{z}/{x}/{y}.mvt?session_id=sid-1'
    );
    expect(layer._mapspecFingerprint).toBe('carto-fp');
    expect(layer._mapspecLayerId).toBe('mapspec-layer');
    expect(layer._mapspecProjectionFingerprint).toBe('proj-fp');
    expect(layer._intentGeneration).toBe(3);
  });

  it('lets committed MapSpec visibility and opacity override restored HUD chrome', () => {
    const layer = buildLayerFromRestored(
      {
        id: 'L1',
        name: 'Schools',
        type: 'vector',
        visible: true,
        opacity: 1,
      },
      'sid-1',
      'carto-fp',
      {
        layers: [
          {
            id: 'L1',
            layout: { visibility: 'none' },
            paint: { opacity: 0.4, 'circle-opacity': 0.4 },
          },
        ],
      },
    );
    expect(layer.visible).toBe(false);
    expect(layer.opacity).toBe(0.4);
  });

  it('treats raster_image + raster_bbox as a heatmap with the raster source', () => {
    const layer = buildLayerFromRestored(
      {
        id: 'raster-1',
        raster_image: 'data:image/png;base64,xxx',
        raster_bbox: [100, 20, 101, 21],
      },
      'sid-1',
    );
    expect(layer.type).toBe('heatmap');
    expect(layer.source).toEqual({ image: 'data:image/png;base64,xxx', bbox: [100, 20, 101, 21] });
  });
});

describe('syncSpecLayersToStore（product-* 直写图层镜像）', () => {
  const productSpec = {
    version: '1.0',
    sources: {
      'webgis_map_product_layer_source': { type: 'geojson', ref_id: 'ref:geojson-poi' },
    },
    layers: [
      {
        id: 'product-930-points',
        source: 'webgis_map_product_layer_source',
        type: 'circle',
        provenance: { algorithm: 'webgis_map_product' },
        name: '点位分布图',
      },
    ],
  };

  it('给无 store 行的 spec 层补 HUD 行（名称/ref 身份/瓦片端点）', () => {
    syncSpecLayersToStore(productSpec as any, 'sid-1');
    const rows = useHudStore.getState().layers;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: 'product-930-points',
      name: '点位分布图',
      _mapspecLayerId: 'product-930-points',
      _refId: 'ref:geojson-poi',
      visible: true,
    });
    expect(rows[0]._tileUrl).toContain('/tiles/{z}/{x}/{y}.mvt?session_id=sid-1');
  });

  it('无 spec 名时按 命名链 兜底（legend 标题 → 算法语义名）', () => {
    syncSpecLayersToStore({
      version: '1.0',
      sources: { s1: { type: 'geojson', ref_id: 'ref:r' } },
      layers: [
        { id: 'product-x-points', source: 's1', type: 'circle', provenance: { algorithm: 'webgis_map_product' } },
        { id: 'result-1', source: 's1', type: 'circle', provenance: { algorithm: 'query_local_poi' } },
      ],
    } as any, 'sid-1');
    const names = useHudStore.getState().layers.map((l) => l.name);
    expect(names).toContain('地图产品图层');
    expect(names).toContain('分析结果: query_local_poi');
  });

  it('layout.visibility=none 的 spec 层镜像为隐藏行', () => {
    syncSpecLayersToStore({
      version: '1.0',
      sources: {},
      layers: [{ id: 'hidden-one', type: 'circle', layout: { visibility: 'none' } }],
    } as any, 'sid-1');
    expect(useHudStore.getState().layers[0].visible).toBe(false);
  });

  it('幂等：按 id 与 _mapspecLayerId 双重去重，重复提交零新增', () => {
    syncSpecLayersToStore(productSpec as any, 'sid-1');
    syncSpecLayersToStore(productSpec as any, 'sid-1');
    expect(useHudStore.getState().layers).toHaveLength(1);

    // 已有行 id 不同但 _mapspecLayerId 相同（恢复层形态）也不重复镜像
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'ref:geojson-poi',
      name: 'POI',
      _mapspecLayerId: 'product-930-points',
    } as any);
    syncSpecLayersToStore(productSpec as any, 'sid-1');
    expect(useHudStore.getState().layers).toHaveLength(1);
  });
});

describe('restoreSessionMapLayers', () => {
  it('adds restored layers to the store and fetches ref data for non-MVT layers', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({ type: 'FeatureCollection', features: [] })),
    });

    await restoreSessionMapLayers(
      {
        layers: [
          {
            id: 'L1',
            name: 'A',
            type: 'vector',
            visible: true,
            opacity: 1,
            _refId: 'ref:abc',
            source: { type: 'FeatureCollection', features: [] },
          },
        ],
      },
      { sessionId: 'sid-1' }
    );

    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(useHudStore.getState().layers[0]._refId).toBe('ref:abc');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/layers/data/ref%3Aabc?session_id=sid-1'),
      expect.anything()
    );
  });

  it('skips the ref data fetch for MVT-capable large layers (tiles serve them)', async () => {
    await restoreSessionMapLayers(
      {
        layers: [
          {
            id: 'L1',
            name: 'A',
            type: 'vector',
            visible: true,
            opacity: 1,
            _refId: 'ref:big',
            _descriptor: { mvt_capable: true, feature_count: 9000 },
            source: { type: 'FeatureCollection', features: [] },
          },
        ],
      },
      { sessionId: 'sid-1' }
    );

    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stores restored MapSpec as the committed live document', async () => {
    const mapspec = {
      version: '1.0',
      sources: { keep: { type: 'geojson' } },
      layers: [{ id: 'keep', source: 'keep', type: 'circle' }],
    };
    await restoreSessionMapLayers(
      {
        layers: [{ id: 'keep', name: 'Keep', type: 'vector', visible: true, opacity: 1 }],
        mapspec,
      },
      { sessionId: 'sid-1' },
    );
    expect(getCommittedMapSpec()).toEqual(mapspec);
  });

  it('does not resurrect layers missing from committed MapSpec', async () => {
    await restoreSessionMapLayers(
      {
        layers: [
          { id: 'gone', name: 'Gone', type: 'vector', visible: true, opacity: 1 },
          { id: 'keep', name: 'Keep', type: 'vector', visible: true, opacity: 1 },
        ],
        mapspec: { layers: [{ id: 'keep', type: 'circle' }] },
      },
      { sessionId: 'sid-1' },
    );
    expect(useHudStore.getState().layers.map((layer) => layer.id)).toEqual(['keep']);
  });

  it('logs ApiError and toasts when ref data fetch fails, without dropping the layer', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: () => Promise.resolve(JSON.stringify({ detail: 'layer gone' })),
    });

    await restoreSessionMapLayers(
      {
        layers: [
          {
            id: 'L1',
            name: '学校分布',
            type: 'vector',
            visible: true,
            opacity: 1,
            _refId: 'ref:missing',
            source: { type: 'FeatureCollection', features: [] },
          },
        ],
      },
      { sessionId: 'sid-1' }
    );

    await vi.waitFor(() => {
      expect(devOnly.error).toHaveBeenCalledWith('[LayerFetch]', expect.any(ApiError));
    });
    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(useHudStore.getState().layers[0]._refId).toBe('ref:missing');
    const toast = useToastStore.getState().toasts.find((t) => t.type === 'error');
    expect(toast?.message).toContain('学校分布');
    expect(toast?.message).toMatch(/加载失败/);
  });
});

describe('#1078(G-9) syncSpecLayersToStore prunes removed mirror rows', () => {
  it('spec 层消失后镜像行被修剪；非镜像命令层与 pending 压制行保留', async () => {
    const { syncSpecLayersToStore } = await import('@/lib/session/map-state-restore');
    useHudStore.getState().clearLayers();
    // 初始：spec 有 ly-a/ly-b；面板行含两镜像 + 一个用户命令层 + 一个 pending 压制行
    const spec = {
      layers: [
        { id: 'ly-a', type: 'circle', source: 's1' },
        { id: 'ly-b', type: 'circle', source: 's2' },
        { id: 'ly-pending', type: 'circle', source: 's3' },
      ],
      sources: {},
    };
    syncSpecLayersToStore(spec, 'sess-1');
    useHudStore.getState().addLayer({
      id: 'user-command-layer', name: '命令层', type: 'vector', visible: true, opacity: 1,
    } as any);
    const { markPendingRemoved } = await import('@/lib/mapspec/session-cursor');
    markPendingRemoved('ly-pending');
    // 后端改写层集：ly-a 被移除、ly-pending 也被移除（但面板侧 pending 压制）
    const spec2 = {
      layers: [{ id: 'ly-b', type: 'circle', source: 's2' }],
      sources: {},
    };
    syncSpecLayersToStore(spec2, 'sess-1');
    const ids = useHudStore.getState().layers.map((l: any) => l.id);
    expect(ids).toContain('ly-b');
    expect(ids).not.toContain('ly-a');
    // pending 压制行保留（用户删除优先语义，由下次收敛处理）
    expect(ids).toContain('ly-pending');
    // 用户命令层（无 _mapspecLayerId）永不修剪
    expect(ids).toContain('user-command-layer');
  });

  it('空层集同样触发修剪（全部层被移除）', async () => {
    const { syncSpecLayersToStore } = await import('@/lib/session/map-state-restore');
    useHudStore.getState().clearLayers();
    syncSpecLayersToStore({
      layers: [{ id: 'ly-x', type: 'circle', source: 's' }], sources: {},
    }, 'sess-2');
    expect(useHudStore.getState().layers.some((l: any) => l.id === 'ly-x')).toBe(true);
    syncSpecLayersToStore({ layers: [], sources: {} }, 'sess-2');
    expect(
      useHudStore.getState().layers.filter((l: any) => l._mapspecLayerId === 'ly-x'),
    ).toHaveLength(0);
  });
});
