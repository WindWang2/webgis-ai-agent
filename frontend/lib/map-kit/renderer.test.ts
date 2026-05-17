import { describe, it, expect, vi, beforeEach } from 'vitest';
import { 
  addGeoJsonSource, 
  addVectorLayer, 
  addNativeHeatmap, 
  removeLayerStack, 
  updateLayerStyle 
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
  });

  describe('updateLayerStyle', () => {
    it('should update visibility', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', { visibility: 'none' });
      expect(mapMock.setLayoutProperty).toHaveBeenCalledWith('test-layer', 'visibility', 'none');
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
