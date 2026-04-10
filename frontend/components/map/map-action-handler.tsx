'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';

function calculateBBox(geojson: any): [number, number, number, number] | null {
  let bounds = [Infinity, Infinity, -Infinity, -Infinity];
  const coord: number[][] = [];

  function extract(node: any) {
    if (Array.isArray(node) && typeof node[0] === 'number') {
      coord.push(node);
    } else if (Array.isArray(node)) {
      node.forEach(extract);
    } else if (node?.type === 'FeatureCollection') {
      node.features.forEach((f: any) => extract(f.geometry.coordinates));
    } else if (node?.type === 'Feature') {
      extract(node.geometry.coordinates);
    } else if (node?.coordinates) {
      extract(node.coordinates);
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

export function MapActionHandler({ mapInstance }: { mapInstance?: any }) {
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

          if (!map.getSource(layerId)) {
            map.addSource(layerId, { type: 'geojson', data: geojson });
          } else {
            (map.getSource(layerId) as any).setData(geojson);
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