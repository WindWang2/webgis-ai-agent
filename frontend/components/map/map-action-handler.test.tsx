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
  getCenter: vi.fn(() => ({ lat: 39.9 })),
  getZoom: vi.fn(() => 10),
  getCanvas: vi.fn(() => ({ width: 800, height: 600 })),
  getBearing: vi.fn(() => 0),
  once: vi.fn((_e: string, cb: () => void) => cb()),
  triggerRepaint: vi.fn(),
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
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({
      layers: [],
      setBaseLayer: mockSetBaseLayer,
      setPendingSystemMessage: mockSetPendingSystemMessage,
    }),
  },
}));

describe('MapActionHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    actions = [];
    popAction = vi.fn();
    dispatchActionFn = vi.fn((action) => { actions = [action]; });
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
});
