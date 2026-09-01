/**
 * Filter fast path（§13）：filter-only 层变化 → kind "filter"；
 * source 更新仍然 recompile；其它变化仍然 recompile。
 */
import { describe, expect, it } from 'vitest';
import { diffSpecs } from './reconciler';
import type { MapSpec, MapSpecLayer } from './types';

function spec(layers: MapSpecLayer[], sources: MapSpec['sources'] = {}): MapSpec {
  return { version: '1.0', sources, layers };
}

const baseLayer = (id: string, filter?: unknown[]): MapSpecLayer => ({
  id, source: 's1', type: 'circle',
  paint: { 'circle-color': '#111' } as never,
  layout: { visibility: 'visible' },
  ...(filter ? { filter: filter as never } : {}),
});

describe('diffSpecs filter fast path', () => {
  it('classifies a filter-only change as kind "filter"', () => {
    const prev = spec([baseLayer('a', ['==', '$type', 'Point'])]);
    const next = spec([baseLayer('a', ['in', ['get', 'district'], ['literal', ['武侯区']]])]);
    const patch = diffSpecs(prev, next);
    expect(patch.layers).toHaveLength(1);
    expect(patch.layers[0].kind).toBe('filter');
    expect(patch.sources).toHaveLength(0);
  });

  it('filter added from nothing is also filter-only', () => {
    const prev = spec([baseLayer('a')]);
    const next = spec([baseLayer('a', ['==', '$type', 'Point'])]);
    expect(diffSpecs(prev, next).layers[0].kind).toBe('filter');
  });

  it('filter removed (→ undefined) is filter-only', () => {
    const prev = spec([baseLayer('a', ['==', '$type', 'Point'])]);
    const next = spec([baseLayer('a')]);
    expect(diffSpecs(prev, next).layers[0].kind).toBe('filter');
  });

  it('paint change stays recompile', () => {
    const prev = spec([baseLayer('a', ['==', '$type', 'Point'])]);
    const changed = { ...baseLayer('a', ['==', '$type', 'Point']), paint: { 'circle-color': '#222' } as never };
    expect(diffSpecs(prev, spec([changed])).layers[0].kind).toBe('recompile');
  });

  it('source update forces recompile even when only filter differs', () => {
    const prev = spec([baseLayer('a', ['==', '$type', 'Point'])], { s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } as never } });
    const next = spec([baseLayer('a', ['in', ['get', 'x'], ['literal', [1]]])], { s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [{ type: 'Feature' }] } as never } });
    const patch = diffSpecs(prev, next);
    expect(patch.sources[0].kind).toBe('update');
    expect(patch.layers[0].kind).toBe('recompile');
  });

  it('multiple sublayers classify independently', () => {
    const prev = spec([
      baseLayer('l__fill', ['==', '$type', 'Polygon']),
      baseLayer('l__point', ['==', '$type', 'Point']),
    ]);
    const next = spec([
      baseLayer('l__fill', ['==', '$type', 'Polygon']),
      baseLayer('l__point', ['in', ['get', 'district'], ['literal', ['武侯区']]]),
    ]);
    const patch = diffSpecs(prev, next);
    expect(patch.layers).toHaveLength(1);
    expect(patch.layers[0].id).toBe('l__point');
    expect(patch.layers[0].kind).toBe('filter');
  });
});
