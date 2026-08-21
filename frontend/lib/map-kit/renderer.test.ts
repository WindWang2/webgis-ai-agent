import { describe, it, expect, vi, beforeEach } from 'vitest';
import { 
  addGeoJsonSource, 
  refreshGeoJsonSourcesByViewport,
  addVectorLayer, 
  addNativeHeatmap, 
  removeLayerStack, 
  updateLayerStyle,
  addThematicLayer,
  resolveHeatmapRadiusPx,
} from './renderer';

describe('renderer', () => {
  let mapMock: any;

  beforeEach(() => {
    mapMock = {
      getSource: vi.fn(),
      addSource: vi.fn(),
      addLayer: vi.fn(),
      getLayer: vi.fn(),
      removeLayer: vi.fn(),
      removeSource: vi.fn(),
      setLayoutProperty: vi.fn(),
      setPaintProperty: vi.fn(),
      getStyle: vi.fn(() => ({ layers: [] })),
    };
  });

  describe('addThematicLayer', () => {
    it('should construct a step expression for choropleth polygons', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addThematicLayer(mapMock, 'thematic-layer', { type: 'FeatureCollection', features: [] }, {
        type: 'choropleth',
        field: 'value',
        breaks: [10, 20],
        colors: ['#f00', '#0f0', '#00f'],
        geometry_type: 'Polygon'
      });

      expect(mapMock.addLayer).toHaveBeenCalledWith({
        id: 'thematic-layer',
        type: 'fill',
        source: 'thematic-layer',
        layout: {},
        paint: {
          'fill-color': ['step', ['get', 'value'], '#f00', 10, '#0f0', 20, '#00f'],
          'fill-opacity': 0.8
        }
      }, undefined);
    });

    it('should construct a step expression for choropleth points', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addThematicLayer(mapMock, 'thematic-layer-pts', { type: 'FeatureCollection', features: [] }, {
        type: 'choropleth',
        field: 'density',
        breaks: [5],
        colors: ['#111', '#222'],
        geometry_type: 'Point'
      });

      expect(mapMock.addLayer).toHaveBeenCalledWith({
        id: 'thematic-layer-pts',
        type: 'circle',
        source: 'thematic-layer-pts',
        layout: {},
        paint: {
          'circle-color': ['step', ['get', 'density'], '#111', 5, '#222'],
          'circle-radius': 6,
          'circle-opacity': 0.8
        }
      }, undefined);
    });

    it('should construct a match expression for lisa polygons', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addThematicLayer(mapMock, 'lisa-layer', { type: 'FeatureCollection', features: [] }, {
        type: 'lisa',
        field: 'cluster',
        categories: { 'HH': '#red', 'LL': '#blue' },
        geometry_type: 'Polygon'
      });

      expect(mapMock.addLayer).toHaveBeenCalledWith({
        id: 'lisa-layer',
        type: 'fill',
        source: 'lisa-layer',
        layout: {},
        paint: {
          'fill-color': ['match', ['get', 'cluster'], 'HH', '#red', 'LL', '#blue', '#cccccc'],
          'fill-opacity': 0.8
        }
      }, undefined);
    });

    it('#557 断点 3: categorical style (list categories) → match expression, numeric keys preserved', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addThematicLayer(mapMock, 'cat-layer', { type: 'FeatureCollection', features: [] }, {
        type: 'categorical',
        field: 'code',
        categories: [
          { key: 1, color: '#fca5a5', label: '1' },
          { key: 2, color: '#93c5fd', label: '2' },
        ],
        geometry_type: 'Polygon'
      });

      expect(mapMock.addLayer).toHaveBeenCalledWith({
        id: 'cat-layer',
        type: 'fill',
        source: 'cat-layer',
        layout: {},
        paint: {
          'fill-color': ['match', ['get', 'code'], 1, '#fca5a5', 2, '#93c5fd', '#cccccc'],
          'fill-opacity': 0.8
        }
      }, undefined);
    });
  });

  describe('addGeoJsonSource', () => {
    it('should add a new GeoJSON source if it does not exist', () => {
      mapMock.getSource.mockReturnValue(undefined);
      const data = { type: 'FeatureCollection', features: [] };
      addGeoJsonSource(mapMock, 'test-source', data);

      expect(mapMock.addSource).toHaveBeenCalledWith('test-source', {
        type: 'geojson',
        data
      });
    });

    it('should update existing GeoJSON source if it exists', () => {
      const sourceMock = { setData: vi.fn() };
      mapMock.getSource.mockReturnValue(sourceMock);
      const data = { type: 'FeatureCollection', features: [] };
      addGeoJsonSource(mapMock, 'test-source', data);

      expect(sourceMock.setData).toHaveBeenCalledWith(data);
      expect(mapMock.addSource).not.toHaveBeenCalled();
    });

    // ── Phase 8: viewport-driven filtering ────────────────────────────────

    function bigFC(n: number) {
      return {
        type: 'FeatureCollection',
        features: Array.from({ length: n }, (_, i) => ({
          type: 'Feature',
          properties: { id: i },
          geometry: { type: 'Point', coordinates: [i, i] }, // diagonal
        })),
      };
    }

    it('trims a large FeatureCollection to the viewport on add', () => {
      mapMock.getSource.mockReturnValue(undefined);
      const data = bigFC(2000); // points (0,0)..(1999,1999)
      addGeoJsonSource(mapMock, 'big', data, { viewport: [100, 100, 105, 105] });

      const added = mapMock.addSource.mock.calls[0][1];
      expect(added.data.features.length).toBe(6); // points 100..105 only
      // The raw (unfiltered) data must be retained for later re-filtering.
      expect(mapMock.addSource).toHaveBeenCalledTimes(1);
    });

    it('passes small collections through unchanged (F31 fast path preserved)', () => {
      mapMock.getSource.mockReturnValue(undefined);
      const data = bigFC(100); // below minFilter=1000
      addGeoJsonSource(mapMock, 'small', data, { viewport: [0, 0, 10, 10] });

      const added = mapMock.addSource.mock.calls[0][1];
      expect(added.data).toBe(data); // same reference — no copy
    });

    it('re-filters an existing source on viewport change (setData with trimmed data)', () => {
      const sourceMock = { setData: vi.fn() };
      mapMock.getSource.mockReturnValue(sourceMock);
      mapMock.getStyle.mockReturnValue({
        sources: { big: { type: 'geojson' } },
        layers: [],
      });

      const data = bigFC(2000);
      addGeoJsonSource(mapMock, 'big', data, { viewport: [0, 0, 10, 10] });
      expect(sourceMock.setData).toHaveBeenCalledTimes(1);
      expect(sourceMock.setData.mock.calls[0][0].features.length).toBe(11); // 0..10

      // Viewport moves elsewhere → setData re-runs with the new subset.
      refreshGeoJsonSourcesByViewport(mapMock, [1000, 1000, 1005, 1005]);
      expect(sourceMock.setData).toHaveBeenCalledTimes(2);
      expect(sourceMock.setData.mock.calls[1][0].features.length).toBe(6); // 1000..1005
    });

    it('does NOT re-setData when the viewport is unchanged (cached result)', () => {
      const sourceMock = { setData: vi.fn() };
      mapMock.getSource.mockReturnValue(sourceMock);
      mapMock.getStyle.mockReturnValue({
        sources: { big: { type: 'geojson' } },
        layers: [],
      });

      const data = bigFC(2000);
      addGeoJsonSource(mapMock, 'big', data, { viewport: [0, 0, 10, 10] });
      const callsAfterFirst = sourceMock.setData.mock.calls.length;

      // Same viewport (same floating-point bounds) → cache hit → no setData.
      refreshGeoJsonSourcesByViewport(mapMock, [0, 0, 10, 10]);
      expect(sourceMock.setData.mock.calls.length).toBe(callsAfterFirst);
    });

    it('skips non-inline sources (no raw data registered)', () => {
      const tileSource = { setData: vi.fn() };
      mapMock.getSource.mockImplementation((id: string) =>
        id === 'tiles' ? tileSource : undefined,
      );
      mapMock.getStyle.mockReturnValue({
        sources: {
          tiles: { type: 'vector', url: 'mapbox://x' },
          raster: { type: 'raster', tiles: ['https://x/{z}/{x}/{y}.png'] },
        },
        layers: [],
      });

      refreshGeoJsonSourcesByViewport(mapMock, [0, 0, 10, 10]);
      expect(tileSource.setData).not.toHaveBeenCalled();
    });
  });

  describe('addVectorLayer', () => {
    it('should add a new vector layer', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addVectorLayer(mapMock, {
        id: 'test-layer',
        source: 'test-source',
        type: 'fill',
        paint: { 'fill-color': '#ff0000' }
      });

      expect(mapMock.addLayer).toHaveBeenCalledWith({
        id: 'test-layer',
        source: 'test-source',
        type: 'fill',
        paint: { 'fill-color': '#ff0000' },
        layout: {}
      }, undefined);
    });

    it('should remove existing layer before adding if it exists', () => {
      mapMock.getLayer.mockReturnValue({});
      addVectorLayer(mapMock, {
        id: 'test-layer',
        source: 'test-source',
        type: 'line'
      });

      expect(mapMock.removeLayer).toHaveBeenCalledWith('test-layer');
      expect(mapMock.addLayer).toHaveBeenCalled();
    });
  });

  describe('addNativeHeatmap', () => {
    it('should add a heatmap layer with default palette', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, {
        id: 'heatmap-layer',
        source: 'test-source'
      });

      expect(mapMock.addLayer).toHaveBeenCalled();
      const layerArg = mapMock.addLayer.mock.calls[0][0];
      expect(layerArg.type).toBe('heatmap');
      expect(layerArg.paint['heatmap-color']).toBeDefined();
    });

    it('should use specified palette', () => {
      addNativeHeatmap(mapMock, {
        id: 'heatmap-layer',
        source: 'test-source',
        palette: 'viridis'
      });
      const layerArg = mapMock.addLayer.mock.calls[0][0];
      // Viridis starts with rgb(68,1,84)
      expect(JSON.stringify(layerArg.paint['heatmap-color'])).toContain('68,1,84');
    });

    it('#611: accepts a custom color array palette (backend heatPalette) spread across density stops', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, {
        id: 'heatmap-layer',
        source: 'test-source',
        palette: ['#0000ff', '#ff0000'],
        intensity: 0.6,
        radius: 20,
      });
      const layerArg = mapMock.addLayer.mock.calls[0][0];
      // 数组被均分铺到密度 0..1（interpolate stops），而不是落回默认 classic 调色板
      expect(layerArg.paint['heatmap-color']).toEqual([
        'interpolate',
        ['linear'],
        ['heatmap-density'],
        0, '#0000ff',
        1, '#ff0000',
      ]);
      // 一等参数 intensity/radius 直达 paint
      expect(layerArg.paint['heatmap-intensity']).toBe(0.6);
      expect(layerArg.paint['heatmap-radius']).toBe(20);
    });

    it('retunes classic stops to the gaussian density domain (单点峰值 0.4 落绿段，不再整片蓝)', () => {
      // MapLibre 累积 shader：val = weight·intensity·0.39894·exp(-4.5d²) ——
      // weight=1 的孤立点中心密度峰值只有 0.4。旧停靠点 (0.2..1 匀铺) 让稀疏区
      // 整片停在首段蓝色（用户实测「没有颜色分级」）；重标定后中间色压到
      // 0.25/0.45 段，任何缩放下都能看到蓝→绿→黄→红的多级渐变。
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, { id: 'heatmap-layer', source: 'test-source' });
      const layerArg = mapMock.addLayer.mock.calls[0][0];
      const expr = layerArg.paint['heatmap-color'] as unknown as any[];
      const stops = expr.slice(3);
      const positions = stops.filter((_, i) => i % 2 === 0);
      expect(positions).toContain(0.25);
      expect(positions).toContain(0.45);
      expect(positions[positions.length - 1]).toBe(1);
      // 多色相：首段蓝、尾段红（颜色 hex/rgb 字符串）
      expect(stops[1]).toContain('110,182');
      expect(stops[stops.length - 1]).toContain('235,40,40');
    });

    it('falls back to classic stops for unknown palette names (no undefined spread)', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, { id: 'heatmap-layer', source: 'test-source', palette: 'not-a-palette' });
      const expr = mapMock.addLayer.mock.calls[0][0].paint['heatmap-color'] as unknown as any[];
      // 7 停靠点 × 2 + 表头 3 项，而不是空 stops 的退化 interpolate
      expect(expr.length).toBe(3 + 14);
      expect(expr.slice(3).filter((_, i) => i % 2 === 0)).toContain(0.45);
    });

    it('defaults intensity to a zoom-interpolate boost when not supplied', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, { id: 'heatmap-layer', source: 'test-source' });
      const intensity = mapMock.addLayer.mock.calls[0][0].paint['heatmap-intensity'];
      // 放大后点距拉开、高斯重叠贡献变少 —— zoom 增益补偿，防止退回冷色
      expect(Array.isArray(intensity)).toBe(true);
      expect(intensity[0]).toBe('interpolate');
      expect(JSON.stringify(intensity)).toContain('zoom');
    });
  });

  describe('removeLayerStack', () => {
    it('should remove both layer and source if they exist', () => {
      mapMock.getLayer.mockReturnValue({});
      mapMock.getSource.mockReturnValue({});
      
      removeLayerStack(mapMock, 'test-id');
      
      expect(mapMock.removeLayer).toHaveBeenCalledWith('test-id');
      expect(mapMock.removeSource).toHaveBeenCalledWith('test-id');
    });

    it('should not try to remove layer/source if they do not exist', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      mapMock.getSource.mockReturnValue(undefined);
      
      removeLayerStack(mapMock, 'test-id');
      
      expect(mapMock.removeLayer).not.toHaveBeenCalled();
      expect(mapMock.removeSource).not.toHaveBeenCalled();
    });

    it('should remove all matching sub-layers when prefix is true', () => {
      mapMock.getStyle.mockReturnValue({
        layers: [
          { id: 'custom-foo' },
          { id: 'custom-foo-fill' },
          { id: 'custom-foo-stroke' },
          { id: 'custom-foobar-fill' }
        ]
      });
      mapMock.getSource.mockReturnValue({});

      removeLayerStack(mapMock, 'custom-foo', true);

      expect(mapMock.removeLayer).toHaveBeenCalledWith('custom-foo');
      expect(mapMock.removeLayer).toHaveBeenCalledWith('custom-foo-fill');
      expect(mapMock.removeLayer).toHaveBeenCalledWith('custom-foo-stroke');
      expect(mapMock.removeLayer).not.toHaveBeenCalledWith('custom-foobar-fill');
    });

    it('should detach dependent layers before removing sources and clean up image textures', () => {
      mapMock.hasImage = vi.fn().mockReturnValue(true);
      mapMock.removeImage = vi.fn();
      mapMock.getStyle.mockReturnValue({
        layers: [
          { id: 'custom-img-layer', source: 'custom-img' }
        ],
        sources: {
          'custom-img': {}
        }
      });
      mapMock.getLayer.mockReturnValue({});
      mapMock.getSource.mockReturnValue({});

      removeLayerStack(mapMock, 'custom-img', true);

      expect(mapMock.removeLayer).toHaveBeenCalledWith('custom-img-layer');
      expect(mapMock.removeSource).toHaveBeenCalledWith('custom-img');
      expect(mapMock.removeImage).toHaveBeenCalledWith('custom-img');
    });
  });

  describe('updateLayerStyle', () => {
    it('should update visibility', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', { visibility: 'none' });
      expect(mapMock.setLayoutProperty).toHaveBeenCalledWith('test-layer', 'visibility', 'none');
    });

    it('#557 断点 5: fillOpacity → fill-opacity paint (fill layer)', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', { fillOpacity: 0.4 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-opacity', 0.4);
    });

    it('#557 断点 3: categorical colorMap → match expression on fill-color', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', {
        categorical: { field: 'landuse', colorMap: { residential: '#fca5a5', commercial: '#93c5fd' } },
      });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-color', [
        'match', ['get', 'landuse'],
        'residential', '#fca5a5',
        'commercial', '#93c5fd',
        '#cccccc',
      ]);
    });

    it('#557 断点 3+5: categorical baseStyle.fillOpacity → fill-opacity too', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', {
        categorical: { field: 'landuse', colorMap: { R: '#f00' }, fillOpacity: 0.75 },
      });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-opacity', 0.75);
    });

    it('should update opacity based on layer type (fill)', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', { opacity: 0.8 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-opacity', 0.8);
    });

    it('should update opacity based on layer type (line)', () => {
      mapMock.getLayer.mockReturnValue({ type: 'line' });
      updateLayerStyle(mapMock, 'test-layer', { opacity: 0.5 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'line-opacity', 0.5);
    });

    it('should update opacity based on layer type (circle)', () => {
      mapMock.getLayer.mockReturnValue({ type: 'circle' });
      updateLayerStyle(mapMock, 'test-layer', { opacity: 0.3 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-opacity', 0.3);
    });

    it('should update opacity based on layer type (heatmap)', () => {
      mapMock.getLayer.mockReturnValue({ type: 'heatmap' });
      updateLayerStyle(mapMock, 'test-layer', { opacity: 0.9 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'heatmap-opacity', 0.9);
    });

    it('should do nothing if layer does not exist', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      updateLayerStyle(mapMock, 'test-layer', { visibility: 'none', opacity: 0.5 });
      expect(mapMock.setLayoutProperty).not.toHaveBeenCalled();
      expect(mapMock.setPaintProperty).not.toHaveBeenCalled();
    });
  });
});

// ── 热力半径契约（后端 heatmap_contract 前端镜像）────────────────────
describe('resolveHeatmapRadiusPx — heatmap radius contract mirror', () => {
  it('explicit radiusPx clamps to [4,80]', () => {
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radiusPx: 22 } as any)).toBe(22);
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radiusPx: 500 } as any)).toBe(80);
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radiusPx: 1 } as any)).toBe(4);
  });

  it('legacy meters (1000/2000) never render as pixels — default 30px', () => {
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radius: 1000 } as any)).toBe(30);
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radius: 2000 } as any)).toBe(30);
  });

  it('legacy 4-60 historical window passes through as px', () => {
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radius: 25 } as any)).toBe(25);
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radius: 61 } as any)).toBe(30);
  });

  it('explicit radiusPx wins over legacy radius', () => {
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's', radiusPx: 18, radius: 1500 } as any)).toBe(18);
  });

  it('nothing given → contract default 30px', () => {
    expect(resolveHeatmapRadiusPx({ id: 'x', source: 's' } as any)).toBe(30);
  });
});
