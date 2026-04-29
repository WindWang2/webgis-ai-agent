'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import maplibregl from 'maplibre-gl';

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
          const {
            title,
            subtitle,
            dark_mode,
            include_legend = true,
            include_compass = true,
            include_scale = true,
            format = "png",
          } = action.params;

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

              // ── 0. Base map ──────────────────────────────────────────────
              ctx.drawImage(canvas, 0, 0);

              // ── 1. Header gradient + title ───────────────────────────────
              const headerH = subtitle ? 130 : 100;
              const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
              headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
              headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
              headerGrad.addColorStop(1, "rgba(0,0,0,0)");
              ctx.fillStyle = headerGrad;
              ctx.fillRect(0, 0, width, headerH);

              ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
              ctx.font = `bold ${Math.max(28, Math.round(width / 22))}px sans-serif`;
              ctx.fillText(title || "WebGIS AI Agent 专题制图", 56, Math.round(headerH * 0.52));

              if (subtitle) {
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)";
                ctx.font = `${Math.max(18, Math.round(width / 36))}px sans-serif`;
                ctx.fillText(subtitle, 56, Math.round(headerH * 0.82));
              }

              // ── 2. Scale bar ──────────────────────────────────────────────
              if (include_scale) {
                const center = map.getCenter();
                const zoom = map.getZoom();
                const metersPerPx =
                  (156543.03392 * Math.cos((center.lat * Math.PI) / 180)) /
                  Math.pow(2, zoom);
                const targetPx = Math.round(width * 0.12);
                const rawMeters = metersPerPx * targetPx;
                // Round to a nice number
                const magnitude = Math.pow(10, Math.floor(Math.log10(rawMeters)));
                const nice = [1, 2, 5, 10].reduce((prev, n) => {
                  const candidate = n * magnitude;
                  return Math.abs(candidate - rawMeters) < Math.abs(prev - rawMeters)
                    ? candidate
                    : prev;
                }, magnitude);
                const barPx = nice / metersPerPx;
                const barLabel = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;

                const bx = 56, by = height - 52, bh = 8;
                // Outer frame
                ctx.strokeStyle = dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)";
                ctx.lineWidth = 1.5;
                ctx.strokeRect(bx, by, barPx, bh);
                // Alternating fill segments
                const segCount = 4;
                const segW = barPx / segCount;
                for (let i = 0; i < segCount; i++) {
                  ctx.fillStyle =
                    i % 2 === 0
                      ? dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)"
                      : "rgba(0,0,0,0)";
                  ctx.fillRect(bx + i * segW, by, segW, bh);
                }
                // Label
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
                ctx.font = "bold 13px sans-serif";
                ctx.textAlign = "left";
                ctx.fillText("0", bx, by - 4);
                ctx.textAlign = "right";
                ctx.fillText(barLabel, bx + barPx, by - 4);
                ctx.textAlign = "left";
              }

              // ── 3. North arrow (compass) ─────────────────────────────────
              if (include_compass) {
                const bearing = map.getBearing();
                const cx = width - 64, cy = 64, r = 28;
                ctx.save();
                ctx.translate(cx, cy);
                ctx.rotate((bearing * Math.PI) / 180);

                // Shadow halo
                ctx.shadowColor = "rgba(0,0,0,0.4)";
                ctx.shadowBlur = 6;

                // North half (red)
                ctx.beginPath();
                ctx.moveTo(0, -r);
                ctx.lineTo(r * 0.35, 0);
                ctx.lineTo(0, r * 0.2);
                ctx.lineTo(-r * 0.35, 0);
                ctx.closePath();
                ctx.fillStyle = "#e53e3e";
                ctx.fill();

                // South half (white)
                ctx.beginPath();
                ctx.moveTo(0, r);
                ctx.lineTo(r * 0.35, 0);
                ctx.lineTo(0, r * 0.2);
                ctx.lineTo(-r * 0.35, 0);
                ctx.closePath();
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.9)" : "#f8fafc";
                ctx.fill();

                // Center dot
                ctx.shadowBlur = 0;
                ctx.beginPath();
                ctx.arc(0, 0, 4, 0, 2 * Math.PI);
                ctx.fillStyle = "#1e293b";
                ctx.fill();

                ctx.restore();

                // "N" label above arrow
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
                ctx.font = "bold 13px sans-serif";
                ctx.textAlign = "center";
                ctx.fillText("N", cx, cy - r - 6);
                ctx.textAlign = "left";
              }

              // ── 4. Legend (choropleth only) ───────────────────────────────
              if (include_legend) {
                const COLOR_PALETTES: Record<string, string[]> = {
                  YlOrRd: ["#ffffb2","#fed976","#feb24c","#fd8d3c","#f03b20","#bd0026"],
                  Blues:  ["#eff3ff","#bdd7e7","#6baed6","#3182bd","#08519c"],
                  Greens: ["#edf8e9","#bae4b3","#74c476","#31a354","#006d2c"],
                  Reds:   ["#fee5d9","#fcae91","#fb6a4a","#de2d26","#a50f15"],
                  Viridis:["#440154","#3b528b","#21908c","#5dc963","#fde725"],
                  Magma:  ["#000004","#3b0f70","#8c2981","#de4968","#feb078","#fcfdbf"],
                };
                const storeState = useHudStore.getState();
                const thematicLayer = storeState.layers.find(
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  (l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth"
                );
                if (thematicLayer) {
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  const meta = (thematicLayer.source as any).metadata as {
                    field: string;
                    breaks: number[];
                    palette: string;
                  };
                  const colors = COLOR_PALETTES[meta.palette] ?? COLOR_PALETTES["YlOrRd"];
                  const classes = meta.breaks.length - 1;
                  const itemH = 22, itemW = 18, padding = 10, gapX = 8;
                  const legendW = 180;
                  const legendH = padding * 2 + 24 + classes * itemH;
                  const lx = width - legendW - 56;
                  const ly = height - legendH - 56;

                  // Background panel
                  ctx.fillStyle = dark_mode
                    ? "rgba(0,10,20,0.82)"
                    : "rgba(255,255,255,0.88)";
                  ctx.beginPath();
                  // Rounded rect polyfill
                  const rad = 8;
                  ctx.moveTo(lx + rad, ly);
                  ctx.lineTo(lx + legendW - rad, ly);
                  ctx.arcTo(lx + legendW, ly, lx + legendW, ly + rad, rad);
                  ctx.lineTo(lx + legendW, ly + legendH - rad);
                  ctx.arcTo(lx + legendW, ly + legendH, lx + legendW - rad, ly + legendH, rad);
                  ctx.lineTo(lx + rad, ly + legendH);
                  ctx.arcTo(lx, ly + legendH, lx, ly + legendH - rad, rad);
                  ctx.lineTo(lx, ly + rad);
                  ctx.arcTo(lx, ly, lx + rad, ly, rad);
                  ctx.closePath();
                  ctx.fill();

                  // Field title
                  ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
                  ctx.font = "bold 12px sans-serif";
                  ctx.fillText(`字段: ${meta.field}`, lx + padding, ly + padding + 12);

                  // Color boxes + labels
                  const formatNum = (n: number) =>
                    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
                    n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
                    n.toFixed(1);

                  for (let i = 0; i < classes; i++) {
                    const iy = ly + padding + 24 + i * itemH;
                    const colorIdx = Math.min(i, colors.length - 1);
                    ctx.fillStyle = colors[colorIdx];
                    ctx.fillRect(lx + padding, iy, itemW, itemH - 4);
                    ctx.strokeStyle = "rgba(128,128,128,0.4)";
                    ctx.lineWidth = 0.5;
                    ctx.strokeRect(lx + padding, iy, itemW, itemH - 4);
                    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.85)" : "#334155";
                    ctx.font = "11px sans-serif";
                    ctx.fillText(
                      `${formatNum(meta.breaks[i])} – ${formatNum(meta.breaks[i + 1])}`,
                      lx + padding + itemW + gapX,
                      iy + itemH - 8
                    );
                  }
                }
              }

              // ── 5. Watermark ─────────────────────────────────────────────
              ctx.fillStyle = dark_mode ? "rgba(0,242,255,0.12)" : "rgba(30,41,59,0.08)";
              ctx.textAlign = "right";
              ctx.font = "14px monospace";
              ctx.fillText("Generated by WebGIS AI Agent", width - 36, height - 18);
              ctx.textAlign = "left";

              // ── 6. Upload / convert ───────────────────────────────────────
              const dataUrl = exportCanvas.toDataURL("image/png");
              const res = await fetch(dataUrl);
              const blob = await res.blob();

              const fmt = (format ?? "png").toLowerCase();

              if (fmt === "pdf") {
                // Send to backend PDF compositor
                const pdfForm = new FormData();
                pdfForm.append("file", blob, "export.png");
                if (title) pdfForm.append("title", title);
                if (subtitle) pdfForm.append("subtitle", subtitle);
                const centerLat = map.getCenter().lat;
                const zoom = map.getZoom();
                const mpp =
                  (156543.03392 * Math.cos((centerLat * Math.PI) / 180)) /
                  Math.pow(2, zoom);
                const mapWidthMeters = mpp * canvas.width;
                const scaleApprox = Math.round(mapWidthMeters / (canvas.width / 96 / 0.0254));
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
                // PNG path (existing behaviour)
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

          // bbox should be [west, south, east, north]
          const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, mapInstance, popAction]);

  return null;
}

export default MapActionHandler;