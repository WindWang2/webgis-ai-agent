import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { MapActionHandler } from './map-action-handler';

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
};

const mockGetMap = vi.fn(() => mapMockInstance);

let popAction: ReturnType<typeof vi.fn>;
let dispatchActionFn: ReturnType<typeof vi.fn>;
let actions: Array<{ command: string; params: Record<string, unknown> }>;
const mockSetSelectedBaseLayer = vi.fn();

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    get actions() { return actions; },
    dispatchAction: dispatchActionFn,
    popAction,
    setSelectedBaseLayer: mockSetSelectedBaseLayer,
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
const mockAddAnnotation = vi.fn((feature) => {
  mockAnnotationsStore.push(feature);
});
const mockClearAnnotations = vi.fn(() => {
  mockAnnotationsStore = [];
});
let mockLayersStore: Array<{ id: string; name?: string; style?: any }> = [];
let mockAnnotationsStore: any[] = [];

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({
      get layers() { return mockLayersStore; },
      setBaseLayer: mockSetBaseLayer,
      setPendingSystemMessage: mockSetPendingSystemMessage,
      removeLayer: mockRemoveLayer,
      updateLayer: mockUpdateLayer,
      get annotations() { return mockAnnotationsStore; },
      addAnnotation: mockAddAnnotation,
      clearAnnotations: mockClearAnnotations,
    }),
  },
}));

describe('MapActionHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    actions = [];
    popAction = vi.fn();
    dispatchActionFn = vi.fn((action) => { actions = [action]; });
    mockLayersStore = [];
    mockAnnotationsStore = [];
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

    const map = mockGetMap();

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.addSource).toHaveBeenCalledWith('custom-test-layer', expect.anything());
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'custom-test-layer', source: 'custom-test-layer' }),
      undefined
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

    const map = mockGetMap();

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

  it('calls removeLayerStack when executing remove_layer', async () => {
    actions = [{
      command: 'remove_layer',
      params: { layerId: 'target-layer' },
    }];

    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [{ id: 'custom-target-layer' }]
    });
    (map.getSource as any).mockReturnValue(true);

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.removeLayer).toHaveBeenCalledWith('custom-target-layer');
    expect(map.removeSource).toHaveBeenCalledWith('custom-target-layer');
    expect(mockRemoveLayer).toHaveBeenCalledWith('target-layer');
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
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [{ id: 'custom-vis-layer-fill' }]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockUpdateLayer).toHaveBeenCalledWith('vis-layer', { visible: false, opacity: 0.5 });
  });

  it('handles LAYER_STYLE_UPDATE correctly', async () => {
    actions = [{
      command: 'LAYER_STYLE_UPDATE',
      params: { layer_id: 'style-layer', style: { color: '#00ff00', strokeWidth: 2 } },
    }];

    mockLayersStore = [{ id: 'style-layer', name: 'Style Layer', style: { color: '#ff0000' } }];
    const map = mockGetMap();
    (map.getStyle as any).mockReturnValue({
      layers: [{ id: 'custom-style-layer-fill' }]
    });

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(mockUpdateLayer).toHaveBeenCalledWith('style-layer', { style: { color: '#00ff00', strokeWidth: 2 } });
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

  it('handles APPLY_LAYER_FILTER correctly with parsed filter array', async () => {
    actions = [{
      command: 'APPLY_LAYER_FILTER',
      params: { layer_id: 'custom-layer', filter: '["==", "density", 10]' },
    }];

    const map = mockGetMap();

    await act(async () => {
      render(<MapActionHandler />);
    });

    expect(map.setFilter).toHaveBeenCalledWith('custom-layer', ['==', 'density', 10]);
  });
});
