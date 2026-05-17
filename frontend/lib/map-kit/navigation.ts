import type { Map, FlyToOptions, FitBoundsOptions } from 'maplibre-gl';
import type { ViewportParams } from './types';

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
