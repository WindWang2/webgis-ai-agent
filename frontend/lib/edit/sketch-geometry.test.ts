import { describe, expect, it } from 'vitest';
import {
  closeRing,
  countSketchVertices,
  draftToFeature,
  geometryVertices,
  snapToVertex,
} from './sketch-geometry';

describe('sketch-geometry', () => {
  it('geometryVertices walks point/line/polygon shapes', () => {
    expect(geometryVertices({ type: 'Point', coordinates: [1, 2] })).toEqual([[1, 2]]);
    expect(
      geometryVertices({ type: 'LineString', coordinates: [[1, 2], [3, 4]] }),
    ).toEqual([[1, 2], [3, 4]]);
    expect(
      geometryVertices({
        type: 'Polygon',
        coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]], [[5, 5], [6, 5], [6, 6], [5, 5]]],
      }),
    ).toEqual([[0, 0], [1, 0], [1, 1], [0, 0], [5, 5], [6, 5], [6, 6], [5, 5]]);
  });

  it('closeRing appends the first coordinate only when open', () => {
    expect(closeRing([[0, 0], [1, 0], [1, 1]])).toEqual([[0, 0], [1, 0], [1, 1], [0, 0]]);
    expect(closeRing([[0, 0], [1, 0], [1, 1], [0, 0]])).toEqual([[0, 0], [1, 0], [1, 1], [0, 0]]);
    expect(closeRing([[0, 0]])).toEqual([[0, 0]]);
  });

  it('draftToFeature enforces minimum vertex counts', () => {
    expect(draftToFeature('line', [[0, 0]], 'x')).toBeNull();
    expect(draftToFeature('line', [[0, 0], [1, 1]], 'x')?.geometry).toEqual({
      type: 'LineString',
      coordinates: [[0, 0], [1, 1]],
    });
    expect(draftToFeature('polygon', [[0, 0], [1, 1]], 'x')).toBeNull();
    const poly = draftToFeature('polygon', [[0, 0], [1, 0], [1, 1]], 'x');
    expect(poly?.geometry).toEqual({
      type: 'Polygon',
      coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]],
    });
    expect(poly?.properties.kind).toBe('sketch_polygon');
  });

  it('snapToVertex hits the nearest candidate within the pixel budget', () => {
    // 单位投影：1° = 100px
    const project = (lngLat: [number, number]) => ({ x: lngLat[0] * 100, y: lngLat[1] * 100 });
    const unproject = (p: { x: number; y: number }) => ({ lng: p.x / 100, lat: p.y / 100 });
    const candidates: [number, number][] = [[1, 1], [5, 5]];
    expect(snapToVertex([1.05, 1.02], candidates, project, unproject, 12)?.coordinate).toEqual([1, 1]);
    expect(snapToVertex([1.5, 1.5], candidates, project, unproject, 12)).toBeNull();
    // 超出预算不吸附
    expect(snapToVertex([1.5, 1.5], candidates, project, unproject, 10)).toBeNull();
  });

  it('countSketchVertices sums across features', () => {
    const features = [
      { id: 'a', type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: [0, 0] }, properties: {} },
      { id: 'b', type: 'Feature' as const, geometry: { type: 'LineString' as const, coordinates: [[0, 0], [1, 1]] }, properties: {} },
    ];
    expect(countSketchVertices(features)).toBe(3);
  });
});
