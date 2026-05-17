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

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    get actions() { return actions; },
    dispatchAction: dispatchActionFn,
    popAction,
    setSelectedBaseLayer: vi.fn(),
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
  TILE_PROVIDERS: [{ name: 'Carto Light', keywords: ['carto'] }],
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({
      layers: [],
      setPendingSystemMessage: vi.fn(),
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
