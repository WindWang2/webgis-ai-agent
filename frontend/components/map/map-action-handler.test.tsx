import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { MapActionHandler } from './map-action-handler';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { COMMAND_CATALOGUE } from '@/lib/map-commands/catalogue';
import {
  notifyUserGestureStart,
  _resetCameraArbitrationForTests,
} from '@/lib/map-commands/camera-arbitration';
import { clearStyleLayerIds } from '@/lib/map-kit/renderer';

const mockFlyTo = vi.fn();
const mapMockInstance = {
  flyTo: mockFlyTo,
  getSource: vi.fn(() => null),
  addSource: vi.fn(),
  addLayer: vi.fn(),
  getLayer: vi.fn(() => null),
  fitBounds: vi.fn(),
  getStyle: vi.fn(() => ({ layers: [] })),
  getCenter: vi.fn(() => ({ lat: 39.9, lng: 116.4 })),
  getZoom: vi.fn(() => 10),
  getCanvas: vi.fn(() => ({ width: 800, height: 600 })),
  getBearing: vi.fn(() => 0),
  getPitch: vi.fn(() => 0),
  once: vi.fn((_e: string, cb: () => void) => cb()),
  triggerRepaint: vi.fn(),
  removeLayer: vi.fn(),
  removeSource: vi.fn(),
  moveLayer: vi.fn(),
  setFilter: vi.fn(),
  setLayoutProperty: vi.fn(),
  setPaintProperty: vi.fn(),
  // V3 camera commands call map.stop() before starting a new animation (design §6)
  stop: vi.fn(),
  // #535: query_features 用到 project（lng/lat → 像素）与 queryRenderedFeatures
  project: vi.fn(() => ({ x: 128, y: 128 })),
  queryRenderedFeatures: vi.fn(() => []),
};

const mockGetMap = vi.fn(() => mapMockInstance);

let popAction: ReturnType<typeof vi.fn>;
let dispatchActionFn: ReturnType<typeof vi.fn>;
let reportTerminalFn: ReturnType<typeof vi.fn>;
let actions: Array<{ command: string; params: Record<string, unknown> }>;
const mockSetSelectedBaseLayer = vi.fn();

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    get actions() { return actions; },
    dispatchAction: dispatchActionFn,
    popAction,
    setSelectedBaseLayer: mockSetSelectedBaseLayer,
    // V3: the handler reports every terminal state through the context
    reportTerminal: reportTerminalFn,
  }),
}));

vi.mock('react-map-gl/maplibre', () => ({
  useMap: () => ({
    default: { getMap: mockGetMap },
  }),
}));

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8000',
}));

vi.mock('@/lib/providers', () => ({
  TILE_PROVIDERS: [
    { name: 'Carto Light', keywords: ['carto', 'light', '浅色'] },
    { name: 'Carto Dark', keywords: ['dark', '深色'] },
    { name: 'ESRI 影像', keywords: ['satellite', '卫星', 'esri'] },
  ],
}));

// Module-level spies — clearAllMocks() in beforeEach resets them.
// These must be module-level (not in factory body) so the test can assert calls.
const mockSetBaseLayer = vi.fn();
const mockSetPendingSystemMessage = vi.fn();
const mockRemoveLayer = vi.fn();
const mockUpdateLayer = vi.fn();
const mockReorderLayers = vi.fn();
const mockExport = vi.fn(async (): Promise<{ ok: boolean; error?: string }> => ({ ok: true }));
const mockAddAnnotation = vi.fn((feature) => {
  mockAnnotationsStore.push(feature);
});
const mockClearAnnotations = vi.fn(() => {
  mockAnnotationsStore = [];
});
let mockLayersStore: Array<{ id: string; name?: string; style?: any; visible?: boolean }> = [];
let mockAnnotationsStore: any[] = [];
// Round-2 FIX-B: mirrors useHudStore.baseLayer so base_layer_change can detect
// an unchanged base layer (no style swap needed) and resolve immediately.
let mockBaseLayerName: string | undefined;

// External-store plumbing for the useHudStore hook mock: lets tests force a
// re-render (e.g. to simulate a mapInstance identity change mid-flight) by
// emitting a change that the hook subscription observes. `mock`-prefixed so the
// vi.mock factory below may reference them (vitest hoisting rule).
const mockHudListeners = new Set<() => void>();
let mockHudVersion = 0;
const mockEmitHudChange = () => {
  mockHudVersion += 1;
  for (const listener of Array.from(mockHudListeners)) listener();
};

// Zustand stores are callable as hooks AND expose .getState().
// MapActionHandler uses both shapes:
//   - `useHudStore((s) => s.annotations)` at render (hook subscription)
//   - `useHudStore.getState().setBaseLayer(...)` inside handlers (imperative read)
// The mock factory builds the store inside itself (so the binding exists when the
// hoisted vi.mock runs); the module-level mock fns (mockSetBaseLayer etc.) are
// referenced by closure and resolve at call time.
vi.mock('@/lib/store/useHudStore', async () => {
  const React = await import('react');
  const buildState = () => ({
    // Lazy getters so test mutations to mockLayersStore/mockAnnotationsStore are seen live
    get layers() { return mockLayersStore; },
    get baseLayer() { return mockBaseLayerName; },
    setBaseLayer: mockSetBaseLayer,
    setPendingSystemMessage: mockSetPendingSystemMessage,
    removeLayer: mockRemoveLayer,
    updateLayer: mockUpdateLayer,
    reorderLayers: mockReorderLayers,
    get annotations() { return mockAnnotationsStore; },
    addAnnotation: mockAddAnnotation,
    clearAnnotations: mockClearAnnotations,
  });
  // Callable as a hook: useHudStore(selector) → selector(state), subscribing so
  // mockEmitHudChange() re-renders subscribers.
  // Also exposes .getState() for imperative reads.
  const useHudStore = Object.assign(
    (selector?: (s: any) => any) => {
      React.useSyncExternalStore(
        (listener) => {
          mockHudListeners.add(listener);
          return () => mockHudListeners.delete(listener);
        },
        () => mockHudVersion,
        () => mockHudVersion,
      );
      return selector ? selector(buildState()) : buildState();
    },
    { getState: buildState },
  );
  return { useHudStore };
});

// export_map's run dynamically imports the heavy exporter engine; mock it so the
// V3 promise-returning export path can be exercised without the real engine.
vi.mock('@/lib/map-kit/exporter', () => ({
  MapExporterEngine: { export: mockExport },
}));

/**
 * The shared mock now owns the FIX-B layout bookkeeping itself:
 * setLayoutProperty/setPaintProperty write into the target layer def and
 * getLayoutProperty/getPaintProperty read it back (#404), so the round-2 FIX-B
 * visibility verification can be exercised end to end without extra wrapping.
 */
function mapWithLayoutBookkeeping() {
  return makeMockMaplibreMap();
}

describe('MapActionHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    actions = [];
    popAction = vi.fn();
    dispatchActionFn = vi.fn((action) => { actions = [action]; });
    reportTerminalFn = vi.fn();
    mockLayersStore = [];
    mockAnnotationsStore = [];
    mockBaseLayerName = undefined;
    // Per-test overrides of mockGetMap (round-2 FIX-B tests use the shared
    // makeMockMaplibreMap) must not leak into later tests: clearAllMocks does
    // not reset implementations, so re-establish the default explicitly.
    mockGetMap.mockImplementation(() => mapMockInstance);
    _resetCameraArbitrationForTests();
    // #462: the renderer's per-map layer-id registry must not leak a seed
    // across tests on the shared singleton map (each test re-mocks getStyle).
    clearStyleLayerIds(mapMockInstance);
  });

  afterEach(() => {
    _resetCameraArbitrationForTests();
  });

  it('forwards bearing and pitch to map.flyTo()', async () => {
    actions = [{
      command: 'fly_to',
      params: { center: [116.4, 39.9], zoom: 12, bearing: 45, pitch: 30 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockFlyTo).toHaveBeenCalledWith(
      expect.objectContaining({ bearing: 45, pitch: 30 })
    );
  });

  it('omits bearing/pitch when not provided', async () => {
    actions = [{
      command: 'fly_to',
      params: { center: [116, 39], zoom: 12 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockFlyTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [116, 39], zoom: 12 })
    );
  });

  it('pops action after execution', async () => {
    actions = [{
      command: 'fly_to',
      params: { center: [116, 39], zoom: 12 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });
    // round-2 FIX-B: camera settles one frame after moveend (deferred settle so
    // a just-arriving user drag can cancel first) — flush that macrotask so the
    // assertion observes the terminal state deterministically instead of racing.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(popAction).toHaveBeenCalled();
  });

  // ─── Regressions for ISSUE-001/002/003 (commit 9766389) ────────────────
  // The original bug: clicking the baselayer dropdown updated only one of two
  // stores (useMapAction.selectedBaseLayer XOR useHudStore.baseLayer), so the
  // dropdown label, HUD panel, and AI env summary could disagree. The fix
  // dual-writes from BOTH the user click path (baselayer-switcher.tsx) and the
  // AI-driven path (BASE_LAYER_CHANGE handler in map-action-handler.tsx).
  // These tests pin the AI-driven half.

  it('regression ISSUE-002: BASE_LAYER_CHANGE dual-writes to both stores (exact name match)', async () => {
    actions = [{
      command: 'BASE_LAYER_CHANGE',
      params: { name: 'Carto Dark' },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // useMapAction writes index
    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(1);
    // useHudStore writes canonical name — this is the half that was missing pre-9766389
    expect(mockSetBaseLayer).toHaveBeenCalledWith('Carto Dark');
  });

  it('regression ISSUE-002: BASE_LAYER_CHANGE dual-writes when matched by keyword (e.g. AI says "卫星")', async () => {
    actions = [{
      command: 'BASE_LAYER_CHANGE',
      params: { name: '卫星' },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // matches TILE_PROVIDERS[2] = 'ESRI 影像' via keyword '卫星'
    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(2);
    // canonical name written to HUD, not the AI's casual phrasing
    expect(mockSetBaseLayer).toHaveBeenCalledWith('ESRI 影像');
  });

  it('regression ISSUE-002: BASE_LAYER_CHANGE no-match does NOT write either store', async () => {
    actions = [{
      command: 'BASE_LAYER_CHANGE',
      params: { name: 'NonExistentLayer' },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetSelectedBaseLayer).not.toHaveBeenCalled();
    expect(mockSetBaseLayer).not.toHaveBeenCalled();
  });

  it('adds custom- prefix to layer and source IDs for add_layer', async () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    actions = [{
      command: 'add_layer',
      params: { layerId: 'test-layer', type: 'fill', geojson },
    }];

    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('custom-test-layer', expect.anything());
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'custom-test-layer', source: 'custom-test-layer' }),
      undefined
    );
    // round-2 FIX-B: confirmed only after the source actually exists on the map
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('calls addThematicLayer when style has a thematic type (choropleth)', async () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    const style = {
      type: 'choropleth',
      field: 'density',
      breaks: [10],
      colors: ['#000', '#fff']
    };

    actions = [{
      command: 'add_layer',
      params: { layerId: 'thematic-layer', geojson, style },
    }];

    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('custom-thematic-layer', expect.anything());
    // Since addThematicLayer delegates to addVectorLayer eventually, we can check the paint expression
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'custom-thematic-layer',
        paint: expect.objectContaining({
          'fill-color': ['step', ['get', 'density'], '#000', 10, '#fff']
        })
      }),
      undefined
    );
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('calls navigation.fitBounds with correct arguments for zoom_to_bbox', async () => {
    actions = [{
      command: 'zoom_to_bbox',
      params: { bbox: [116.0, 39.0, 117.0, 40.0], padding: 40 },
    }];

    const map = mockGetMap();

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.fitBounds).toHaveBeenCalledWith([116.0, 39.0, 117.0, 40.0], { duration: 1500, padding: 40 });
  });

  it('calls navigation.flyTo with correct arguments for set_map_view', async () => {
    actions = [{
      command: 'set_map_view',
      params: { zoom: 11, bearing: 20, pitch: 15 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockFlyTo).toHaveBeenCalledWith(
      expect.objectContaining({
        center: [116.4, 39.9],
        zoom: 11,
        bearing: 20,
        pitch: 15
      })
    );
  });

  it('#534: bearing-only set_map_view passes the validator and flies (was invalid_params)', async () => {
    actions = [{
      command: 'set_map_view',
      params: { bearing: 30 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // 校验器放行 —— 不再以 invalid_params 失败（此前后端成功、前端立刻拒绝）
    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'set_map_view' }),
      'failed',
      expect.objectContaining({ error: 'invalid_params' }),
    );
    // 只改 bearing：center/zoom/pitch 保持当前值
    expect(mockFlyTo).toHaveBeenCalledWith(
      expect.objectContaining({
        center: [116.4, 39.9],
        zoom: 10,
        bearing: 30,
        pitch: 0,
      })
    );
  });

  it('#534: pitch-only set_map_view passes the validator and flies', async () => {
    actions = [{
      command: 'set_map_view',
      params: { pitch: 60 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'set_map_view' }),
      'failed',
      expect.objectContaining({ error: 'invalid_params' }),
    );
    expect(mockFlyTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [116.4, 39.9], zoom: 10, bearing: 0, pitch: 60 })
    );
  });

  it('#535: query_features dispatches, runs the rendered-feature query and acks succeeded (was unknown_command)', async () => {
    actions = [{
      command: 'query_features',
      params: { location: [116.4, 39.9], buffer_m: 10 },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // 不在目录里注册前，这里以 unknown_command 失败且没有任何执行副作用。
    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'query_features' }),
      'failed',
      expect.objectContaining({ error: 'unknown_command' }),
    );
    // 查询真实执行（lng/lat 投影 → queryRenderedFeatures）
    expect(mapMockInstance.queryRenderedFeatures).toHaveBeenCalledTimes(1);
    // 终端状态是 succeeded（0 个要素也如实成功、带 count）
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'query_features' }),
      'succeeded',
      expect.objectContaining({
        actual: expect.objectContaining({ featureCount: 0, summary: expect.stringContaining('未查询到已渲染要素') }),
      }),
    );
  });

  it('calls removeLayerStack when executing remove_layer', async () => {
    actions = [{
      command: 'remove_layer',
      params: { layerId: 'target-layer' },
    }];

    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map.addSource('custom-target-layer', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'custom-target-layer', type: 'fill', source: 'custom-target-layer' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.removeLayer).toHaveBeenCalledWith('custom-target-layer');
    expect(map.removeSource).toHaveBeenCalledWith('custom-target-layer');
    expect(mockRemoveLayer).toHaveBeenCalledWith('target-layer');
    // round-2 FIX-B: the stack is actually gone from the map → confirmed
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('calls map.moveLayer correctly when executing REORDER_LAYER to top', async () => {
    actions = [{
      command: 'REORDER_LAYER',
      params: { layer_id: 'reorder-layer', position: 'top' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [
        { id: 'custom-other-layer' },
        { id: 'custom-reorder-layer' }
      ]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.moveLayer).toHaveBeenCalledWith('custom-reorder-layer', undefined);
  });

  it('calls map.moveLayer correctly for REORDER_LAYER position bottom', async () => {
    actions = [{
      command: 'REORDER_LAYER',
      params: { layer_id: 'reorder-layer', position: 'bottom' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [
        { id: 'custom-other-layer' },
        { id: 'custom-reorder-layer' }
      ]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.moveLayer).toHaveBeenCalledWith('custom-reorder-layer', 'custom-other-layer');
  });

  it('calls map.moveLayer correctly for REORDER_LAYER position up', async () => {
    actions = [{
      command: 'REORDER_LAYER',
      params: { layer_id: 'layer2', position: 'up' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [
        { id: 'custom-layer1' },
        { id: 'custom-layer2' },
        { id: 'custom-layer3' }
      ]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.moveLayer).toHaveBeenCalledWith('custom-layer2', 'custom-layer1');
  });

  it('calls map.moveLayer correctly for REORDER_LAYER position down', async () => {
    actions = [{
      command: 'REORDER_LAYER',
      params: { layer_id: 'layer2', position: 'down' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [
        { id: 'custom-layer1' },
        { id: 'custom-layer2' },
        { id: 'custom-layer3' }
      ]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.moveLayer).toHaveBeenCalledWith('custom-layer2', undefined);
  });

  it('calls map.moveLayer correctly for REORDER_LAYER position before', async () => {
    actions = [{
      command: 'REORDER_LAYER',
      params: { layer_id: 'layer3', position: 'before', before_id: 'layer1' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [
        { id: 'custom-layer1' },
        { id: 'custom-layer2' },
        { id: 'custom-layer3' }
      ]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.moveLayer).toHaveBeenCalledWith('custom-layer3', 'custom-layer1');
  });

  it('handles LAYER_VISIBILITY_UPDATE correctly', async () => {
    actions = [{
      command: 'LAYER_VISIBILITY_UPDATE',
      params: { layer_id: 'vis-layer', visible: false, opacity: 0.5 },
    }];

    mockLayersStore = [{ id: 'vis-layer', name: 'Visibility Layer' }];
    const map = mapWithLayoutBookkeeping();
    mockGetMap.mockImplementation(() => map);
    map._layers.push({ id: 'custom-vis-layer-fill', type: 'fill', source: 'custom-vis-layer' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockUpdateLayer).toHaveBeenCalledWith('vis-layer', { visible: false, opacity: 0.5 });
    // round-2 FIX-B: the visibility change is applied AND verified on the map
    expect(map.setLayoutProperty).toHaveBeenCalledWith('custom-vis-layer-fill', 'visibility', 'none');
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('handles LAYER_STYLE_UPDATE correctly', async () => {
    actions = [{
      command: 'LAYER_STYLE_UPDATE',
      params: { layer_id: 'style-layer', style: { color: '#00ff00', strokeWidth: 2 } },
    }];

    mockLayersStore = [{ id: 'style-layer', name: 'Style Layer', style: { color: '#ff0000' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map._layers.push({ id: 'custom-style-layer-fill', type: 'fill', source: 'custom-style-layer' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockUpdateLayer).toHaveBeenCalledWith('style-layer', { style: { color: '#00ff00', strokeWidth: 2 } });
    // round-2 FIX-B: the style update is applied to a layer that exists on the map
    expect(map.setPaintProperty).toHaveBeenCalledWith('custom-style-layer-fill', 'fill-color', '#00ff00');
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_STYLE_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('handles add_marker and updates annotation layer', async () => {
    actions = [{
      command: 'add_marker',
      params: { longitude: 116.4, latitude: 39.9, label: 'Test Marker', color: '#ff0000' },
    }];

    const map = mockGetMap();
    const mockSetData = vi.fn();
    let sourceExists = false;
    (map.getSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations' && sourceExists) {
        return { setData: mockSetData };
      }
      return null;
    });
    (map.addSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations') {
        sourceExists = true;
      }
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('claude-annotations', expect.any(Object));
    expect(map.addLayer).toHaveBeenCalled();
    expect(mockSetData).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'FeatureCollection',
        features: expect.arrayContaining([
          expect.objectContaining({
            properties: expect.objectContaining({ label: 'Test Marker', color: '#ff0000', kind: 'marker' })
          })
        ])
      })
    );
  });

  it('handles draw_measurement for polyline shape', async () => {
    actions = [{
      command: 'draw_measurement',
      params: { shape: 'polyline', coordinates: [[116.4, 39.9], [116.5, 40.0]], label: '10km' },
    }];

    const map = mockGetMap();
    const mockSetData = vi.fn();
    let sourceExists = false;
    (map.getSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations' && sourceExists) {
        return { setData: mockSetData };
      }
      return null;
    });
    (map.addSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations') {
        sourceExists = true;
      }
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetData).toHaveBeenCalledWith(
      expect.objectContaining({
        features: expect.arrayContaining([
          expect.objectContaining({
            geometry: expect.objectContaining({ type: 'LineString' }),
            properties: expect.objectContaining({ label: '10km', kind: 'measure_line' })
          })
        ])
      })
    );
  });

  it('handles draw_measurement for polygon shape', async () => {
    actions = [{
      command: 'draw_measurement',
      params: { shape: 'polygon', coordinates: [[116.4, 39.9], [116.5, 39.9], [116.5, 40.0], [116.4, 39.9]], label: '100sqkm' },
    }];

    const map = mockGetMap();
    const mockSetData = vi.fn();
    let sourceExists = false;
    (map.getSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations' && sourceExists) {
        return { setData: mockSetData };
      }
      return null;
    });
    (map.addSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations') {
        sourceExists = true;
      }
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetData).toHaveBeenCalledWith(
      expect.objectContaining({
        features: expect.arrayContaining([
          expect.objectContaining({
            geometry: expect.objectContaining({ type: 'Polygon' }),
            properties: expect.objectContaining({ label: '100sqkm', kind: 'measure_polygon' })
          })
        ])
      })
    );
  });

  it('handles clear_annotations correctly', async () => {
    actions = [{
      command: 'clear_annotations',
      params: {},
    }];

    const map = mockGetMap();
    const mockSetData = vi.fn();
    let sourceExists = true;
    (map.getSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations' && sourceExists) {
        return { setData: mockSetData };
      }
      return null;
    });
    (map.addSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations') {
        sourceExists = true;
      }
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetData).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'FeatureCollection',
        features: []
      })
    );
  });

  it('handles APPLY_LAYER_FILTER correctly with parsed filter array (issue #393)', async () => {
    actions = [{
      command: 'APPLY_LAYER_FILTER',
      params: { layer_id: 'custom-layer', filter: '["==", "density", 10]' },
    }];
    // The map has MapSpec sublayers (`${id}__*`), not the bare id — the old code
    // setFilter'd the bare id (a silent no-op) and acked succeeded anyway.
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'custom-layer__fill', type: 'fill' });
    map.addLayer({ id: 'custom-layer__point', type: 'circle' });
    mockGetMap.mockImplementation(() => map);
    mockLayersStore = [{ id: 'custom-layer', name: 'Custom' }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.setFilter).toHaveBeenCalledWith('custom-layer__fill', ['==', 'density', 10]);
    expect(map.setFilter).toHaveBeenCalledWith('custom-layer__point', ['==', 'density', 10]);
    // store sync: the reconcile re-emits layer.filter, so the filter survives
    expect(mockUpdateLayer).toHaveBeenCalledWith('custom-layer', { filter: ['==', 'density', 10] });
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'APPLY_LAYER_FILTER' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('APPLY_LAYER_FILTER with no matching target → failed ack target_not_found (no fake success)', async () => {
    actions = [{
      command: 'APPLY_LAYER_FILTER',
      params: { layer_id: 'ghost', filter: ['==', 'density', 10] },
    }];
    const map = makeMockMaplibreMap(); // no layers on the map, none in the store
    mockGetMap.mockImplementation(() => map);
    mockLayersStore = [];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'APPLY_LAYER_FILTER' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(map.setFilter).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  // ─── V3 terminal states (Harness–Map Interaction Closed Loop, design §6) ──
  // Every action settles exactly once: queued → running → terminal. The handler
  // reports the terminal through reportTerminal (→ context ack sink) and then pops.

  it('V3: unknown command → failed ack unknown_command (was warn + silent pop)', async () => {
    actions = [{ command: 'not_a_real_command', params: {} }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'not_a_real_command' }),
      'failed',
      expect.objectContaining({ error: 'unknown_command' }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3: requiredParams now gates the SSE path → failed ack invalid_params', async () => {
    // fly_to requires a center; the SSE path now rejects param failures as terminal
    actions = [{ command: 'fly_to', params: { zoom: 12 } }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' }),
      'failed',
      expect.objectContaining({ error: 'invalid_params' }),
    );
    // rejected before dispatch — the map never moved
    expect(mockFlyTo).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  it('V3: explicit succeeded result → succeeded ack', async () => {
    actions = [{ command: 'clear_annotations', params: {} }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'clear_annotations' }),
      'succeeded',
      expect.any(Object),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 (issue #393): void run → failed ack no_result, never a fake succeeded', async () => {
    // Every catalogue command now returns an explicit MapCommandResult; a void
    // return means the command forgot to report (or an unverifiable path
    // slipped through) and must fail honestly — the old default converted the
    // broken apply_layer_filter/heatmap commands into fake successes.
    const original = (COMMAND_CATALOGUE as any).clear_annotations;
    (COMMAND_CATALOGUE as any).clear_annotations = { requiredParams: () => true, run: () => undefined };
    try {
      actions = [{ command: 'clear_annotations', params: {} }];

      await act(async () => {
        render(<MapActionHandler />);
      });

      expect(reportTerminalFn).toHaveBeenCalledWith(
        expect.objectContaining({ command: 'clear_annotations' }),
        'failed',
        expect.objectContaining({ error: 'no_result' }),
      );
      expect(popAction).toHaveBeenCalled();
    } finally {
      (COMMAND_CATALOGUE as any).clear_annotations = original;
    }
  });

  it('V3: ack metadata carries started_at/finished_at/duration_ms', async () => {
    actions = [{ command: 'clear_annotations', params: {} }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    const details = reportTerminalFn.mock.calls[0][2] as Record<string, unknown>;
    expect(typeof details.startedAt).toBe('string');
    expect(details.startedAt).toBeTruthy();
    expect(typeof details.finishedAt).toBe('string');
    expect(typeof details.durationMs).toBe('number');
    expect(details.durationMs as number).toBeGreaterThanOrEqual(0);
  });

  it('V3: camera command settles succeeded with the settled viewport as actual', async () => {
    actions = [{ command: 'fly_to', params: { center: [116, 39], zoom: 12 } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    // Simulate the animation actually reaching the requested target: the round-2
    // convergence check only acks succeeded when the settled viewport reached
    // the target within tolerance (a success the map did not earn is a fake ack)
    // — the old static mock viewport would settle `interrupted`.
    map.flyTo.mockImplementation(() => map._setViewport({ center: [116, 39], zoom: 12 }));

    await act(async () => {
      render(<MapActionHandler />);
    });
    // moveend (from the mock emitter) + the one-frame deferred settle
    await act(async () => {
      map._fire('moveend');
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' }),
      'succeeded',
      expect.objectContaining({
        actual: { center: [116, 39], zoom: 12, bearing: 0, pitch: 0 },
      }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3: user gesture mid-flight → cancelled ack superseded_by_user', async () => {
    // Hold moveend back so the camera promise stays pending; then a user gesture
    // supersedes the in-flight animation.
    let capturedMoveend: (() => void) | undefined;
    (mapMockInstance.once as any).mockImplementationOnce((_e: string, cb: () => void) => {
      capturedMoveend = cb;
    });
    actions = [{ command: 'fly_to', params: { center: [116, 39], zoom: 12 } }];

    await act(async () => {
      render(<MapActionHandler />);
    });
    expect(mockFlyTo).toHaveBeenCalled();
    expect(reportTerminalFn).not.toHaveBeenCalled(); // still running

    await act(async () => {
      notifyUserGestureStart();
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' }),
      'cancelled',
      expect.objectContaining({ error: 'superseded_by_user' }),
    );
    expect(popAction).toHaveBeenCalled();
    expect(capturedMoveend).toBeDefined(); // the stale moveend never fired
  });

  it('V3: effect re-run with the same head action does not re-execute run() (runningActionIdRef guard)', async () => {
    // Hold moveend back so the camera promise stays pending while we re-render.
    let capturedMoveend: (() => void) | undefined;
    (mapMockInstance.once as any).mockImplementationOnce((_e: string, cb: () => void) => {
      capturedMoveend = cb;
    });
    actions = [{ command: 'fly_to', params: { center: [116, 39], zoom: 12 } }];

    render(<MapActionHandler />);
    expect(mockFlyTo).toHaveBeenCalledTimes(1);
    expect(reportTerminalFn).not.toHaveBeenCalled(); // still running

    // mapInstance identity changes on every useMap() call — force a re-render
    // via the HUD store subscription (simulates MapProvider remount mid-flight
    // with the same action at the queue head). The runningActionIdRef guard must
    // skip the duplicate execution: the first run owns the settle.
    await act(async () => {
      mockEmitHudChange();
    });

    expect(mockFlyTo).toHaveBeenCalledTimes(1); // NOT re-executed
    expect(reportTerminalFn).not.toHaveBeenCalled(); // first run still owns the settle

    // first run completes normally → single terminal report + single pop
    await act(async () => {
      capturedMoveend?.();
    });
    // deferred settle one frame after moveend (round-2 camera settle)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(reportTerminalFn).toHaveBeenCalledTimes(1);
    expect(popAction).toHaveBeenCalledTimes(1);
  });

  it('V3: throw inside run → failed ack + user system message preserved', async () => {
    mockClearAnnotations.mockImplementationOnce(() => {
      throw new Error('boom');
    });
    actions = [{ command: 'clear_annotations', params: {} }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'clear_annotations' }),
      'failed',
      expect.objectContaining({ error: 'boom' }),
    );
    // /review C10: user-facing system message kept for unexpected throws
    expect(mockSetPendingSystemMessage).toHaveBeenCalledWith(expect.stringContaining('boom'));
    expect(popAction).toHaveBeenCalled();
  });

  it('V3: export_map returns a promise — succeeds + pops only after the render-callback work', async () => {
    actions = [{
      command: 'export_map',
      params: { format: 'png' },
    }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // exporter engine invoked with the real map + params
    expect(mockExport).toHaveBeenCalledWith(
      expect.objectContaining({ map: mapMockInstance }),
      expect.objectContaining({ format: 'png' }),
    );
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'export_map' }),
      'succeeded',
      expect.any(Object),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3: export_map failure → failed ack export_failed', async () => {
    mockExport.mockResolvedValueOnce({ ok: false, error: 'canvas busy' });
    actions = [{ command: 'export_map', params: {} }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'export_map' }),
      'failed',
      expect.objectContaining({ error: 'export_failed' }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  // ─── V3 review FIX-2 ────────────────────────────────────────────────────
  // (5) [P1] missing-layer acks: remove_layer / layer_visibility_update /
  // layer_style_update returned void (succeeded) when the target layer does not
  // exist (silent no-op forEach). They now fail with target_not_found and the
  // map is NOT mutated with fabricated ids.

  it('V3 FIX-2: remove_layer with a missing target → failed ack target_not_found, no fabricated-id map mutation', async () => {
    actions = [{ command: 'remove_layer', params: { layerId: 'ghost-layer' } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({ layers: [] }); // no custom-ghost-layer anywhere
    mockLayersStore = []; // and not in the store either

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(map.removeLayer).not.toHaveBeenCalled();
    expect(map.removeSource).not.toHaveBeenCalled();
    expect(mockRemoveLayer).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 FIX-2: layer_visibility_update with a missing target → failed ack target_not_found, no store update', async () => {
    actions = [{ command: 'LAYER_VISIBILITY_UPDATE', params: { layer_id: 'ghost-layer', visible: false } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({ layers: [] });
    mockLayersStore = [];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(mockUpdateLayer).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 FIX-2: layer_style_update with a missing target → failed ack target_not_found', async () => {
    actions = [{ command: 'LAYER_STYLE_UPDATE', params: { layer_id: 'ghost-layer', style: { color: '#f00' } } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({ layers: [] });
    mockLayersStore = [];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_STYLE_UPDATE' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  // (8) [P2] base_layer_change no-match + reorder_layer missing-layer reach
  // explicit failed acks (target_not_found) — tests pin the failed terminal.

  it('V3 FIX-2: BASE_LAYER_CHANGE with no matching provider → failed ack target_not_found', async () => {
    actions = [{ command: 'BASE_LAYER_CHANGE', params: { name: 'NonExistentLayer' } }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'BASE_LAYER_CHANGE' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(mockSetSelectedBaseLayer).not.toHaveBeenCalled();
    expect(mockSetBaseLayer).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 FIX-2: REORDER_LAYER with a missing layer → failed ack target_not_found, moveLayer NOT called', async () => {
    actions = [{ command: 'REORDER_LAYER', params: { layer_id: 'ghost', position: 'top' } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({ layers: [{ id: 'custom-other' }] });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'REORDER_LAYER' }),
      'failed',
      expect.objectContaining({ error: 'target_not_found' }),
    );
    expect(map.moveLayer).not.toHaveBeenCalled();
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 (issue #393): REORDER_LAYER on a MapSpec `${id}__*` layer reorders the store durably', async () => {
    actions = [{ command: 'REORDER_LAYER', params: { layer_id: 'analysis-result', position: 'top' } }];
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'analysis-result__fill', type: 'fill' });
    map.addLayer({ id: 'analysis-result__point', type: 'circle' });
    map.addLayer({ id: 'other__fill', type: 'fill' });
    mockGetMap.mockImplementation(() => map);
    mockLayersStore = [{ id: 'other' }, { id: 'analysis-result' }];

    await act(async () => {
      render(<MapActionHandler />);
    });

    // index 0 = topmost; the MapSpecRuntime reconcile applies the map z-order
    // from this array — the legacy custom-only matcher failed target_not_found
    expect(mockReorderLayers).toHaveBeenCalledWith([
      { id: 'analysis-result' },
      { id: 'other' },
    ]);
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'REORDER_LAYER' }),
      'succeeded',
      expect.objectContaining({ actual: { store_updated: true } }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  // (8) [P2] add_layer legacy {id} form: the run body now reads `id` as the
  // layerId fallback (the validator already accepted it — the run didn't).

  it('V3 FIX-2: add_layer legacy {id} params work and ack confirmed (id → layerId fallback)', async () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    actions = [{ command: 'add_layer', params: { id: 'legacy-layer', type: 'fill', geojson } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('custom-legacy-layer', expect.anything());
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'custom-legacy-layer', source: 'custom-legacy-layer' }),
      undefined,
    );
    // (6) [P2] layer add now carries a verifiable marker — confirmed only after
    // the source is actually present on the map (round-2 FIX-B).
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  // (6) [P2] verifiable `actual` for layer/annotation commands — the harness
  // InteractionStateConvergenceRate needs actual.confirmed after a mutation.

  it('V3 FIX-2: REORDER_LAYER success ack carries actual {confirmed:true}', async () => {
    actions = [{ command: 'REORDER_LAYER', params: { layer_id: 'reorder-layer', position: 'top' } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [{ id: 'custom-other-layer' }, { id: 'custom-reorder-layer' }],
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'REORDER_LAYER' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('V3 FIX-2: remove_layer success ack carries actual {confirmed:true}', async () => {
    actions = [{ command: 'remove_layer', params: { layerId: 'target-layer' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map.addSource('custom-target-layer', { type: 'geojson', data: {} });
    map.addLayer({ id: 'custom-target-layer', type: 'fill', source: 'custom-target-layer' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.removeLayer).toHaveBeenCalledWith('custom-target-layer');
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('V3 FIX-2: add_marker success ack carries actual {confirmed:true}', async () => {
    actions = [{ command: 'add_marker', params: { longitude: 116.4, latitude: 39.9, label: 'M' } }];
    const map = mockGetMap();
    const mockSetData = vi.fn();
    let sourceExists = false;
    (map.getSource as any).mockImplementation((id: string) =>
      id === 'claude-annotations' && sourceExists ? { setData: mockSetData } : null,
    );
    (map.addSource as any).mockImplementation((id: string) => {
      if (id === 'claude-annotations') sourceExists = true;
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetData).toHaveBeenCalled();
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_marker' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  // ─── V3 round-2 FIX-B (layer-command truthfulness) ───────────────────────
  // (1) [P1] `confirmed:true` must follow a REAL post-mutation map check —
  // add_* verifies getSource, remove/visibility/style verify the map state.
  // (2) [P1] remove/visibility/style resolve store layers keyed `${id}__${sub}`
  // (MapSpecRuntime reconcile); sublayers that cannot be verified synchronously
  // ack `store_updated:true` (backend treats it as non-converging) — never a
  // fabricated `confirmed`.

  it('V3 FIX-B: add_layer acks mutation_failed (never confirmed) when the source is not on the map', async () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    actions = [{ command: 'add_layer', params: { layerId: 'ghost-source', type: 'fill', geojson } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    // The add call is swallowed (e.g. mid style swap) — the source never appears.
    map.addSource.mockImplementation(() => {});

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_layer' }),
      'failed',
      expect.objectContaining({ error: 'mutation_failed' }),
    );
    // no confirmed claim without a map-side check
    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_layer' }),
      'succeeded',
      expect.objectContaining({ actual: expect.objectContaining({ confirmed: true }) }),
    );
  });

  it('V3 FIX-B: add_raster_layer confirms only when the image source exists on the map', async () => {
    actions = [{
      command: 'add_raster_layer',
      params: { id: 'raster-layer', image: 'https://example.com/img.png', bbox: [116, 39, 117, 40] },
    }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('custom-raster-layer', expect.anything());
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_raster_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('V3 FIX-B: add_raster_layer with a swallowed source add → mutation_failed', async () => {
    actions = [{
      command: 'add_raster_layer',
      params: { id: 'raster-layer', image: 'https://example.com/img.png', bbox: [116, 39, 117, 40] },
    }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map.addSource.mockImplementation(() => {});

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'add_raster_layer' }),
      'failed',
      expect.objectContaining({ error: 'mutation_failed' }),
    );
  });

  it('V3 FIX-B: remove_layer resolves a __-keyed store layer and removes all matched sublayers', async () => {
    actions = [{ command: 'remove_layer', params: { layer_id: 'poi' } }];
    mockLayersStore = [{ id: 'poi', name: 'POI Layer' }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map._sources['poi'] = { type: 'geojson', data: {} };
    map._layers.push({ id: 'poi__main', type: 'circle', source: 'poi' });
    map._layers.push({ id: 'poi__label', type: 'symbol', source: 'poi' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.removeLayer).toHaveBeenCalledWith('poi__main');
    expect(map.removeLayer).toHaveBeenCalledWith('poi__label');
    expect(map.removeSource).toHaveBeenCalledWith('poi');
    expect(mockRemoveLayer).toHaveBeenCalledWith('poi');
    // every matched sublayer is gone from the map → confirmed
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('V3 FIX-B: remove_layer on a store layer with no applied sublayers acks store_updated, not confirmed', async () => {
    actions = [{ command: 'remove_layer', params: { layer_id: 'poi' } }];
    mockLayersStore = [{ id: 'poi', name: 'POI Layer' }];
    const map = makeMockMaplibreMap(); // store layer exists but no sublayers applied yet
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockRemoveLayer).toHaveBeenCalledWith('poi');
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'succeeded',
      expect.objectContaining({ actual: { store_updated: true } }),
    );
    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'remove_layer' }),
      'succeeded',
      expect.objectContaining({ actual: expect.objectContaining({ confirmed: true }) }),
    );
  });

  it('V3 FIX-B: layer_visibility_update on a __-keyed store layer updates and verifies the sublayer', async () => {
    actions = [{ command: 'LAYER_VISIBILITY_UPDATE', params: { layer_id: 'poi', visible: false } }];
    mockLayersStore = [{ id: 'poi', name: 'POI Layer', visible: true }];
    const map = mapWithLayoutBookkeeping();
    mockGetMap.mockImplementation(() => map);
    map._layers.push({ id: 'poi__main', type: 'circle', source: 'poi' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.setLayoutProperty).toHaveBeenCalledWith('poi__main', 'visibility', 'none');
    expect(mockUpdateLayer).toHaveBeenCalledWith('poi', expect.objectContaining({ visible: false }));
    // getLayoutProperty reflects the new value → confirmed
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  it('V3 FIX-B: visibility update on a store layer with no applied sublayers acks store_updated', async () => {
    actions = [{ command: 'LAYER_VISIBILITY_UPDATE', params: { layer_id: 'poi', visible: false } }];
    mockLayersStore = [{ id: 'poi', name: 'POI Layer' }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockUpdateLayer).toHaveBeenCalledWith('poi', expect.objectContaining({ visible: false }));
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { store_updated: true } }),
    );
  });

  it('V3 FIX-B: visibility update whose value does not stick on the map → store_updated', async () => {
    actions = [{ command: 'LAYER_VISIBILITY_UPDATE', params: { layer_id: 'poi', visible: false } }];
    mockLayersStore = [{ id: 'poi', name: 'POI Layer' }];
    // The shared mock is stateful now (setLayoutProperty writes into the layer
    // def), so "unverifiable" must simulate the real-world race the FIX-B
    // guard exists for: the set did not take effect on the map (e.g. style
    // still loading in real MapLibre). Keep getLayoutProperty stale — it must
    // NOT reflect the 'none' that was just set.
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map._layers.push({ id: 'poi__main', type: 'circle', source: 'poi' });
    map.getLayoutProperty = vi.fn(() => 'visible');

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { store_updated: true } }),
    );
    expect(reportTerminalFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_VISIBILITY_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: expect.objectContaining({ confirmed: true }) }),
    );
  });

  it('V3 FIX-B: layer_style_update on a __-keyed store layer verifies getLayer before confirming', async () => {
    actions = [{ command: 'LAYER_STYLE_UPDATE', params: { layer_id: 'poi', style: { color: '#ff0000' } } }];
    mockLayersStore = [{ id: 'poi', style: {} }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);
    map._layers.push({ id: 'poi__main', type: 'circle', source: 'poi' });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.setPaintProperty).toHaveBeenCalledWith('poi__main', 'circle-color', '#ff0000');
    expect(mockUpdateLayer).toHaveBeenCalledWith('poi', { style: { color: '#ff0000' } });
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'LAYER_STYLE_UPDATE' }),
      'succeeded',
      expect.objectContaining({ actual: { confirmed: true } }),
    );
  });

  // (3) [P1] base_layer_change must not ack succeeded before the async style
  // swap starts — resolve on the next map `style.load`, fail on style error or
  // 15s timeout; an unchanged base layer resolves immediately.

  it('V3 FIX-B: base_layer_change resolves succeeded only after map style.load', async () => {
    actions = [{ command: 'base_layer_change', params: { name: 'Carto Dark' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    render(<MapActionHandler />);
    // The store writes happen immediately, but the ack must wait for the swap.
    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(1);
    expect(mockSetBaseLayer).toHaveBeenCalledWith('Carto Dark');
    expect(reportTerminalFn).not.toHaveBeenCalled();

    await act(async () => {
      map._fire('style.load');
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'base_layer_change' }),
      'succeeded',
      expect.any(Object),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 FIX-B: base_layer_change fails with timeout when style.load never fires', async () => {
    actions = [{ command: 'base_layer_change', params: { name: 'Carto Dark' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    vi.useFakeTimers();
    try {
      render(<MapActionHandler />);
      expect(reportTerminalFn).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000);
      });
    } finally {
      vi.useRealTimers();
    }

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'base_layer_change' }),
      'failed',
      expect.objectContaining({ error: 'timeout' }),
    );
    expect(popAction).toHaveBeenCalled();
  });

  it('V3 FIX-B: base_layer_change fails on a style error event', async () => {
    actions = [{ command: 'base_layer_change', params: { name: 'Carto Dark' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    render(<MapActionHandler />);
    await act(async () => {
      map._fire('error', { error: new Error('Style parse error: unsupported layer type') });
    });

    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'base_layer_change' }),
      'failed',
      expect.objectContaining({ error: 'style_error' }),
    );
  });

  it('V3 FIX-B: a tile fetch error does not fail the base layer swap', async () => {
    actions = [{ command: 'base_layer_change', params: { name: 'Carto Dark' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    render(<MapActionHandler />);
    await act(async () => {
      map._fire('error', { tile: {}, error: new Error('Tile load failed') });
    });
    expect(reportTerminalFn).not.toHaveBeenCalled();

    await act(async () => {
      map._fire('style.load');
    });
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'base_layer_change' }),
      'succeeded',
      expect.any(Object),
    );
  });

  it('V3 FIX-B: base_layer_change with an unchanged base layer resolves immediately, no style wait', async () => {
    mockBaseLayerName = 'Carto Dark';
    actions = [{ command: 'base_layer_change', params: { name: 'Carto Dark' } }];
    const map = makeMockMaplibreMap();
    mockGetMap.mockImplementation(() => map);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockSetSelectedBaseLayer).not.toHaveBeenCalled();
    expect(mockSetBaseLayer).not.toHaveBeenCalled();
    expect(map.once).not.toHaveBeenCalledWith('style.load', expect.any(Function));
    expect(reportTerminalFn).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'base_layer_change' }),
      'succeeded',
      expect.any(Object),
    );
    expect(popAction).toHaveBeenCalled();
  });
});
