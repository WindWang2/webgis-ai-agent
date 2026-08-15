import { describe, expect, it, vi, beforeEach } from 'vitest';
import { heatmapCommands } from './heatmapCommands';
import type { MapCommandContext } from './types';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';

function makeCtx(map: any, params: Record<string, unknown> = {}): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({}),
    setSelectedBaseLayer: () => {},
    command: 'add_heatmap_raster',
    params,
  } as unknown as MapCommandContext;
}

/**
 * Issue #393: the three heatmap commands used to return void unconditionally —
 * the dispatcher converted the void into a `succeeded` ack even when the
 * renderer mount silently failed. They now verify the post-state (source +
 * layer present) before claiming success.
 */
describe('heatmap commands (issue #393: post-state verification, no fake success)', () => {
  beforeEach(() => vi.clearAllMocks());

  describe('add_heatmap_raster', () => {
    const params = {
      image: 'data:image/png;base64,AAAA',
      bbox: [116, 39, 117, 40],
      opacity: 0.5,
      layerId: 'rain',
    };

    it('acks confirmed:true only when the image source AND raster layer landed', () => {
      const map = makeMockMaplibreMap();
      const result = heatmapCommands.add_heatmap_raster.run(makeCtx(map, params));

      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
      expect(map.getSource('custom-rain')).toBeTruthy();
      expect(map.getLayer('custom-rain')).toBeTruthy();
    });

    it('fails mutation_failed when the map never registers the mount', () => {
      // getSource/getLayer always null — the renderer calls no-op → old code
      // returned void → dispatcher acked succeeded. Now it must fail honestly.
      const bareMap = {
        getSource: vi.fn(() => null),
        getLayer: vi.fn(() => null),
        addSource: vi.fn(),
        addLayer: vi.fn(),
        getStyle: vi.fn(() => ({ layers: [], sources: {} })),
        getCanvas: vi.fn(() => ({ width: 800, height: 600 })),
        fitBounds: vi.fn(),
        getCenter: vi.fn(() => ({ lng: 116, lat: 39 })),
        getZoom: vi.fn(() => 5),
      };
      const result = heatmapCommands.add_heatmap_raster.run(makeCtx(bareMap, params));

      expect(result).toEqual({ status: 'failed', error: 'mutation_failed' });
    });

    it('rejects missing payload data as invalid_params', () => {
      const map = makeMockMaplibreMap();
      expect(heatmapCommands.add_heatmap_raster.run(makeCtx(map, { bbox: [1, 2, 3, 4] })))
        .toEqual({ status: 'failed', error: 'invalid_params' });
      expect(heatmapCommands.add_heatmap_raster.run(makeCtx(map, { image: 'x' })))
        .toEqual({ status: 'failed', error: 'invalid_params' });
    });
  });

  describe('add_native_heatmap', () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    const params = { geojson, layerId: 'poi-heat', palette: 'magma', radius: 20 };

    it('acks confirmed:true only when the source AND heatmap layer landed', () => {
      const map = makeMockMaplibreMap();
      const result = heatmapCommands.add_native_heatmap.run(makeCtx(map, params));

      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
      expect(map.getSource('custom-poi-heat')).toBeTruthy();
      expect(map.getLayer('custom-poi-heat')).toBeTruthy();
    });

    it('fails mutation_failed when the mount did not land', () => {
      const bareMap = {
        getSource: vi.fn(() => null),
        getLayer: vi.fn(() => null),
        addSource: vi.fn(),
        addLayer: vi.fn(),
        removeLayer: vi.fn(),
        getStyle: vi.fn(() => ({ layers: [], sources: {} })),
      };
      const result = heatmapCommands.add_native_heatmap.run(makeCtx(bareMap, params));

      expect(result).toEqual({ status: 'failed', error: 'mutation_failed' });
    });

    it('rejects missing geojson as invalid_params', () => {
      expect(heatmapCommands.add_native_heatmap.run(makeCtx(makeMockMaplibreMap(), {})))
        .toEqual({ status: 'failed', error: 'invalid_params' });
    });
  });

  describe('create_thematic_map', () => {
    const geojson = { type: 'FeatureCollection', features: [] };
    const style = { type: 'choropleth', field: 'pop', breaks: [0, 100], colors: ['#fff', '#000'] };
    const params = { geojson, layerId: 'pop-theme', style, field: 'pop' };

    it('acks confirmed:true only when the source AND thematic layer landed', () => {
      const map = makeMockMaplibreMap();
      const result = heatmapCommands.create_thematic_map.run(makeCtx(map, params));

      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
      expect(map.getSource('custom-pop-theme')).toBeTruthy();
      expect(map.getLayer('custom-pop-theme')).toBeTruthy();
    });

    it('fails mutation_failed when the mount did not land', () => {
      const bareMap = {
        getSource: vi.fn(() => null),
        getLayer: vi.fn(() => null),
        addSource: vi.fn(),
        addLayer: vi.fn(),
        getStyle: vi.fn(() => ({ layers: [], sources: {} })),
      };
      const result = heatmapCommands.create_thematic_map.run(makeCtx(bareMap, params));

      expect(result).toEqual({ status: 'failed', error: 'mutation_failed' });
    });

    it('rejects missing geojson/style as invalid_params', () => {
      const map = makeMockMaplibreMap();
      expect(heatmapCommands.create_thematic_map.run(makeCtx(map, { style })))
        .toEqual({ status: 'failed', error: 'invalid_params' });
      expect(heatmapCommands.create_thematic_map.run(makeCtx(map, { geojson })))
        .toEqual({ status: 'failed', error: 'invalid_params' });
    });
  });
});
