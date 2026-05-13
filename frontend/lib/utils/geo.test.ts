import { describe, it, expect } from 'vitest';
import { bboxToFlyTo, isValidBbox } from './geo';

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
