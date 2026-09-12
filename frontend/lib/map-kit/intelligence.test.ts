import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setLayerFilter } from './renderer';
import { measure } from './navigation';
import type { Map } from 'maplibre-gl';

describe('MapKit Intelligence', () => {
  let mockMap: any;

  beforeEach(() => {
    mockMap = {
      queryRenderedFeatures: vi.fn(),
      getLayer: vi.fn(),
      setFilter: vi.fn(),
    };
  });

  describe('setLayerFilter', () => {
    it('should set filter on layer', () => {
      mockMap.getLayer.mockReturnValue({});
      const filter = ['>', ['get', 'pop'], 1000];
      
      setLayerFilter(mockMap, 'test-layer', filter);
      
      expect(mockMap.setFilter).toHaveBeenCalledWith('test-layer', filter);
    });

    it('should throw error if layer not found', () => {
      mockMap.getLayer.mockReturnValue(null);
      expect(() => setLayerFilter(mockMap, 'invalid', [])).toThrow("Layer 'invalid' not found.");
    });
  });

  describe('measure', () => {
    it('should calculate distance between points', () => {
      const coords: [number, number][] = [
        [0, 0],
        [1, 1]
      ];
      // Distance between (0,0) and (1,1) is approx 157.25 km
      const result = measure(mockMap as Map, coords, 'distance');
      
      expect(result.success).toBe(true);
      expect(result.data).toBeGreaterThan(150);
      expect(result.data).toBeLessThan(165);
      expect(result.summary).toContain("Total distance");
    });

    it('should return error for less than 2 points', () => {
      const result = measure(mockMap as Map, [[0, 0]], 'distance');
      expect(result.success).toBe(false);
      expect(result.summary).toBe("At least two points are required for measurement.");
    });
  });
});
