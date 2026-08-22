/**
 * Shared meters-per-pixel helper — single source of truth for MapLibre's
 * 512px tile size (F-D-1 / #800).
 *
 * MapLibre's worldSize = tileSize(512) * 2**zoom, so ground resolution at
 * latitude φ and zoom z is:
 *   metersPerPixel = EARTH_CIRCUMFERENCE * cos(φ) / (512 * 2**z)
 * where EARTH_CIRCUMFERENCE = 40075016.686 m.
 *
 * Every consumer (live scale bars, export scale bar/graticule, query radius)
 * must import this instead of re-deriving with 256 or 2**(z+8).
 */
const EARTH_CIRCUMFERENCE = 40_075_016.686;

export function metersPerPixelAt(zoom: number, lat: number): number {
  return (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180)) / (512 * Math.pow(2, zoom));
}

/** Exposed for tests that want the canonical circumference. */
export const EARTH_CIRCUMFERENCE_M = EARTH_CIRCUMFERENCE;
