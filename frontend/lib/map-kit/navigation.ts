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

/**
 * Calculates the bounding box of a GeoJSON object.
 * Returns [minLng, minLat, maxLng, maxLat] or null.
 */
export function calculateBBox(geojson: any): [number, number, number, number] | null {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  const coord: number[][] = [];

  function extract(node: any) {
    if (Array.isArray(node) && typeof node[0] === 'number') {
      coord.push(node as number[]);
    } else if (Array.isArray(node)) {
      node.forEach(extract);
    } else if (node && typeof node === 'object' && 'type' in node) {
      const obj = node as any;
      if (obj.type === 'FeatureCollection' && Array.isArray(obj.features)) {
        obj.features.forEach((f: any) => {
          if (f.geometry?.coordinates) extract(f.geometry.coordinates);
        });
      } else if (obj.type === 'Feature' && obj.geometry?.coordinates) {
        extract(obj.geometry.coordinates);
      } else if ('coordinates' in obj) {
        extract(obj.coordinates);
      }
    }
  }

  extract(geojson);
  if (coord.length === 0) return null;

  coord.forEach(c => {
    if (c[0] < bounds[0]) bounds[0] = c[0];
    if (c[1] < bounds[1]) bounds[1] = c[1];
    if (c[0] > bounds[2]) bounds[2] = c[0];
    if (c[1] > bounds[3]) bounds[3] = c[1];
  });

  return bounds as [number, number, number, number];
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

  let total = 0;
  for (let i = 0; i < n - 1; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[i + 1];
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
