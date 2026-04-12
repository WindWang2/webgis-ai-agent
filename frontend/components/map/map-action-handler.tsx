'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import type { GeoJSONFeatureCollection } from '@/lib/types';

interface MapInstanceLike {
  getMap?: () => maplibregl.Map;
  getSource?: (id: string) => { setData: (data: unknown) => void } | undefined;
  addSource?: (id: string, source: unknown) => void;
  addLayer?: (layer: unknown) => void;
  getLayer?: (id: string) => unknown;
  fitBounds?: (bounds: unknown, options?: unknown) => void;
  flyTo?: (options: unknown) => void;
}

function calculateBBox(geojson: GeoJSONFeatureCollection): [number, number, number, number] | null {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  const coord: number[][] = [];

  function extract(node: unknown) {
    if (Array.isArray(node) && typeof node[0] === 'number') {
      coord.push(node);
    } else if (Array.isArray(node)) {
      node.forEach(extract);
    } else if (node && typeof node === 'object' && 'type' in node) {
      const obj = node as Record<string, unknown>;
      if (obj.type === 'FeatureCollection' && Array.isArray(obj.features)) {
        (obj.features as Array<{ geometry?: { coordinates: unknown } }>).forEach(f => {
          if (f.geometry?.coordinates) extract(f.geometry.coordinates);
        });
      } else if (obj.type === 'Feature' && (obj as { geometry?: { coordinates: unknown } }).geometry?.coordinates) {
        extract((obj as { geometry: { coordinates: unknown } }).geometry.coordinates);
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

export function MapActionHandler({ mapInstance }: { mapInstance?: MapInstanceLike }) {
  const { action, clearAction } = useMapAction();

  useEffect(() => {
    if (!action || !mapInstance) return;

    const map = mapInstance.getMap ? mapInstance.getMap() : mapInstance;
    if (!map) return;

    try {
      switch (action.command) {
        case 'add_layer': {
          const { layerId, type, geojson, style, flyTo } = action.params;
          if (!layerId || !geojson) break;

          if (!map.getSource?.(layerId)) {
            map.addSource?.(layerId, { type: 'geojson', data: geojson });
          } else {
            map.getSource?.(layerId)?.setData(geojson);
          }

          if (!map.getLayer(layerId)) {
            map.addLayer({
              id: layerId,
              type: type || 'fill',
              source: layerId,
              paint: style || {}
            });
          }

          if (flyTo) {
            const bbox = calculateBBox(geojson);
            if (bbox) {
              map.fitBounds(bbox, { padding: 50, duration: 1500 });
            }
          }
          break;
        }

        case 'fly_to':
          if (action.params.center) {
            map.flyTo({
              center: action.params.center,
              zoom: action.params.zoom || 12,
              duration: 1500
            });
          }
          break;
      }
    } catch (error) {
      console.error('[MapActionHandler] Error executing action:', error);
    } finally {
      clearAction();
    }
  }, [action, mapInstance, clearAction]);

  return null;
}

export default MapActionHandler;