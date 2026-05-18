'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import maplibregl from 'maplibre-gl';

import { useMap } from 'react-map-gl/maplibre';

import { API_BASE } from '@/lib/api/config';
import { TILE_PROVIDERS } from '@/lib/providers';
import { useHudStore } from '@/lib/store/useHudStore';

import * as navigation from '@/lib/map-kit/navigation';
import * as renderer from '@/lib/map-kit/renderer';
import * as exporter from '@/lib/map-kit/exporter';

/**
 * Helper to parse filters that might come in as JSON strings from AI commands
 */
function parseFilter(filter: any): any[] | null {
  if (!filter) return null;
  if (typeof filter === 'string') {
    try {
      return JSON.parse(filter);
    } catch (e) {
      console.warn('[MapActionHandler] Failed to parse filter string:', filter);
      return null;
    }
  }
  return filter;
}

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

          const id = `custom-${layerId}`;
          renderer.addGeoJsonSource(map, id, geojson);
          
          if (style && (style.type === 'choropleth' || style.type === 'lisa')) {
            renderer.addThematicLayer(map, id, geojson, style as any);
          } else {
            renderer.addVectorLayer(map, {
              id,
              type: (type || 'fill') as any,
              source: id,
              paint: style || {}
            });
          }

          if (flyTo) {
            const bbox = navigation.calculateBBox(geojson);
            if (bbox) {
              navigation.fitBounds(map, bbox, 50);
            }
          }
          break;
        }

        case 'fly_to':
          if (action.params?.center) {
            navigation.flyTo(map, {
              center: action.params.center,
              zoom: action.params?.zoom || 12,
              bearing: action.params.bearing,
              pitch: action.params.pitch,
            });
          }
          break;
        
        case 'add_heatmap_raster': {
          const { image, bbox, opacity, layerId } = action.params || {};
          if (!image || !bbox) break;
          
          const id = `custom-${layerId || 'heatmap-' + Date.now()}`;
          
          // bbox is [west, south, east, north]
          // MapLibre image source expects: [top-left, top-right, bottom-right, bottom-left]
          const coords: [[number, number], [number, number], [number, number], [number, number]] = [
            [bbox[0], bbox[3]], // west, north
            [bbox[2], bbox[3]], // east, north
            [bbox[2], bbox[1]], // east, south
            [bbox[0], bbox[1]]  // west, south
          ];

          renderer.addImageSource(map, id, image, coords);
          renderer.addVectorLayer(map, {
            id,
            type: 'raster',
            source: id,
            paint: { 'raster-opacity': opacity || 0.7 }
          });
          
          navigation.fitBounds(map, bbox, 50);
          break;
        }

        case 'add_native_heatmap': {
          const { geojson, layerId, palette, radius } = action.params || {};
          if (!geojson) break;

          const id = `custom-${layerId || 'native-heatmap-' + Date.now()}`;
          
          renderer.addGeoJsonSource(map, id, geojson);
          renderer.addNativeHeatmap(map, {
            id,
            source: id,
            palette: palette as any,
            radius,
            opacity: 0.8
          });
          break;
        }

        case 'BASE_LAYER_CHANGE': {
          const name = action.params?.name as string | undefined;
          if (!name) break;
          const search = name.toLowerCase();

          // 1. Exact name match (case-insensitive)
          let idx = TILE_PROVIDERS.findIndex(p => p.name.toLowerCase() === search);

          // 2. Bidirectional substring match
          if (idx === -1) {
            idx = TILE_PROVIDERS.findIndex(p => {
              const n = p.name.toLowerCase();
              return n.includes(search) || search.includes(n);
            });
          }

          // 3. Keyword index — ai команды like "卫星"/"dark"/"osm"命中对应条目
          if (idx === -1) {
            idx = TILE_PROVIDERS.findIndex(p =>
              p.keywords.some(k => search.includes(k.toLowerCase())),
            );
          }

          if (idx !== -1) {
            setSelectedBaseLayer(idx);
          } else {
            console.warn('[MapActionHandler] Could not match base layer name:', name);
          }
          break;
        }

        case 'LAYER_VISIBILITY_UPDATE': {
          const { layer_id, visible, opacity } = action.params || {};
          if (!layer_id) break;
          
          const style = map.getStyle();
          style.layers?.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}`)) {
              renderer.updateLayerStyle(map, l.id, {
                visibility: visible !== undefined ? (visible ? 'visible' : 'none') : undefined,
                opacity
              });
            }
          });
          break;
        }

        case 'LAYER_STYLE_UPDATE': {
          const { layer_id, style } = action.params || {};
          if (!layer_id || !style) break;
          const mapStyle = map.getStyle();
          mapStyle.layers?.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}`)) {
              renderer.updateLayerStyle(map, l.id, {
                color: (style as any).color,
                strokeWidth: (style as any).strokeWidth
              });
            }
          });
          break;
        }

        case 'REMOVE_LAYER':
        case 'remove_layer': {
          const { layer_id, layerId } = action.params || {};
          const target = layer_id || layerId;
          if (!target) break;
          renderer.removeLayerStack(map, `custom-${target}`, true);
          break;
        }

        case 'export_map': {
          const {
            title,
            subtitle,
            showWatermark = true,
            showLegend = action.params?.showLegend ?? action.params?.include_legend ?? true,
            showCompass = action.params?.showCompass ?? action.params?.include_compass ?? true,
            showScale = action.params?.showScale ?? action.params?.include_scale ?? true,
            format = "png",
            paperSize = "screen",
            orientation = "landscape",
            dpi = 96
          } = action.params || {};
          
          const theme = useHudStore.getState().theme;

          map.once("render", async () => {
            try {
              const baseCanvas = map.getCanvas();
              const { canvas: exportCanvas, srcW } = exporter.prepareExportCanvas(baseCanvas, {
                paperSize: paperSize as any,
                orientation: orientation as any,
                dpi
              });

              const storeState = useHudStore.getState();
              const thematicLayerInfo = storeState.layers.find(
                (l) => l.visible && ((l.style as any)?.type === "choropleth" || (l.style as any)?.type === "lisa" || (l.source as any)?.metadata?.thematic_type === "choropleth")
              );
              const thematicLayer = (thematicLayerInfo?.style as any)?.type ? thematicLayerInfo?.style : thematicLayerInfo;

              exporter.composeLayout(exportCanvas, title || '', subtitle || '', {
                dpi,
                theme,
                showScale,
                showCompass,
                showWatermark,
                showLegend,
                mapCenter: map.getCenter(),
                mapZoom: map.getZoom(),
                mapBearing: map.getBearing(),
                thematicLayer
              });

              const dataUrl = exportCanvas.toDataURL("image/png");
              const res = await fetch(dataUrl);
              const blob = await res.blob();

              const fmt = (format ?? "png").toLowerCase();

              if (fmt === "pdf") {
                const pdfForm = new FormData();
                pdfForm.append("file", blob, "export.png");
                if (title) pdfForm.append("title", title);
                if (subtitle) pdfForm.append("subtitle", subtitle);
                const centerLat = map.getCenter().lat;
                const zoom = map.getZoom();
                const mpp =
                  (156543.03392 * Math.cos((centerLat * Math.PI) / 180)) /
                  Math.pow(2, zoom);
                const mapWidthMeters = mpp * srcW;
                const physicalWidthMeters = srcW * (0.0254 / 96);
                const scaleApprox = Math.round(mapWidthMeters / physicalWidthMeters);
                pdfForm.append("scale_text", `1:${scaleApprox.toLocaleString()}`);

                const pdfRes = await fetch(`${API_BASE}/api/v1/export/pdf`, {
                  method: "POST",
                  body: pdfForm,
                });
                if (!pdfRes.ok) throw new Error(`PDF endpoint returned ${pdfRes.status}`);
                const pdfData = await pdfRes.json();
                const pdfUrl: string = pdfData.url;
                useHudStore.getState().setPendingSystemMessage(
                  `[系统通知] 专题底图 PDF \`${title || "未命名"}\` 已成功生成，` +
                    `文件已落盘并分配URL：${pdfUrl}。` +
                    `请告知用户 PDF 已就绪，可通过以下链接下载：[下载PDF](${API_BASE}${pdfUrl})。注意展示完链接后直接结束。`
                );
              } else {
                const formData = new FormData();
                formData.append("file", blob, "export.png");
                if (title) formData.append("title", title);

                const uploadRes = await fetch(`${API_BASE}/api/v1/export`, {
                  method: "POST",
                  body: formData,
                });
                if (!uploadRes.ok) throw new Error("Export URL generation failed");
                const data = await uploadRes.json();
                const url: string = data.url;
                useHudStore.getState().setPendingSystemMessage(
                  `[系统通知] 专题地图 \`${title || "未命名"}\` 已成功排版合成，` +
                    `文件已落盘并分配URL：${url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${url})\` 将该成品展示给用户，并祝其研究顺利！注意展示完图片后直接结束。`
                );
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
          if (!imageUrl || !bbox || !id) break;

          const sourceId = `custom-${id}`;
          const layerId = `${sourceId}-layer`;

          // bbox should be [west, south, east, north]
          const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
            [bbox[0], bbox[3]], // top-left
            [bbox[2], bbox[3]], // top-right
            [bbox[2], bbox[1]], // bottom-right
            [bbox[0], bbox[1]]  // bottom-left
          ];

          renderer.addImageSource(map, sourceId, imageUrl, coordinates);
          renderer.addVectorLayer(map, {
            id: layerId,
            type: 'raster',
            source: sourceId,
            paint: {
              'raster-opacity': opacity,
              'raster-fade-duration': 500
            }
          });

          navigation.fitBounds(map, bbox, 80);
          break;
        }

        case 'APPLY_LAYER_FILTER': {
          const { layer_id, filter } = action.params || {};
          if (!layer_id) break;
          // Apply MapLibre filter with fallback parser for simple string filters
          map.setFilter(layer_id, parseFilter(filter) as any);
          break;
        }
      }
    } catch (error) {
      console.error('[MapActionHandler] Error executing action:', error);
    } finally {
      popAction();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, mapInstance, popAction]);

  return null;
}

export default MapActionHandler;