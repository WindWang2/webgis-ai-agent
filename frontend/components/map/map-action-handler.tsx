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

import { useMap } from 'react-map-gl/maplibre';

import { MAP_STYLES } from '@/lib/constants';

export function MapActionHandler() {
  const { actions, popAction, setSelectedBaseLayer } = useMapAction();
  const mapContext = useMap();
  const mapInstance = mapContext.default;
  const action = actions[0];

  useEffect(() => {
    if (!action) return;
    
    if (!mapInstance) {
      console.warn('[MapActionHandler] No map instance found! (Is MapProvider missing or Map ID mismatch?)');
      return;
    }

    const map = mapInstance.getMap();
    if (!map) return;

    console.log('[MapActionHandler] Processing action:', action.command, 'on map:', map.getContainer().id);

    try {
      switch (action.command) {
        case 'add_layer': {
          const { layerId, type, geojson, style, flyTo } = action.params;
          if (!layerId || !geojson) break;

          if (!map.getSource(layerId)) {
            map.addSource(layerId, { type: 'geojson', data: geojson as any });
          } else {
            (map.getSource(layerId) as maplibregl.GeoJSONSource).setData(geojson as any);
          }

          if (!map.getLayer(layerId)) {
            map.addLayer({
              id: layerId,
              type: type || 'fill',
              source: layerId as any,
              paint: style || {}
            } as any);
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
          if (action.params?.center) {
            map.flyTo({
              center: action.params.center,
              zoom: action.params?.zoom || 12,
              duration: 1500
            });
          }
          break;
        
        case 'add_heatmap_raster': {
          const { image, bbox, opacity, layerId } = action.params || {};
          if (!image || !bbox) break;
          
          const id = layerId || 'heatmap-' + Date.now();
          
          const coords = [
            [bbox[1], bbox[3]], 
            [bbox[2], bbox[3]], 
            [bbox[2], bbox[0]], 
            [bbox[1], bbox[0]]  
          ] as [[number, number], [number, number], [number, number], [number, number]];

          if (!map.getSource(id)) {
            map.addSource(id, {
              type: 'image',
              url: image,
              coordinates: coords
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
              source.updateImage({ url: image, coordinates: coords });
            }
          }
          
          map.fitBounds([[bbox[1], bbox[0]], [bbox[2], bbox[3]]], { padding: 50 });
          break;
        }

        case 'BASE_LAYER_CHANGE': {
          const name = action.params?.name;
          if (name) {
            const searchName = name.toLowerCase();
            let idx = MAP_STYLES.findIndex(s => s.name.toLowerCase() === searchName);
            
            if (idx === -1) {
              idx = MAP_STYLES.findIndex(s => {
                const lowerS = s.name.toLowerCase();
                return lowerS.includes(searchName) || searchName.includes(lowerS);
              });
            }
            
            if (idx === -1) {
              if (searchName.includes("卫星") || searchName.includes("影像") || searchName.includes("satellite")) {
                idx = MAP_STYLES.findIndex(s => s.name.includes("影像"));
              } else if (searchName.includes("深色") || searchName.includes("dark")) {
                idx = MAP_STYLES.findIndex(s => s.name.includes("深色"));
              } else if (searchName.includes("地图") || searchName.includes("osm") || searchName.includes("street")) {
                idx = MAP_STYLES.findIndex(s => s.name.includes("OSM"));
              }
            }

            if (idx !== -1) {
              console.log('[MapActionHandler] Directly setting base layer to:', MAP_STYLES[idx].name);
              setSelectedBaseLayer(idx);
            } else {
              console.warn('[MapActionHandler] Could not match base layer name:', name);
            }
          }
          break;
        }

        case 'LAYER_VISIBILITY_UPDATE': {
          const { layer_id, visible, opacity } = action.params || {};
          if (!layer_id) break;
          
          // Analytical layers in MapPanel are prefixed with 'custom-' and suffixed with sub-layer IDs
          const layers = map.getStyle().layers || [];
          layers.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}`)) {
              if (visible !== undefined) {
                map.setLayoutProperty(l.id, 'visibility', visible ? 'visible' : 'none');
              }
              if (opacity !== undefined) {
                const prop = l.type === 'raster' ? 'raster-opacity' : 
                            l.type === 'fill' ? 'fill-opacity' :
                            l.type === 'line' ? 'line-opacity' :
                            l.type === 'circle' ? 'circle-opacity' : '';
                if (prop) map.setPaintProperty(l.id, prop, opacity);
              }
            }
          });
          break;
        }

        case 'LAYER_STYLE_UPDATE': {
          const { layer_id, style } = action.params || {};
          if (!layer_id || !style) break;
          const layers = map.getStyle().layers || [];
          layers.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}`)) {
              if (style.color) {
                const prop = l.type === 'fill' ? 'fill-color' :
                            l.type === 'line' ? 'line-color' :
                            l.type === 'circle' ? 'circle-color' : '';
                if (prop) map.setPaintProperty(l.id, prop, style.color);
              }
              if (style.strokeWidth) {
                if (l.type === 'line') map.setPaintProperty(l.id, 'line-width', style.strokeWidth);
                if (l.type === 'circle') map.setPaintProperty(l.id, 'circle-stroke-width', style.strokeWidth);
              }
            }
          });
          break;
        }

        case 'REMOVE_LAYER':
        case 'remove_layer': {
          const { layer_id, layerId } = action.params || {};
          const target = layer_id || layerId;
          if (!target) break;
          const style = map.getStyle();
          style.layers?.forEach(l => {
            if (l.id.startsWith(`custom-${target}`)) {
              map.removeLayer(l.id);
            }
          });
          if (map.getSource(`custom-${target}`)) {
            map.removeSource(`custom-${target}`);
          }
          break;
        }
      }
    } catch (error) {
      console.error('[MapActionHandler] Error executing action:', error);
    } finally {
      popAction();
    }
  }, [action, mapInstance, popAction]);

  return null;
}

export default MapActionHandler;