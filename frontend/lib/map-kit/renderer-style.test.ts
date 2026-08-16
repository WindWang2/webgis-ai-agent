import { describe, it, expect, vi } from 'vitest';

// Mock maplibre Map
function createMockMap(layerType: string = 'fill') {
  const paintProps: Record<string, any> = {};
  const layoutProps: Record<string, any> = {};
  return {
    getLayer: () => ({ type: layerType }),
    getPaintProperty: (id: string, prop: string) => paintProps[prop],
    setPaintProperty: vi.fn((id: string, prop: string, value: any) => { paintProps[prop] = value; }),
    setLayoutProperty: vi.fn((id: string, prop: string, value: any) => { layoutProps[prop] = value; }),
  };
}

// We'll import after the mock is ready
import { updateLayerStyle, raiseCustomOverlayLayers, CUSTOM_OVERLAY_PREFIX } from './renderer';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';

describe('updateLayerStyle expanded properties', () => {
  it('sets stroke color on fill layer', () => {
    const map = createMockMap('fill');
    updateLayerStyle(map as any, 'test-layer', {
      color: '#ff0000',
      strokeColor: '#000000',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-color', '#ff0000');
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-outline-color', '#000000');
  });

  it('sets point size on circle layer', () => {
    const map = createMockMap('circle');
    updateLayerStyle(map as any, 'test-layer', {
      pointSize: 8,
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-radius', 8);
  });

  it('sets dash array on line layer', () => {
    const map = createMockMap('line');
    updateLayerStyle(map as any, 'test-layer', {
      dashArray: 'dashed',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'line-dasharray', [4, 2]);
  });

  it('sets stroke color on circle layer', () => {
    const map = createMockMap('circle');
    updateLayerStyle(map as any, 'test-layer', {
      strokeColor: '#333333',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-stroke-color', '#333333');
  });
});

describe('raiseCustomOverlayLayers — post-reconcile z-band (#461)', () => {
  it('lifts every custom-* overlay back above the spec sublayers a reconcile stacked on top', () => {
    const map = makeMockMaplibreMap();
    // Imperative overlays (add_layer / heatmap / thematic map commands).
    map.addSource('custom-poi', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'custom-poi', type: 'circle', source: 'custom-poi' });
    map.addLayer({ id: 'custom-poi-label', type: 'symbol', source: 'custom-poi' });
    // Spec sublayers + basemap layers.
    map.addLayer({ id: 'basemap-raster', type: 'raster', source: 'raster-tiles' });
    map.addLayer({ id: 'poi__fill', type: 'fill', source: 'poi' });
    map.addLayer({ id: 'poi__point', type: 'circle', source: 'poi' });
    // A layer-changing reconcile buries the custom overlays at the top.
    // (syncLayerZOrder moves every spec sublayer to the top of the stack.)

    raiseCustomOverlayLayers(map as any);

    const order = map._layers.map((l: any) => l.id);
    // Both custom overlays sit above ALL spec sublayers…
    for (const specLayer of ['basemap-raster', 'poi__fill', 'poi__point']) {
      expect(order.indexOf('custom-poi')).toBeGreaterThan(order.indexOf(specLayer));
      expect(order.indexOf('custom-poi-label')).toBeGreaterThan(order.indexOf(specLayer));
    }
    // …and their insertion order (relative z among overlays) is preserved.
    expect(order.indexOf('custom-poi')).toBeLessThan(order.indexOf('custom-poi-label'));
  });

  it('is a cheap no-op when no custom-* overlays exist', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'poi__point', type: 'circle', source: 'poi' });
    raiseCustomOverlayLayers(map as any);
    expect(map._calls.moveLayer).toEqual([]);
    expect(map._layers.map((l: any) => l.id)).toEqual(['poi__point']);
  });

  it('skips ids the style no longer has (mid-reconcile vanish) without throwing', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'custom-gone', type: 'circle', source: 'custom-gone' });
    map.removeLayer('custom-gone');
    expect(() => raiseCustomOverlayLayers(map as any)).not.toThrow();
    expect(map._calls.moveLayer).toEqual([]);
  });

  it(`matches only the ${CUSTOM_OVERLAY_PREFIX} prefix (annotation/spec layers stay put)`, () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'poi__point', type: 'circle', source: 'poi' });
    map.addLayer({ id: 'claude-annotations-fill', type: 'fill', source: 'claude-annotations' });
    raiseCustomOverlayLayers(map as any);
    expect(map._calls.moveLayer).toEqual([]);
  });
});
