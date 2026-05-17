import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flyTo, fitBounds, jumpTo, validateCoordinate } from './navigation';
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
});
