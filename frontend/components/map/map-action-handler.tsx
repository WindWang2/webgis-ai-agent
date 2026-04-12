'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import maplibregl from 'maplibre-gl';

interface MapInstanceLike {
  getMap?: () => maplibregl.Map;
  getSource?: (id: string) => maplibregl.Source | undefined;
  addSource?: (id: string, source: any) => void;
  addLayer?: (layer: any) => void;
  getLayer?: (id: string) => any;
  fitBounds?: (bounds: any, options?: any) => void;
  flyTo?: (options: any) => void;
}

function calculateBBox(geojson: GeoJSONFeatureCollection): [number, number, number, number] | null {
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

export function MapActionHandler({ mapInstance }: { mapInstance?: MapInstanceLike }) {
  const { action, clearAction } = useMapAction();

  useEffect(() => {
    if (!action || !mapInstance) return;

    const map = mapInstance.getMap ? mapInstance.getMap() : (mapInstance as unknown as maplibregl.Map);
    if (!map) return;

    try {
      switch (action.command) {
        case 'add_layer': {
          const { layerId, type, geojson, style, flyTo } = action.params;
          if (!layerId || !geojson) break;

          if (!map.getSource(layerId)) {
            map.addSource(layerId, { type: 'geojson', data: geojson });
          } else {
            (map.getSource(layerId) as maplibregl.GeoJSONSource).setData(geojson);
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
        
        case 'add_heatmap_raster': {
          const { image, bbox, opacity, layerId } = action.params;
          if (!image || !bbox) break;
          
          const id = layerId || 'heatmap-' + Date.now();
          
          // top-left, top-right, bottom-right, bottom-left
          const coordinates = [
            [bbox[1], bbox[3]], 
            [bbox[2], bbox[3]], 
            [bbox[2], bbox[0]], 
            [bbox[1], bbox[0]]  
          ];

          if (!map.getSource(id)) {
            map.addSource(id, {
              type: 'image',
              url: image,
              coordinates: coordinates
            });
            map.addLayer({
              id: id,
              type: 'raster',
              source: id,
              paint: { 'raster-opacity': opacity || 0.7 }
            });
          } else {
            const source = map.getSource(id) as maplibregl.ImageSource;
            if (source.updateImage) {
              source.updateImage({ url: image, coordinates: coordinates });
            }
          }
          
          map.fitBounds([[bbox[1], bbox[0]], [bbox[2], bbox[3]]], { padding: 50 });
          break;
        }
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