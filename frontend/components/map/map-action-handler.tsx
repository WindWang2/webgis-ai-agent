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

import { API_BASE } from '@/lib/api/config';
import { MAP_STYLES } from '@/lib/constants';
import { useHudStore } from '@/lib/store/useHudStore';

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

        case 'export_map': {
          const { title, subtitle, include_legend, dark_mode } = action.params;
          map.once("render", async () => {
            try {
              const canvas = map.getCanvas();
              const width = canvas.width;
              const height = canvas.height;
              
              const exportCanvas = document.createElement("canvas");
              exportCanvas.width = width;
              exportCanvas.height = height;
              const ctx = exportCanvas.getContext("2d");
              if (!ctx) return;
              
              // Draw underlying map canvas
              ctx.drawImage(canvas, 0, 0);
              
              // 1. Draw modern minimalist gradient overlay for header
              const gradient = ctx.createLinearGradient(0, 0, 0, 160);
              gradient.addColorStop(0, dark_mode ? "rgba(0,10,20,0.85)" : "rgba(255,255,255,0.95)");
              gradient.addColorStop(0.6, dark_mode ? "rgba(0,10,20,0.5)" : "rgba(255,255,255,0.6)");
              gradient.addColorStop(1, "rgba(0,0,0,0)");
              ctx.fillStyle = gradient;
              ctx.fillRect(0, 0, width, 180);
              
              // 2. Draw Title
              ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
              ctx.font = "bold 42px sans-serif";
              ctx.fillText(title || "WebGIS AI Agent 专题制图", 60, 70);
              
              // 3. Draw Subtitle
              if (subtitle) {
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.7)" : "rgba(30,41,59,0.7)";
                ctx.font = "24px sans-serif";
                ctx.fillText(subtitle, 60, 115);
              }

              // 4. Draw Watermark & Stamp
              ctx.fillStyle = dark_mode ? "rgba(0,242,255,0.15)" : "rgba(30,41,59,0.1)";
              ctx.textAlign = "right";
              ctx.font = "18px monospace";
              ctx.fillText("Generated by WebGIS AI Agent ✨", width - 40, height - 30);
              ctx.textAlign = "left"; // reset

              // Fetch blob natively without dataUrl overhead if possible, but dataUrl is safer cross-browser for tainted canvas
              const dataUrl = exportCanvas.toDataURL("image/png");
              const res = await fetch(dataUrl);
              const blob = await res.blob();
              
              const formData = new FormData();
              formData.append("file", blob, "export.png");
              if (title) formData.append("title", title);
              
              const uploadRes = await fetch(`${API_BASE}/api/v1/export`, {
                method: "POST",
                body: formData
              });
              
              if (uploadRes.ok) {
                const data = await uploadRes.json();
                const url = data.url;
                // Important: Trigger background system ping implicitly so the Agent can speak
                useHudStore.getState().setPendingSystemMessage(
                  `[系统通知] 专题地图 \`${title || '未命名'}\` 已成功排版合成，` +
                  `文件已落盘并分配URL：${url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${url})\` 将该成品展示给用户，并祝其研究顺利！注意展示完图片后直接结束。`
                );
              } else {
                 throw new Error("Export URL generation failed");
              }
            } catch (e) {
              console.error("[MapActionHandler] Canvas extraction/export failed", e);
              useHudStore.getState().setPendingSystemMessage(
                `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`
              );
            }
          });
          map.triggerRepaint();
          break;
        }

        case 'add_raster_layer': {
          const { id, url, image, bbox, opacity = 1.0 } = action.params;
          const imageUrl = image || url;
          if (!imageUrl || !bbox) break;

          // bbox should be [west, south, east, north]
          const coordinates = [
            [bbox[0], bbox[3]], // top-left
            [bbox[2], bbox[3]], // top-right
            [bbox[2], bbox[1]], // bottom-right
            [bbox[0], bbox[1]]  // bottom-left
          ];

          if (map.getSource(id)) {
            map.removeLayer(`${id}-layer`);
            map.removeSource(id);
          }

          map.addSource(id, {
            type: 'image',
            url: imageUrl,
            coordinates: coordinates
          });

          map.addLayer({
            id: `${id}-layer`,
            type: 'raster',
            source: id,
            paint: {
              'raster-opacity': opacity,
              'raster-fade-duration': 500
            }
          });

          // Zoom to the result extent
          map.fitBounds(bbox, { padding: 80, duration: 1500 });
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