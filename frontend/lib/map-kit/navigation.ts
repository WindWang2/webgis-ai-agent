import type { Map, FlyToOptions, FitBoundsOptions } from 'maplibre-gl';
import type { ViewportParams, GeoAnalysisResult } from './types';

/**
 * Validates a coordinate pair [lng, lat].
 * Longitude must be between -180 and 180.
 * Latitude must be between -90 and 90.
 */
export function validateCoordinate(coord: [number, number]): boolean {
  const [lng, lat] = coord;
  return lng >= -180 && lng <= 180 && lat >= -90 && lat <= 90;
}

/**
 * Smoothly transitions the map to a new viewport.
 */
export function flyTo(map: Map, params: ViewportParams): void {
  if (!validateCoordinate(params.center)) {
    throw new Error('Invalid coordinates');
  }

  const options: FlyToOptions = {
    center: params.center,
    zoom: params.zoom,
    duration: 1500,
  };

  if (params.bearing !== undefined) options.bearing = params.bearing;
  if (params.pitch !== undefined) options.pitch = params.pitch;

  map.flyTo(options);
}

/**
 * Adjusts the map view to fit a bounding box.
 * bbox: [west, south, east, north]
 */
export function fitBounds(
  map: Map,
  bbox: [number, number, number, number],
  padding: number = 0
): void {
  const [west, south, east, north] = bbox;
  if (!validateCoordinate([west, south]) || !validateCoordinate([east, north])) {
    throw new Error('Invalid coordinates in bbox');
  }

  const options: FitBoundsOptions = {
    padding,
    duration: 1500,
  };

  map.fitBounds(bbox, options);
}

/**
 * Instantly changes the map viewport.
 */
export function jumpTo(map: Map, params: ViewportParams): void {
  if (!validateCoordinate(params.center)) {
    throw new Error('Invalid coordinates');
  }

  map.jumpTo({
    center: params.center,
    zoom: params.zoom,
    bearing: params.bearing,
    pitch: params.pitch,
  });
}

const _bboxCache = new WeakMap<object, [number, number, number, number]>();

/**
 * Calculates the bounding box of a GeoJSON object.
 * Returns [minLng, minLat, maxLng, maxLat] or null.
 *
 * Optimized single-pass traversal with WeakMap memoization and writeback.
 */
export function calculateBBox(geojson: any): [number, number, number, number] | null {
  if (!geojson || typeof geojson !== 'object') return null;

  // 1. Check WeakMap cache (fast O(1) for repeated calls on the same object)
  const cached = _bboxCache.get(geojson);
  if (cached) return cached;

  // 2. Fast path: check precomputed bbox if valid
  if (Array.isArray(geojson.bbox) && geojson.bbox.length === 4) {
    const [w, s, e, n] = geojson.bbox;
    if (Number.isFinite(w) && Number.isFinite(s) && Number.isFinite(e) && Number.isFinite(n)) {
      const validBBox: [number, number, number, number] = [w, s, e, n];
      _bboxCache.set(geojson, validBBox);
      return validBBox;
    }
  }

  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;
  let count = 0;

  function updateBounds(lng: unknown, lat: unknown) {
    if (typeof lng === 'number' && typeof lat === 'number' && Number.isFinite(lng) && Number.isFinite(lat)) {
      if (lng < minLng) minLng = lng;
      if (lat < minLat) minLat = lat;
      if (lng > maxLng) maxLng = lng;
      if (lat > maxLat) maxLat = lat;
      count++;
    }
  }

  function extract(node: any) {
    if (!node) return;
    if (Array.isArray(node)) {
      if (typeof node[0] === 'number') {
        updateBounds(node[0], node[1]);
      } else {
        for (let i = 0; i < node.length; i++) {
          extract(node[i]);
        }
      }
    } else if (typeof node === 'object') {
      if (node.type === 'FeatureCollection' && Array.isArray(node.features)) {
        for (let i = 0; i < node.features.length; i++) {
          const f = node.features[i];
          if (f?.geometry?.coordinates) extract(f.geometry.coordinates);
        }
      } else if (node.type === 'Feature' && node.geometry?.coordinates) {
        extract(node.geometry.coordinates);
      } else if ('coordinates' in node) {
        extract(node.coordinates);
      }
    }
  }

  extract(geojson);

  if (count === 0) return null;
  const result: [number, number, number, number] = [minLng, minLat, maxLng, maxLat];
  _bboxCache.set(geojson, result);
  return result;
}

/**
 * Asynchronously calculates the bounding box of a GeoJSON object, yielding
 * to the main thread to prevent UI freezing on heavy datasets.
 */
export function calculateBBoxAsync(geojson: any): Promise<[number, number, number, number] | null> {
  return new Promise((resolve) => {
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      (window as any).requestIdleCallback(() => resolve(calculateBBox(geojson)));
    } else {
      setTimeout(() => resolve(calculateBBox(geojson)), 0);
    }
  });
}

/**
 * Calculates the distance between two points in kilometers using Haversine formula.
 */
export function haversineDistance(coord1: [number, number], coord2: [number, number]): number {
  const R = 6371; // Radius of the Earth in km
  const [lon1, lat1] = coord1;
  const [lon2, lat2] = coord2;

  const dLat = (lat2 - lat1) * (Math.PI / 180);
  const dLon = (lon2 - lon1) * (Math.PI / 180);

  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * (Math.PI / 180)) * Math.cos(lat2 * (Math.PI / 180)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);

  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

/**
 * Measures distance or area on the map.
 * Returns a summary of the measurement.
 */
export function measure(
  map: Map,
  coords: [number, number][],
  type: 'distance' | 'area' = 'distance'
): GeoAnalysisResult {
  if (coords.length < 2) {
    return {
      success: false,
      data: 0,
      summary: "At least two points are required for measurement."
    };
  }

  if (type === 'distance') {
    let totalDistance = 0;
    for (let i = 0; i < coords.length - 1; i++) {
      totalDistance += haversineDistance(coords[i], coords[i + 1]);
    }

    const summary = `Total distance: ${totalDistance.toFixed(2)} km`;
    return {
      success: true,
      data: totalDistance,
      summary
    };
  }

  if (coords.length < 3) {
    return {
      success: false,
      data: 0,
      summary: "At least three points are required for area measurement."
    };
  }

  const area = polygonAreaKm2(coords);
  return {
    success: true,
    data: area,
    summary: `Area: ${formatDistance(area)}²`
  };
}

/**
 * Computes polygon area in km² using spherical excess on a unit-sphere
 * (Girard's theorem via the shoelace-like signed-angle approach).
 */
export function polygonAreaKm2(coords: [number, number][]): number {
  const R = 6371;
  const n = coords.length;
  if (n < 3) return 0;

  const isClosed = coords[0][0] === coords[n - 1][0] && coords[0][1] === coords[n - 1][1];
  const count = isClosed ? n - 1 : n;

  let total = 0;
  for (let i = 0; i < count; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[(i + 1) % n];
    total += (lon2 - lon1) * (Math.PI / 180) *
      (2 + Math.sin(lat1 * Math.PI / 180) + Math.sin(lat2 * Math.PI / 180));
  }
  return Math.abs(total * R * R / 2);
}

/**
 * Formats a distance value (in km) into a human-readable string.
 * Values < 1 km are shown in meters; >= 1 km in kilometers.
 */
export function formatDistance(km: number): string {
  if (km < 1) {
    return `${(km * 1000).toFixed(0)}m`;
  }
  return `${km.toFixed(2)}km`;
}
