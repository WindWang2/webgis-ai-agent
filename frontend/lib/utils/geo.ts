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

// ---------------------------------------------------------------------------
// Viewport (bbox) feature filtering — perf optimization for large inline
// GeoJSON sources. MapLibre internally tile-culls rendered features, but the
// up-front ``GeoJSONSource.setData(data)`` parses the *entire* FeatureCollection
// into an internal spatial index every call. For a 100k-feature layer this is
// ~100ms of main-thread jank per setData. Pre-filtering to the visible bounds
// before setData cuts both the parse cost and the index size.
//
// ``filterFeaturesByBounds`` is a pure function (no maplibre dep) so it can run
// in the reconciler worker and is trivially testable.
// ---------------------------------------------------------------------------

interface FeatureLike {
  type: 'Feature';
  geometry?: { type: string; coordinates: any } | null;
  properties?: any;
}

interface FeatureCollectionLike {
  type: 'FeatureCollection';
  features: FeatureLike[];
}

/**
 * Compute the bounding box [west, south, east, north] of a GeoJSON geometry.
 * Returns null for empty/invalid geometries. Recurses through all coordinate
 * nesting depths (Polygon/MultiPolygon/LineString/Point + collections).
 */
export function geometryBBox(geometry: { type: string; coordinates: any } | null | undefined): [number, number, number, number] | null {
  if (!geometry || !geometry.coordinates) return null;
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
  let found = false;

  const visit = (node: any): void => {
    if (typeof node === 'number') return; // shouldn't reach a bare number here
    if (Array.isArray(node)) {
      if (node.length >= 2 && typeof node[0] === 'number' && typeof node[1] === 'number') {
        const [x, y] = node;
        if (isFinite(x) && isFinite(y)) {
          if (x < west) west = x;
          if (x > east) east = x;
          if (y < south) south = y;
          if (y > north) north = y;
          found = true;
        }
        return;
      }
      for (const child of node) visit(child);
    }
  };
  visit(geometry.coordinates);
  return found ? [west, south, east, north] : null;
}

/**
 * Test whether two bounding boxes (axis-aligned) intersect. Touching edges
 * count as intersection (inclusive on all four sides) so viewport-edge features
 * are never dropped.
 */
export function bboxIntersects(
  a: [number, number, number, number],
  b: [number, number, number, number],
): boolean {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

/**
 * Filter a GeoJSON FeatureCollection to features whose geometry bounding box
 * intersects the viewport bounds. Returns a *new* FeatureCollection; the input
 * is not mutated. Features with null/empty geometry or unparsable coordinates
 * are dropped (they have no on-screen extent).
 *
 * Use this before ``GeoJSONSource.setData`` on large inline sources. Recompute
 * on ``moveend`` when implementing viewport-driven incremental loading.
 *
 * @param fc        The FeatureCollection to filter.
 * @param viewport  [west, south, east, north] of the visible map area.
 * @param minFilter Skip filtering below this feature count — for small layers
 *                  the bbox computation overhead outweighs the setData savings.
 *                  Default 1000.
 */
export function filterFeaturesByBounds(
  fc: FeatureCollectionLike,
  viewport: [number, number, number, number],
  minFilter = 1000,
): FeatureCollectionLike {
  if (!fc || !Array.isArray(fc.features)) return fc;
  // Below the threshold, filtering overhead isn't worth it — return as-is.
  if (fc.features.length < minFilter) return fc;
  const kept: FeatureLike[] = [];
  for (const feat of fc.features) {
    const bbox = geometryBBox(feat.geometry);
    if (bbox && bboxIntersects(bbox, viewport)) {
      kept.push(feat);
    }
  }
  return { type: 'FeatureCollection', features: kept };
}
