/**
 * Convert a bounding box to fly-to params.
 * Zoom thresholds use strict > — maxDiff === 1 yields zoom 11, not 8.
 * Throws on invalid bbox (non-finite coords, west >= east, south >= north).
 */
export function bboxToFlyTo(bbox: [number, number, number, number]): {
  center: [number, number];
  zoom: number;
} {
  const [west, south, east, north] = bbox;
  if (![west, south, east, north].every(isFinite)) {
    throw new Error('Invalid bbox: non-finite coordinate');
  }
  if (west >= east) throw new Error('Invalid bbox: west >= east');
  if (south >= north) throw new Error('Invalid bbox: south >= north');
  const maxDiff = Math.max(east - west, north - south);
  const zoom = maxDiff > 10 ? 4 : maxDiff > 1 ? 8 : maxDiff > 0.1 ? 11 : 14;
  return {
    center: [(west + east) / 2, (south + north) / 2],
    zoom,
  };
}

export function isValidBbox(bbox: unknown): bbox is [number, number, number, number] {
  if (!Array.isArray(bbox) || bbox.length !== 4) return false;
  const [west, south, east, north] = bbox as number[];
  return [west, south, east, north].every(isFinite) && west < east && south < north;
}
