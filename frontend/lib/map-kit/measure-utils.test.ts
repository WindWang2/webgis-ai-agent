import { describe, it, expect } from 'vitest';
import {
  haversineDistance,
  measure,
  polygonAreaKm2,
  formatDistance,
} from './navigation';

describe('haversineDistance', () => {
  it('returns ~111km for 1 degree of latitude', () => {
    const d = haversineDistance([0, 0], [0, 1]);
    expect(d).toBeCloseTo(111.2, 0);
  });

  it('returns 0 for same point', () => {
    expect(haversineDistance([116.4, 39.9], [116.4, 39.9])).toBe(0);
  });
});

describe('measure', () => {
  it('computes distance between two points', () => {
    const result = measure(
      null as any,
      [[116.4, 39.9], [121.47, 31.23]],
      'distance'
    );
    expect(result.success).toBe(true);
    // Beijing to Shanghai ~1068 km
    expect(result.data).toBeCloseTo(1068, -1);
  });

  it('computes multi-point distance', () => {
    const result = measure(
      null as any,
      [[0, 0], [0, 1], [1, 1]],
      'distance'
    );
    expect(result.success).toBe(true);
    expect(result.data).toBeGreaterThan(111);
  });

  it('returns error for less than 2 points', () => {
    const result = measure(null as any, [[0, 0]], 'distance');
    expect(result.success).toBe(false);
  });

  it('computes polygon area', () => {
    // ~1 degree square near equator ≈ ~12300 km²
    const result = measure(
      null as any,
      [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
      'area'
    );
    expect(result.success).toBe(true);
    expect(result.data).toBeCloseTo(12363, -1);
  });
});

describe('polygonAreaKm2', () => {
  it('computes area of a unit square near equator', () => {
    const area = polygonAreaKm2([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]);
    expect(area).toBeCloseTo(12363, -1);
  });
});

describe('formatDistance', () => {
  it('formats meters for short distances', () => {
    expect(formatDistance(0.5)).toMatch(/m/);
  });

  it('formats km for longer distances', () => {
    expect(formatDistance(15)).toMatch(/km/);
  });
});
