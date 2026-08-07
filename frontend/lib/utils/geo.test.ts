import { describe, it, expect } from 'vitest';
import { bboxToFlyTo, isValidBbox, geometryBBox, bboxIntersects, filterFeaturesByBounds } from './geo';

describe('bboxToFlyTo', () => {
  it('returns zoom 4 for continent-scale bbox (maxDiff > 10)', () => {
    const result = bboxToFlyTo([0, 0, 20, 15]);
    expect(result.zoom).toBe(4);
    expect(result.center).toEqual([10, 7.5]);
  });

  it('returns zoom 8 for country-scale bbox (1 < maxDiff <= 10)', () => {
    const result = bboxToFlyTo([116, 39, 121, 42]);
    expect(result.zoom).toBe(8);
    expect(result.center).toEqual([118.5, 40.5]);
  });

  it('returns zoom 11 for city-scale bbox (0.1 < maxDiff <= 1)', () => {
    const result = bboxToFlyTo([116.3, 39.8, 116.7, 40.1]);
    expect(result.zoom).toBe(11);
    expect(result.center[0]).toBeCloseTo(116.5);
    expect(result.center[1]).toBeCloseTo(39.95);
  });

  it('returns zoom 14 for neighborhood bbox (maxDiff <= 0.1)', () => {
    const result = bboxToFlyTo([116.39, 39.89, 116.41, 39.91]);
    expect(result.zoom).toBe(14);
  });

  it('threshold is strict: maxDiff exactly 1 yields zoom 11 (not 8)', () => {
    const result = bboxToFlyTo([0, 0, 1, 0.5]);
    expect(result.zoom).toBe(11);
  });

  it('throws when west >= east', () => {
    expect(() => bboxToFlyTo([120, 30, 110, 40])).toThrow();
  });

  it('throws when south >= north', () => {
    expect(() => bboxToFlyTo([110, 40, 120, 30])).toThrow();
  });

  it('throws on non-finite coordinates', () => {
    expect(() => bboxToFlyTo([NaN, 0, 10, 10])).toThrow();
    expect(() => bboxToFlyTo([0, Infinity, 10, 10])).toThrow();
  });
});

describe('isValidBbox', () => {
  it('returns true for valid bbox', () => {
    expect(isValidBbox([116, 39, 117, 40])).toBe(true);
  });

  it('returns false for non-array', () => {
    expect(isValidBbox('not an array')).toBe(false);
  });

  it('returns false for wrong length', () => {
    expect(isValidBbox([1, 2, 3])).toBe(false);
  });

  it('returns false when west >= east', () => {
    expect(isValidBbox([120, 30, 110, 40])).toBe(false);
  });

  it('returns false when south >= north', () => {
    expect(isValidBbox([110, 40, 120, 30])).toBe(false);
  });

  it('returns false for non-finite values', () => {
    expect(isValidBbox([NaN, 0, 10, 10])).toBe(false);
  });
});

describe('geometryBBox', () => {
  it('computes bbox for a Point', () => {
    expect(geometryBBox({ type: 'Point', coordinates: [116.4, 39.9] })).toEqual([116.4, 39.9, 116.4, 39.9]);
  });

  it('computes bbox for a Polygon (nested rings)', () => {
    const poly = {
      type: 'Polygon',
      coordinates: [[[0, 0], [10, 0], [10, 20], [0, 20], [0, 0]]],
    };
    expect(geometryBBox(poly as any)).toEqual([0, 0, 10, 20]);
  });

  it('computes bbox for a MultiPolygon (deeply nested)', () => {
    const mp = {
      type: 'MultiPolygon',
      coordinates: [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[5, 5], [8, 5], [8, 9], [5, 9], [5, 5]]],
      ],
    };
    expect(geometryBBox(mp as any)).toEqual([0, 0, 8, 9]);
  });

  it('returns null for null geometry', () => {
    expect(geometryBBox(null)).toBeNull();
    expect(geometryBBox(undefined)).toBeNull();
  });

  it('returns null for empty coordinates', () => {
    expect(geometryBBox({ type: 'Polygon', coordinates: [] })).toBeNull();
  });

  it('ignores non-finite coordinates', () => {
    expect(geometryBBox({ type: 'Point', coordinates: [NaN, NaN] })).toBeNull();
  });
});

describe('bboxIntersects', () => {
  it('returns true for overlapping boxes', () => {
    expect(bboxIntersects([0, 0, 10, 10], [5, 5, 15, 15])).toBe(true);
  });

  it('returns true for touching edges (inclusive)', () => {
    expect(bboxIntersects([0, 0, 10, 10], [10, 10, 20, 20])).toBe(true);
  });

  it('returns false for disjoint boxes', () => {
    expect(bboxIntersects([0, 0, 10, 10], [20, 20, 30, 30])).toBe(false);
  });

  it('returns true for containment', () => {
    expect(bboxIntersects([0, 0, 100, 100], [40, 40, 60, 60])).toBe(true);
  });
});

describe('filterFeaturesByBounds', () => {
  const fc = (n: number) => ({
    type: 'FeatureCollection' as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: 'Feature' as const,
      properties: { id: i },
      geometry: { type: 'Point', coordinates: [i, i] },  // points along y=x diagonal
    })),
  });

  it('returns input unchanged below minFilter threshold', () => {
    const small = fc(100);
    const out = filterFeaturesByBounds(small, [0, 0, 5, 5]);
    expect(out).toBe(small);  // same reference — no filtering
  });

  it('filters to viewport-intersecting features above threshold', () => {
    const big = fc(2000);  // points at (0,0)...(1999,1999)
    const out = filterFeaturesByBounds(big, [100, 100, 105, 105], 1000);
    expect(out.features.length).toBe(6);  // points 100..105
    expect(out.features[0].properties.id).toBe(100);
    expect(out.features[5].properties.id).toBe(105);
  });

  it('does not mutate the input', () => {
    const big = fc(2000);
    const origLen = big.features.length;
    filterFeaturesByBounds(big, [0, 0, 5, 5], 1000);
    expect(big.features.length).toBe(origLen);
  });

  it('returns empty collection when nothing intersects', () => {
    const big = fc(2000);
    const out = filterFeaturesByBounds(big, [5000, 5000, 6000, 6000], 1000);
    expect(out.features).toEqual([]);
  });

  it('drops features with null geometry', () => {
    const big = {
      type: 'FeatureCollection' as const,
      features: [
        ...Array.from({ length: 1500 }, (_, i) => ({
          type: 'Feature' as const, properties: { id: i },
          geometry: { type: 'Point', coordinates: [i, i] },
        })),
        { type: 'Feature' as const, properties: { id: 'null' }, geometry: null },
      ],
    };
    const out = filterFeaturesByBounds(big, [0, 0, 10, 10], 1000);
    expect(out.features.find((f) => f.properties.id === 'null')).toBeUndefined();
  });
});
