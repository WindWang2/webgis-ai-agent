import { describe, expect, it } from 'vitest';
import { anchorFractionInBounds, boundsFromCenterZoom, alignWrappedBounds, bboxToBounds } from './geo-anchor';

describe('Mercator viewport anchors (#1808)', () => {
  it.each([0, 40, 60])('projects the center to the exact viewport center at latitude %s', (lat) => {
    const bounds = boundsFromCenterZoom({ lng: 110, lat }, 10, 1200, 800)!;
    expect(bounds.east - 110).toBeCloseTo(1200 / (512 * 2 ** 10) * 180, 8);
    expect(anchorFractionInBounds({ lng: 110, lat }, bounds)).toMatchObject({ fx: 0.5 });
    expect(anchorFractionInBounds({ lng: 110, lat }, bounds)!.fy).toBeCloseTo(0.5, 10);
    const north = anchorFractionInBounds({ lng: 110, lat: bounds.north }, bounds)!;
    expect(north.fy).toBeCloseTo(1);
  });
});

it('aligns wrapped inset locations and the main viewport into one world copy', () => {
  const inset = { west: 170, south: -20, east: 190, north: -10 };
  expect(bboxToBounds([170, -20, -170, -10])).toEqual(inset);
  expect(anchorFractionInBounds([-175, -15], inset)?.fx).toBeCloseTo(0.75);
  expect(alignWrappedBounds({ west: -179, south: -18, east: -175, north: -12 }, inset))
    .toEqual({ west: 181, south: -18, east: 185, north: -12 });
});
