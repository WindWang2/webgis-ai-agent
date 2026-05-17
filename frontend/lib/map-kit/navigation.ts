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
