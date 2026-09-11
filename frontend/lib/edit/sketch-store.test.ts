import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getSketchState,
  nextSketchId,
  replaceSketchFeatures,
  resetSketchStore,
  setSketchDraft,
  setSketchSelected,
  sketchFeatureCollection,
  subscribeSketch,
  updateSketchGeometry,
  type SketchFeature,
} from './sketch-store';

function feature(id: string, coordinates: [number, number][]): SketchFeature {
  return {
    id,
    type: 'Feature',
    geometry: { type: 'LineString', coordinates },
    properties: { kind: 'sketch_line' },
  };
}

describe('sketch-store', () => {
  afterEach(() => {
    resetSketchStore();
  });

  it('replace/update/draft/select round-trips and bumps the version', () => {
    const v0 = getSketchState().version;
    let notifications = 0;
    const unsub = subscribeSketch(() => { notifications += 1; });

    replaceSketchFeatures([feature('a', [[0, 0], [1, 1]])]);
    expect(getSketchState().features).toHaveLength(1);

    updateSketchGeometry('a', { type: 'LineString', coordinates: [[0, 0], [2, 2]] });
    expect(getSketchState().features[0].geometry).toEqual({
      type: 'LineString', coordinates: [[0, 0], [2, 2]],
    });

    setSketchDraft({ kind: 'polygon', coordinates: [[1, 1]] });
    expect(getSketchState().draft?.kind).toBe('polygon');

    setSketchSelected('a');
    expect(getSketchState().selectedFeatureId).toBe('a');

    expect(getSketchState().version).toBeGreaterThan(v0);
    expect(notifications).toBe(4);
    unsub();
  });

  it('sketchFeatureCollection mirrors the feature list', () => {
    replaceSketchFeatures([feature('a', [[0, 0], [1, 1]])]);
    const fc = sketchFeatureCollection();
    expect(fc.type).toBe('FeatureCollection');
    expect(fc.features[0].id).toBe('a');
  });

  it('resetSketchStore clears features/draft/selection', () => {
    replaceSketchFeatures([feature('a', [[0, 0], [1, 1]])]);
    setSketchDraft({ kind: 'line', coordinates: [[0, 0]] });
    setSketchSelected('a');
    resetSketchStore();
    const s = getSketchState();
    expect(s.features).toHaveLength(0);
    expect(s.draft).toBeNull();
    expect(s.selectedFeatureId).toBeNull();
  });

  it('nextSketchId is unique', () => {
    expect(nextSketchId()).not.toBe(nextSketchId());
  });

  it('updateSketchGeometry on missing id is a no-op', () => {
    replaceSketchFeatures([feature('a', [[0, 0], [1, 1]])]);
    const before = getSketchState().features;
    updateSketchGeometry('missing', { type: 'LineString', coordinates: [] });
    expect(getSketchState().features).toBe(before);
    void vi;
  });
});
