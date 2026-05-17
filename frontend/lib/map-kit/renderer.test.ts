import { describe, it, expect, vi, beforeEach } from 'vitest';
import { addGeoJsonSource } from './renderer';

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
});
