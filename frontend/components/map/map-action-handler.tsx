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

// R8 annotation source: 单一 FeatureCollection 收纳 add_marker / draw_measurement 输出。
// Zustand 状态迁移：将原本模块级的 mutable array 迁移至 Zustand，支持多组件共享、响应式更新和一致的生命周期管理。
const ANNOTATION_SOURCE_ID = 'claude-annotations';

function annotationFC() {
  const annotations = useHudStore.getState().annotations;
  return { type: 'FeatureCollection', features: annotations.slice() };
}

function ensureAnnotationLayers(map: any) {
  if (!map.getSource(ANNOTATION_SOURCE_ID)) {
    map.addSource(ANNOTATION_SOURCE_ID, { type: 'geojson', data: annotationFC() });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-fill`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-fill`,
      source: ANNOTATION_SOURCE_ID,
      type: 'fill',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.25 },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-line`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-line`,
      source: ANNOTATION_SOURCE_ID,
      type: 'line',
      filter: ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'Polygon']],
      paint: { 'line-color': '#2563eb', 'line-width': 2 },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-circle`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-circle`,
      source: ANNOTATION_SOURCE_ID,
      type: 'circle',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 7,
        'circle-color': ['coalesce', ['get', 'color'], '#ef4444'],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-label`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-label`,
      source: ANNOTATION_SOURCE_ID,
      type: 'symbol',
      layout: {
        'text-field': ['get', 'label'],
        'text-size': 12,
        'text-anchor': 'top',
        'text-offset': [0, 0.8],
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#0f172a',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
      },
    });
  }
}

function refreshAnnotations(map: any) {
  const src = map.getSource(ANNOTATION_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (src && typeof (src as any).setData === 'function') {
    src.setData(annotationFC() as any);
  }
}

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

    // F5: 默认在 finally 同步 popAction；某些 case 走异步 map.once 回调，
    // 把 deferredPop 设为 true，由 case 自己负责出队。
    let deferredPop = false;

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

        case 'zoom_to_bbox': {
          const bbox = action.params?.bbox as [number, number, number, number] | undefined;
          const padding = action.params?.padding ?? 50;
          if (!bbox || bbox.length < 4) break;
          try {
            navigation.fitBounds(map, bbox, padding);
          } catch (e) {
            console.warn('[MapActionHandler] zoom_to_bbox failed:', e);
          }
          break;
        }

        case 'set_map_view': {
          const { zoom, bearing, pitch } = action.params || {};
          if (zoom === undefined && bearing === undefined && pitch === undefined) break;
          const center = map.getCenter();
          navigation.flyTo(map, {
            center: [center.lng, center.lat],
            zoom: zoom !== undefined ? zoom : map.getZoom(),
            bearing: bearing !== undefined ? bearing : map.getBearing(),
            pitch: pitch !== undefined ? pitch : map.getPitch(),
          });
          break;
        }
        
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
            // QA-2026-05-20 ISSUE-002 fix: keep useHudStore.baseLayer in sync so
            // the dropdown button label, HUD panel, and status bar all show the
            // canonical name after an AI-driven switch_base_layer call.
            useHudStore.getState().setBaseLayer(TILE_PROVIDERS[idx].name);
          } else {
            console.warn('[MapActionHandler] Could not match base layer name:', name);
          }
          break;
        }

        case 'LAYER_VISIBILITY_UPDATE': {
          const { layer_id, visible, opacity, name, color } = action.params || {};
          if (!layer_id) break;

          const style = map.getStyle();
          style.layers?.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}-`)) {
              renderer.updateLayerStyle(map, l.id, {
                visibility: visible !== undefined ? (visible ? 'visible' : 'none') : undefined,
                opacity,
                color: color as string | undefined,
              });
            }
          });
          // Sync visibility/opacity/name/color back to store so LayersTab stays in sync
          const storeUpdates: Record<string, unknown> = {};
          if (visible !== undefined) storeUpdates.visible = visible;
          if (opacity !== undefined) storeUpdates.opacity = opacity;
          if (name !== undefined) storeUpdates.name = name;
          if (color !== undefined) storeUpdates.style = { ...(useHudStore.getState().layers.find(l => l.id === layer_id)?.style ?? {}), color };
          if (Object.keys(storeUpdates).length > 0) {
            useHudStore.getState().updateLayer(layer_id, storeUpdates);
          }
          break;
        }

        case 'LAYER_STYLE_UPDATE': {
          const { layer_id, style } = action.params || {};
          if (!layer_id || !style) break;
          const mapStyle = map.getStyle();
          mapStyle.layers?.forEach(l => {
            if (l.id.startsWith(`custom-${layer_id}-`)) {
              renderer.updateLayerStyle(map, l.id, {
                color: (style as any).color,
                strokeWidth: (style as any).strokeWidth
              });
            }
          });
          // Sync style changes back to store so LayersTab swatch stays in sync
          const styleColor = (style as any).color;
          if (styleColor !== undefined) {
            const existing = useHudStore.getState().layers.find(l => l.id === layer_id);
            useHudStore.getState().updateLayer(layer_id, {
              style: { ...(existing?.style ?? {}), color: styleColor },
            });
          }
          break;
        }

        case 'REMOVE_LAYER':
        case 'remove_layer': {
          const { layer_id, layerId } = action.params || {};
          const target = layer_id || layerId;
          if (!target) break;
          renderer.removeLayerStack(map, `custom-${target}`, true);
          // Sync removal to store so LayersTab stays in sync
          useHudStore.getState().removeLayer(target);
          break;
        }

        case 'REORDER_LAYER': {
          const { layer_id, position, before_id } = action.params || {};
          if (!layer_id || !position) break;
          const style = map.getStyle();
          const allLayers = style.layers || [];
          const subIds = allLayers
            .map((l: any) => l.id as string)
            .filter((id) => id === `custom-${layer_id}` || id.startsWith(`custom-${layer_id}-`));
          if (subIds.length === 0) break;

          // Snapshot custom layer IDs only (we ignore base style layers)
          const customIds = allLayers
            .map((l: any) => l.id as string)
            .filter((id) => id.startsWith('custom-'));

          const firstSubIdx = customIds.indexOf(subIds[0]);
          let beforeAnchor: string | undefined;

          if (position === 'top') {
            beforeAnchor = undefined; // moveLayer with no anchor -> top
          } else if (position === 'bottom') {
            const bottomCandidate = customIds.find((id) => !subIds.includes(id));
            beforeAnchor = bottomCandidate;
          } else if (position === 'up') {
            // Find next custom group above
            for (let i = firstSubIdx - 1; i >= 0; i--) {
              if (!subIds.includes(customIds[i])) {
                // Place subIds before the layer that sits above customIds[i]
                beforeAnchor = customIds[i];
                break;
              }
            }
          } else if (position === 'down') {
            for (let i = firstSubIdx + subIds.length; i < customIds.length; i++) {
              if (!subIds.includes(customIds[i])) {
                beforeAnchor = customIds[i + 1];
                break;
              }
            }
          } else if (position === 'before' && before_id) {
            const targetGroup = customIds.find((id) => id === `custom-${before_id}` || id.startsWith(`custom-${before_id}-`));
            beforeAnchor = targetGroup;
          }

          try {
            for (const id of subIds) {
              map.moveLayer(id, beforeAnchor);
            }
          } catch (e) {
            console.warn('[MapActionHandler] REORDER_LAYER failed:', e);
          }
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

          // /review C11: surface a loading message — export upload can take seconds
          // (SVG/PDF over slow network) and currently the user sees nothing happen.
          try {
            useHudStore.getState().setPendingSystemMessage(
              `[系统通知] 正在生成 ${String(format).toUpperCase()} 导出文件…`
            );
          } catch {
            /* defensive */
          }

          const theme = useHudStore.getState().theme;
          // F5: 异步 export 必须等 map.once('render') 真正回调完再 popAction，
          // 否则连续触发 export 会让后一次在前一次还没合成完时覆盖 canvas。
          // 标记该 case 自己负责 popAction，外层 finally 跳过。
          deferredPop = true;

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

              if (fmt === "svg") {
                // Wrap the rendered PNG inside an SVG container.
                // Downstream vector tools (Illustrator/Inkscape) can open this and
                // layer additional vector annotations on top of the raster basemap.
                const w = exportCanvas.width;
                const h = exportCanvas.height;
                const svg = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><title>${(title || "map").replace(/[<>&]/g, "")}</title><image width="${w}" height="${h}" xlink:href="${dataUrl}"/></svg>`;
                const svgBlob = new Blob([svg], { type: "image/svg+xml" });
                const svgForm = new FormData();
                svgForm.append("file", svgBlob, "export.svg");
                if (title) svgForm.append("title", title);
                const svgRes = await fetch(`${API_BASE}/api/v1/export`, {
                  method: "POST",
                  body: svgForm,
                });
                if (!svgRes.ok) throw new Error("SVG export upload failed");
                const svgData = await svgRes.json();
                const svgUrl: string = svgData.url;
                useHudStore.getState().setPendingSystemMessage(
                  `[系统通知] 专题地图 SVG \`${title || "未命名"}\` 已成功生成 (含嵌入位图)，` +
                    `文件已落盘并分配URL：${svgUrl}。可通过以下链接下载：[下载SVG](${API_BASE}${svgUrl})。注意展示完链接后直接结束。`
                );
              } else if (fmt === "pdf") {
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
            } finally {
              // F5: 真正合成完才出队，杜绝重入
              popAction();
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

        case 'add_marker': {
          const { longitude, latitude, label, color } = action.params || {};
          if (typeof longitude !== 'number' || typeof latitude !== 'number') break;
          ensureAnnotationLayers(map);
          useHudStore.getState().addAnnotation({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [longitude, latitude] },
            properties: { label: label || null, color: color || '#ef4444', kind: 'marker' },
          });
          refreshAnnotations(map);
          break;
        }

        case 'draw_measurement': {
          const { shape, coordinates, label } = action.params || {};
          if (!Array.isArray(coordinates) || coordinates.length < 2) break;
          ensureAnnotationLayers(map);
          const store = useHudStore.getState();
          if (shape === 'polygon') {
            const ring = coordinates.slice();
            // 闭合环
            if (ring.length > 0) {
              const first = ring[0];
              const last = ring[ring.length - 1];
              if (first[0] !== last[0] || first[1] !== last[1]) ring.push([first[0], first[1]]);
            }
            store.addAnnotation({
              type: 'Feature',
              geometry: { type: 'Polygon', coordinates: [ring] },
              properties: { label: label || null, kind: 'measure_polygon' },
            });
            // 也撒一个 label 点在质心，便于地图上看到数值
            const cx = ring.reduce((s, p) => s + p[0], 0) / ring.length;
            const cy = ring.reduce((s, p) => s + p[1], 0) / ring.length;
            if (label) {
              store.addAnnotation({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [cx, cy] },
                properties: { label, color: 'transparent', kind: 'measure_label' },
              });
            }
          } else {
            // 默认 polyline
            store.addAnnotation({
              type: 'Feature',
              geometry: { type: 'LineString', coordinates: coordinates.slice() },
              properties: { label: label || null, kind: 'measure_line' },
            });
            if (label) {
              const end = coordinates[coordinates.length - 1];
              store.addAnnotation({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: end.slice() },
                properties: { label, color: 'transparent', kind: 'measure_label' },
              });
            }
          }
          refreshAnnotations(map);
          break;
        }

        case 'clear_annotations': {
          useHudStore.getState().clearAnnotations();
          refreshAnnotations(map);
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
      // /review C10: surface AI command failures to the user via system message
      // instead of swallowing in console — otherwise user sees nothing and
      // assumes the AI lied about what it was doing.
      const msg = error instanceof Error ? error.message : String(error);
      console.error('[MapActionHandler] Error executing action:', error);
      try {
        useHudStore.getState().setPendingSystemMessage(
          `[系统通知] 地图命令 ${action.command} 执行失败: ${msg}`
        );
      } catch {
        /* defensive: store unavailable */
      }
    } finally {
      if (!deferredPop) popAction();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, mapInstance, popAction]);

  return null;
}

export default MapActionHandler;