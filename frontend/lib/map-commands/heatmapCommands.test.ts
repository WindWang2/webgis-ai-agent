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

    it('#533 accepts the backend-authored image URL shape and fits the exact bbox', () => {
      // 后端 _author_raster_display_result 发射的形状：
      // params = {layerId, result_ref, mapspec_fingerprint, bbox, image: URL}
      const map = makeMockMaplibreMap();
      const urlParams = {
        layerId: 'raster-heat_1-source',
        result_ref: 'ref:raster/raster-heat_1-source',
        mapspec_fingerprint: 'carto-sha256:abc',
        image: '/api/v1/sessions/s1/raster/raster-heat_1-source.png',
        bbox: [116.5, 39.5, 117.5, 40.5],
      };
      // 校验器（requiredParams）必须放行 URL 形态
      expect(heatmapCommands.add_heatmap_raster.requiredParams(urlParams)).toBe(true);

      const result = heatmapCommands.add_heatmap_raster.run(makeCtx(map, urlParams));

      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
      expect(map.getSource('custom-raster-heat_1-source')).toBeTruthy();
      expect(map.getLayer('custom-raster-heat_1-source')).toBeTruthy();
      // fitBounds 收到与后端 bbox 完全一致的 4 元素数值数组（数值真值，非仅 ack）
      const fitCalls = (map.fitBounds as ReturnType<typeof vi.fn>).mock.calls;
      expect(fitCalls.length).toBeGreaterThan(0);
      expect(fitCalls[0][0]).toEqual([116.5, 39.5, 117.5, 40.5]);
    });

    it('requiredParams rejects params without url/image (validator contract)', () => {
      expect(heatmapCommands.add_heatmap_raster.requiredParams({ bbox: [1, 2, 3, 4] })).toBe(false);
      expect(heatmapCommands.add_heatmap_raster.requiredParams({})).toBe(false);
      expect(heatmapCommands.add_heatmap_raster.requiredParams({ url: 'x' })).toBe(true);
      expect(heatmapCommands.add_heatmap_raster.requiredParams({ image: 'x' })).toBe(true);
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

    it('#611: backend template emission — heatPalette/field/intensity are consumed and the id is stable across re-applies (no Date.now() stacking)', () => {
      // app/tools/templates.py apply_template heatmap variant 发射的形状：
      // params = {geojson, field, intensity, radius, heatPalette}（无 layerId）
      const map = makeMockMaplibreMap();
      const backendParams = {
        geojson: { type: 'FeatureCollection', features: [] },
        field: 'pop',
        intensity: 0.6,
        radius: 25,
        heatPalette: ['#0000ff', '#00ff00', '#ffff00', '#ff0000'],
      };

      const result = heatmapCommands.add_native_heatmap.run(makeCtx(map, backendParams));
      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });

      const id = 'custom-native-heatmap-pop';
      expect(map.getSource(id)).toBeTruthy();
      const layer = map.getLayer(id);
      expect(layer).toBeTruthy();
      // 后端一等参数 intensity 进了 heatmap paint
      expect(layer.paint['heatmap-intensity']).toBe(0.6);
      // heatPalette 颜色数组进了 heatmap-color（不再是默认 classic 调色板）
      expect(JSON.stringify(layer.paint['heatmap-color'])).toContain('#0000ff');
      expect(JSON.stringify(layer.paint['heatmap-color'])).not.toContain('33,102,172');

      // 同模板应用两次 → 稳定 id 原地更新：source 复用（setData），不叠层
      const srcAddsBefore = map._calls.addSource.length;
      const result2 = heatmapCommands.add_native_heatmap.run(makeCtx(map, backendParams));
      expect(result2).toEqual({ status: 'succeeded', result: { confirmed: true } });
      expect(map._calls.addSource.length).toBe(srcAddsBefore);
      expect(map._layers.filter((l: any) => l.id === id).length).toBe(1);
    });

    it('#611: older palette named-key callers still work (fallback path)', () => {
      const map = makeMockMaplibreMap();
      const result = heatmapCommands.add_native_heatmap.run(
        makeCtx(map, { geojson: { type: 'FeatureCollection', features: [] }, field: 'pop', palette: 'viridis', intensity: 1.2 }),
      );
      expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
      const layer = map.getLayer('custom-native-heatmap-pop');
      expect(JSON.stringify(layer.paint['heatmap-color'])).toContain('68,1,84'); // viridis 首色
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
