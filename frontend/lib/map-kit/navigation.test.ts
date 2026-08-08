import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flyTo, fitBounds, jumpTo, validateCoordinate, calculateBBox, calculateBBoxAsync, polygonAreaKm2 } from './navigation';
import type { ViewportParams } from './types';
import type { Map } from 'maplibre-gl';

describe('navigation', () => {
  let mockMap: Partial<Map>;

  beforeEach(() => {
    mockMap = {
      flyTo: vi.fn(),
      fitBounds: vi.fn(),
      jumpTo: vi.fn(),
    };
  });

  describe('validateCoordinate', () => {
    it('should return true for valid coordinates', () => {
      expect(validateCoordinate([0, 0])).toBe(true);
      expect(validateCoordinate([180, 90])).toBe(true);
      expect(validateCoordinate([-180, -90])).toBe(true);
    });

    it('should return false for invalid longitude', () => {
      expect(validateCoordinate([181, 0])).toBe(false);
      expect(validateCoordinate([-181, 0])).toBe(false);
    });

    it('should return false for invalid latitude', () => {
      expect(validateCoordinate([0, 91])).toBe(false);
      expect(validateCoordinate([0, -91])).toBe(false);
    });
  });

  describe('flyTo', () => {
    it('should call map.flyTo with correct parameters', () => {
      const params: ViewportParams = {
        center: [120, 30],
        zoom: 10,
        bearing: 45,
        pitch: 60,
      };
      flyTo(mockMap as Map, params);
      expect(mockMap.flyTo).toHaveBeenCalledWith({
        center: [120, 30],
        zoom: 10,
        bearing: 45,
        pitch: 60,
        duration: 1500,
      });
    });

    it('should throw error for invalid coordinates', () => {
      const params: ViewportParams = {
        center: [200, 30],
        zoom: 10,
      };
      expect(() => flyTo(mockMap as Map, params)).toThrow('Invalid coordinates');
    });
  });

  describe('fitBounds', () => {
    it('should call map.fitBounds with correct parameters', () => {
      const bbox: [number, number, number, number] = [110, 20, 130, 40];
      fitBounds(mockMap as Map, bbox, 50);
      expect(mockMap.fitBounds).toHaveBeenCalledWith(bbox, {
        padding: 50,
        duration: 1500,
      });
    });

    it('should throw error for invalid bbox', () => {
      const bbox: [number, number, number, number] = [200, 20, 130, 40];
      expect(() => fitBounds(mockMap as Map, bbox)).toThrow('Invalid coordinates in bbox');
    });
  });

  describe('jumpTo', () => {
    it('should call map.jumpTo with correct parameters', () => {
      const params: ViewportParams = {
        center: [120, 30],
        zoom: 10,
      };
      jumpTo(mockMap as Map, params);
      expect(mockMap.jumpTo).toHaveBeenCalledWith({
        center: [120, 30],
        zoom: 10,
      });
    });
  });

  describe('calculateBBox', () => {
    it('should return null for empty or null inputs', () => {
      expect(calculateBBox(null)).toBeNull();
      expect(calculateBBox({})).toBeNull();
      expect(calculateBBox({ type: 'FeatureCollection', features: [] })).toBeNull();
    });

    it('should use precomputed bbox if valid', () => {
      const geojson = {
        type: 'FeatureCollection',
        bbox: [100, 10, 120, 30],
        features: []
      };
      expect(calculateBBox(geojson)).toEqual([100, 10, 120, 30]);
    });

    it('should correctly calculate bbox for FeatureCollection and filter NaN/Infinity', () => {
      const geojson = {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: {
              type: 'Point',
              coordinates: [116.4, 39.9]
            }
          },
          {
            type: 'Feature',
            geometry: {
              type: 'LineString',
              coordinates: [[116.2, 39.8], [NaN, 40.0], [116.8, Infinity], [117.0, 40.2]]
            }
          }
        ]
      };
      const bbox = calculateBBox(geojson);
      expect(bbox).toEqual([116.2, 39.8, 117.0, 40.2]);
    });

    it('should calculate bbox asynchronously using calculateBBoxAsync', async () => {
      const geojson = {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]]
        }
      };
      const bbox = await calculateBBoxAsync(geojson);
      expect(bbox).toEqual([10, 10, 20, 20]);
    });
  });

  describe('polygonAreaKm2', () => {
    it('should return 0 for fewer than 3 coordinates', () => {
      expect(polygonAreaKm2([[0, 0], [1, 1]])).toBe(0);
    });

    it('should calculate equal area for closed and unclosed polygon coordinate arrays', () => {
      const unclosed: [number, number][] = [[116, 39], [117, 39], [117, 40], [116, 40]];
      const closed: [number, number][] = [[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]];

      const areaUnclosed = polygonAreaKm2(unclosed);
      const areaClosed = polygonAreaKm2(closed);

      expect(areaUnclosed).toBeGreaterThan(0);
      expect(areaUnclosed).toBeCloseTo(areaClosed, 5);
    });
  });
});
