import { describe, it, expect } from 'vitest';
import { compileStyleMethod, compileMapSpec } from './compiler';

describe('compileStyleMethod fallback for missing stops/cases (#935)', () => {
  it('interpolate with missing stops returns default or 0 instead of throwing', () => {
    const malformed: any = { method: 'interpolate', field: 'elevation' };
    expect(() => compileStyleMethod(malformed)).not.toThrow();
    expect(compileStyleMethod(malformed)).toBe(0);
  });

  it('interpolate with missing stops and explicit default returns default', () => {
    const malformed: any = { method: 'interpolate', field: 'elevation', default: '#ff0000' };
    expect(compileStyleMethod(malformed)).toBe('#ff0000');
  });

  it('interpolate with empty stops array returns fallback', () => {
    const malformed: any = { method: 'interpolate', field: 'elevation', stops: [] };
    expect(compileStyleMethod(malformed)).toBe(0);
  });

  it('step with missing stops already returns default/0 (regression)', () => {
    const m: any = { method: 'step', field: 'score', stops: undefined };
    expect(compileStyleMethod(m)).toBe(0);
    expect(compileStyleMethod({ method: 'step', field: 'score', stops: [], default: '#ffffb2' } as any)).toBe('#ffffb2');
  });

  it('match with missing cases returns default or empty string instead of throwing', () => {
    const malformed: any = { method: 'match', field: 'zone_type' };
    expect(() => compileStyleMethod(malformed)).not.toThrow();
    expect(compileStyleMethod(malformed)).toBe('');
  });

  it('match with missing cases and explicit default returns default', () => {
    const malformed: any = { method: 'match', field: 'zone_type', default: '#00ff00' };
    expect(compileStyleMethod(malformed)).toBe('#00ff00');
  });

  it('match with empty cases array returns fallback', () => {
    const malformed: any = { method: 'match', field: 'zone_type', cases: [] };
    expect(compileStyleMethod(malformed)).toBe('');
  });

  it('compileMapSpec with incomplete interpolate does not throw and emits fallback paint', () => {
    const spec: any = {
      version: '1.0',
      sources: { elev: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
      layers: [{ id: 'terrain_layer', source: 'elev', type: 'circle', paint: { color: { method: 'interpolate', field: 'elevation' } } }],
    };
    expect(() => compileMapSpec(spec)).not.toThrow();
    const res = compileMapSpec(spec);
    expect(res.style.layers[0].paint['circle-color']).toBe(0);
    // legends should not crash either
    expect(res.legend).toEqual([]);
  });

  it('compileMapSpec with incomplete match does not throw and emits fallback', () => {
    const spec: any = {
      version: '1.0',
      sources: { s: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
      layers: [{ id: 'l', source: 's', type: 'fill', paint: { color: { method: 'match', field: 'zone_type' } } }],
    };
    expect(() => compileMapSpec(spec)).not.toThrow();
    const res = compileMapSpec(spec);
    expect(res.style.layers[0].paint['fill-color']).toBe('');
  });
});
