import maplibregl from 'maplibre-gl';
import type { LegendSpec } from './types';
import { resolveStyle, type LayoutStyle } from './layout-style';

/**
 * Captures the current map canvas and returns it as a Blob.
 * @param map The MapLibre map instance.
 * @returns A promise resolving to a PNG Blob.
 */
export async function captureMapCanvas(map: maplibregl.Map): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const canvas = map.getCanvas();
    // Using image/png by default for better quality
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error('Failed to capture map canvas'));
      }
    }, 'image/png');
  });
}

export interface ExportOptions {
  paperSize?: 'screen' | 'A4' | 'A3';
  orientation?: 'landscape' | 'portrait';
  dpi?: number;
}

/**
 * 审计 F33：composeLayout 的完整 options 类型。之前用 any，让未来 caller
 * 传部分字段时静默产生 NaN 渲染（如 mapCenter undefined -> NaN scale bar）。
 *
 * 审计 follow-up（CI Docker build）：初版漏了 legendSpec / heatmapLegend ——
 * map-action-handler.tsx 调用方传这两个字段，TS 报 "Object literal may only
 * specify known properties"。补全字段类型。
 */
export interface ComposeLayoutOptions {
  dpi?: number;
  theme?: 'light' | 'dark';
  showScale?: boolean;
  showCompass?: boolean;
  showWatermark?: boolean;
  showLegend?: boolean;
  showMetadata?: boolean;
  showGraticules?: boolean;
  author?: string;
  dataSource?: string;
  mapCenter?: { lat: number; lng: number };
  mapZoom?: number;
  mapBearing?: number;
  thematicLayer?: unknown;
  /** Structured legend spec from layer.legend_spec (graduated/continuous/categorical/divergent). */
  legendSpec?: LegendSpec;
  /** Heatmap gradient legend metadata; consumed when type === 'heatmap' layers are visible. */
  heatmapLegend?: { name?: string };
  /** Layout template style overrides (colors, fonts, margins, graticule, watermark). */
  style?: LayoutStyle;
}

/**
 * Prepares a new canvas for export, handling cropping and high-DPI upscaling.
 */
export function prepareExportCanvas(
  sourceCanvas: HTMLCanvasElement,
  options: ExportOptions = {}
): { canvas: HTMLCanvasElement; scaleX: number; scaleY: number; srcX: number; srcY: number; srcW: number; srcH: number } {
  const { paperSize = 'screen', orientation = 'landscape', dpi = 96 } = options;
  
  let srcW = sourceCanvas.width;
  let srcH = sourceCanvas.height;
  let srcX = 0;
  let srcY = 0;

  // 1. Calculate Crop Box if A4
  if (paperSize === 'A4' || paperSize === 'A3') {
    const targetRatio = orientation === 'landscape' ? 1.414 : 1 / 1.414;
    const canvasRatio = srcW / srcH;
    
    if (canvasRatio > targetRatio) {
      const newW = srcH * targetRatio;
      srcX = (srcW - newW) / 2;
      srcW = newW;
    } else {
      const newH = srcW / targetRatio;
      srcY = (srcH - newH) / 2;
      srcH = newH;
    }
  }

  // 2. High-DPI Upscaling calculation
  const dpiMultiplier = dpi / 96;
  const targetW = Math.round(srcW * dpiMultiplier);
  const targetH = Math.round(srcH * dpiMultiplier);

  const exportCanvas = document.createElement("canvas");
  exportCanvas.width = targetW;
  exportCanvas.height = targetH;
  const ctx = exportCanvas.getContext("2d");
  if (!ctx) throw new Error("Could not get canvas context");

  // Draw cropped base map
  ctx.drawImage(sourceCanvas, srcX, srcY, srcW, srcH, 0, 0, targetW, targetH);

  return {
    canvas: exportCanvas,
    scaleX: dpiMultiplier,
    scaleY: dpiMultiplier,
    srcX,
    srcY,
    srcW,
    srcH
  };
}

/**
 * Composes a professional map layout with title, subtitle, scale bar, and compass.
 * @param canvas The canvas element to draw on (containing the map image).
 * @param title The map title.
 * @param subtitle Optional subtitle.
 * @param options Configuration options (dpi, theme, showScale, showCompass, etc.)
 */
export function composeLayout(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: ComposeLayoutOptions = {}
) {
  const ctx = canvas.getContext('2d');
  // FE-21：之前 !ctx 时静默 return，调用方不检查返回值直接 toDataURL →
  // 上传空白画布然后报"导出成功"。改为抛异常让调用方的 catch 处理。
  if (!ctx) throw new Error('Failed to get 2d context for export canvas');

  const {
    dpi = 96,
    theme = 'light',
    showScale = true,
    showCompass = true,
    showWatermark = true,
    showLegend = true,
    showMetadata = true,
    showGraticules = false,
    author = '',
    dataSource = '',
    mapCenter,
    mapZoom,
    mapBearing = 0,
    thematicLayer,
  } = options;

  const dark_mode = theme === 'dark';
  const layoutStyle = resolveStyle(theme, options.style);
  const dpiMultiplier = dpi / 96;
  const scalePx = (val: number) => val * dpiMultiplier;
  const targetW = canvas.width;
  const targetH = canvas.height;
  const marginX = scalePx(layoutStyle.marginPx);

  // 1. Header gradient
  const headerH = subtitle ? scalePx(130) : scalePx(100);
  const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
  headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
  headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
  headerGrad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = headerGrad;
  ctx.fillRect(0, 0, targetW, headerH);

  // 2. Title
  ctx.fillStyle = layoutStyle.titleColor;
  ctx.font = layoutStyle.titleFont.includes('px') ? layoutStyle.titleFont : `bold ${scalePx(32)}px ${layoutStyle.fontFamily}`;
  ctx.fillText(title || "WebGIS AI Agent", marginX, scalePx(52));

  if (subtitle) {
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)";
    ctx.font = `${scalePx(20)}px ${layoutStyle.fontFamily}`;
    ctx.fillText(subtitle, marginX, scalePx(82));
  }

  // 3. Scale bar
  if (showScale && mapCenter && mapZoom !== undefined) {
    const metersPerPx =
      (156543.03392 * Math.cos((mapCenter.lat * Math.PI) / 180)) /
      Math.pow(2, mapZoom);
    
    const logicalW = targetW / dpiMultiplier;
    const targetPx = Math.round(logicalW * 0.12);
    const rawMeters = metersPerPx * targetPx;
    
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawMeters)));
    const nice = [1, 2, 5, 10].reduce((prev, n) => {
      const candidate = n * magnitude;
      return Math.abs(candidate - rawMeters) < Math.abs(prev - rawMeters)
        ? candidate
        : prev;
    }, magnitude);
    
    const barPx = (nice / metersPerPx) * dpiMultiplier;
    const barLabel = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;

    const bx = marginX, by = targetH - scalePx(52), bh = scalePx(8);
    ctx.strokeStyle = dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)";
    ctx.lineWidth = scalePx(1.5);
    ctx.strokeRect(bx, by, barPx, bh);
    
    const segCount = 4;
    const segW = barPx / segCount;
    for (let i = 0; i < segCount; i++) {
      ctx.fillStyle =
        i % 2 === 0
          ? dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)"
          : "rgba(0,0,0,0)";
      ctx.fillRect(bx + i * segW, by, segW, bh);
    }
    
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px ${layoutStyle.fontFamily}`;
    ctx.textAlign = "left";
    ctx.fillText("0", bx, by - scalePx(4));
    ctx.textAlign = "right";
    ctx.fillText(barLabel, bx + barPx, by - scalePx(4));
    ctx.textAlign = "left";
  }

  // 4. Compass
  if (showCompass) {
    const bearing = mapBearing;
    const cx = targetW - scalePx(64), cy = scalePx(64), r = scalePx(28);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((bearing * Math.PI) / 180);

    ctx.shadowColor = "rgba(0,0,0,0.4)";
    ctx.shadowBlur = scalePx(6);

    ctx.beginPath();
    ctx.moveTo(0, -r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = layoutStyle.accentColor || "#e53e3e";
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(0, r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.9)" : "#f8fafc";
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.arc(0, 0, scalePx(4), 0, 2 * Math.PI);
    ctx.fillStyle = "#1e293b";
    ctx.fill();

    ctx.restore();

    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px ${layoutStyle.fontFamily}`;
    ctx.textAlign = "center";
    ctx.fillText("N", cx, cy - r - scalePx(6));
    ctx.textAlign = "left";
  }

  // 4.5 Graticule / coordinate grid lines
  if (showGraticules && mapCenter && mapZoom !== undefined) {
    _drawGraticules(ctx, { dark_mode, scalePx, targetW, targetH, mapCenter, mapZoom, graticuleColor: layoutStyle.graticuleColor });
  }

  // 5. Legend
  const { heatmapLegend, legendSpec } = options;
  if (showLegend && (thematicLayer || heatmapLegend || legendSpec)) {
    _drawLegend(ctx, {
      dark_mode, scalePx, targetW, targetH,
      thematicLayer, heatmapLegend, legendSpec,
    });
  }

  // 6. Watermark
  if (showWatermark) {
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)";
    ctx.textAlign = "right";
    ctx.font = `bold ${scalePx(16)} monospace`;
    ctx.fillText(layoutStyle.watermarkText, targetW - scalePx(36), targetH - scalePx(18));
    ctx.textAlign = "left";
  }

  // 7. Metadata (author, date, CRS, data source)
  if (showMetadata) {
    const parts: string[] = [];
    if (author) parts.push(`作者: ${author}`);
    parts.push(`日期: ${new Date().toISOString().slice(0, 10)}`);
    if (mapCenter) parts.push(`CRS: EPSG:4326 (display)`);
    if (dataSource) parts.push(`数据: ${dataSource}`);

    if (parts.length > 0) {
      ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.3)";
      ctx.font = `${scalePx(10)}px sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(parts.join('  |  '), scalePx(56), targetH - scalePx(18));
      ctx.textAlign = "left";
    }
  }
}

// ── Legend drawing helpers ──────────────────────────────────────────

// ── Graticule drawing ──────────────────────────────────────────────

function _drawGraticules(
  ctx: CanvasRenderingContext2D,
  opts: {
    dark_mode: boolean;
    scalePx: (v: number) => number;
    targetW: number;
    targetH: number;
    mapCenter: { lat: number; lng: number };
    mapZoom: number;
    graticuleColor?: string;
  }
) {
  const { dark_mode, scalePx, targetW, targetH, mapCenter, mapZoom, graticuleColor } = opts;

  // Calculate graticule interval from zoom level
  const intervals = [30, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01];
  const zoomIndex = Math.max(0, Math.min(Math.floor((mapZoom - 1) / 2), intervals.length - 1));
  const interval = intervals[zoomIndex];

  // Calculate geographic extent from center and zoom
  // Web Mercator: metersPerPixel = 156543.03392 * cos(lat) / 2^zoom
  const metersPerPixel = (156543.03392 * Math.cos((mapCenter.lat * Math.PI) / 180)) / Math.pow(2, mapZoom);
  const halfWidthMeters = (targetW / 2) * metersPerPixel;
  const halfHeightMeters = (targetH / 2) * metersPerPixel;

  // Convert meters to degrees (approximate)
  const metersPerDegree = 111319.9;
  const halfWidthDeg = halfWidthMeters / metersPerDegree;
  const halfHeightDeg = halfHeightMeters / (metersPerDegree * Math.cos((mapCenter.lat * Math.PI) / 180));

  const minLng = mapCenter.lng - halfWidthDeg;
  const maxLng = mapCenter.lng + halfWidthDeg;
  const minLat = mapCenter.lat - halfHeightDeg;
  const maxLat = mapCenter.lat + halfHeightDeg;

  // Snap to interval grid
  const startLng = Math.floor(minLng / interval) * interval;
  const startLat = Math.floor(minLat / interval) * interval;

  ctx.save();
  ctx.strokeStyle = graticuleColor || (dark_mode ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.12)");
  ctx.lineWidth = scalePx(0.5);
  ctx.setLineDash([scalePx(4), scalePx(4)]);
  ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.4)" : "rgba(0,0,0,0.35)";
  ctx.font = `${scalePx(9)}px sans-serif`;

  // Draw longitude lines (vertical)
  for (let lng = startLng; lng <= maxLng; lng += interval) {
    const x = ((lng - minLng) / (maxLng - minLng)) * targetW;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, targetH);
    ctx.stroke();
    // Label at bottom
    ctx.textAlign = "center";
    ctx.fillText(`${Math.abs(lng).toFixed(interval < 1 ? 1 : 0)}°${lng >= 0 ? 'E' : 'W'}`, x, targetH - scalePx(22));
  }

  // Draw latitude lines (horizontal)
  for (let lat = startLat; lat <= maxLat; lat += interval) {
    const y = targetH - ((lat - minLat) / (maxLat - minLat)) * targetH;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(targetW, y);
    ctx.stroke();
    // Label at left
    ctx.textAlign = "left";
    ctx.fillText(`${Math.abs(lat).toFixed(interval < 1 ? 1 : 0)}°${lat >= 0 ? 'N' : 'S'}`, scalePx(4), y - scalePx(3));
  }

  ctx.setLineDash([]);
  ctx.restore();
}

const HEATMAP_COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const HEATMAP_LABELS = ['极低', '低', '中', '高', '极高'];

interface LegendDrawCtx {
  ctx: CanvasRenderingContext2D;
  dark_mode: boolean;
  scalePx: (v: number) => number;
  targetW: number;
  targetH: number;
}

function _drawLegendBox(ld: LegendDrawCtx, legendW: number, legendH: number, drawContent: (lx: number, ly: number) => void, yOffset: number = 0) {
  const { ctx, scalePx, targetW, targetH, dark_mode } = ld;
  const lx = targetW - legendW - scalePx(56);
  const ly = targetH - legendH - scalePx(56) - yOffset;

  ctx.fillStyle = dark_mode ? "rgba(0,10,20,0.82)" : "rgba(255,255,255,0.88)";
  ctx.beginPath();
  const rad = scalePx(8);
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

  drawContent(lx, ly);
}

function _drawHeatmapLegend(ld: LegendDrawCtx, name?: string, yOffset: number = 0): number {
  const { ctx, scalePx } = ld;
  const padding = scalePx(10);
  const barH = scalePx(8);
  const gradientW = scalePx(140);
  const gradientLabelH = scalePx(16);
  const titleH = name ? scalePx(20) : 0;
  const legendW = padding * 2 + gradientW + scalePx(6);
  const legendH = padding * 2 + titleH + barH + gradientLabelH + scalePx(4);

  _drawLegendBox(ld, legendW, legendH, (lx, ly) => {
    let y = ly + padding;

    if (name) {
      ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.7)" : "rgba(100,116,139,0.9)";
      ctx.font = `${scalePx(10)}px monospace`;
      ctx.fillText(name.toUpperCase(), lx + padding, y + scalePx(12));
      y += scalePx(18);
    }

    // Gradient bar: 5 color segments
    const segW = gradientW / HEATMAP_COLORS.length;
    for (let i = 0; i < HEATMAP_COLORS.length; i++) {
      ctx.fillStyle = HEATMAP_COLORS[i];
      ctx.fillRect(lx + padding + i * segW, y, segW + 1, barH);
    }

    // Labels below gradient
    y += barH + scalePx(4);
    ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.6)" : "rgba(100,116,139,0.8)";
    ctx.font = `${scalePx(10)}px sans-serif`;
    ctx.textAlign = "left";
    ctx.fillText(HEATMAP_LABELS[0], lx + padding, y + scalePx(10));
    ctx.textAlign = "right";
    ctx.fillText(HEATMAP_LABELS[HEATMAP_LABELS.length - 1], lx + padding + gradientW, y + scalePx(10));
    ctx.textAlign = "center";
    for (let i = 1; i < HEATMAP_LABELS.length - 1; i++) {
      ctx.fillText(HEATMAP_LABELS[i], lx + padding + (i / (HEATMAP_LABELS.length - 1)) * gradientW, y + scalePx(10));
    }
    ctx.textAlign = "left";
  }, yOffset);
  return legendH + scalePx(10);
}

const COLOR_PALETTES: Record<string, string[]> = {
  YlOrRd: ["#ffffb2","#fed976","#feb24c","#fd8d3c","#f03b20","#bd0026"],
  Blues:  ["#eff3ff","#bdd7e7","#6baed6","#3182bd","#08519c"],
  Greens: ["#edf8e9","#bae4b3","#74c476","#31a354","#006d2c"],
  Reds:   ["#fee5d9","#fcae91","#fb6a4a","#de2d26","#a50f15"],
  Viridis:["#440154","#3b528b","#21908c","#5dc963","#fde725"],
  Magma:  ["#000004","#3b0f70","#8c2981","#de4968","#feb078","#fcfdbf"],
};

function _drawDiscreteLegend(ld: LegendDrawCtx, field: string, colors: string[], labels: string[], yOffset: number = 0): number {
  const { ctx, scalePx } = ld;
  const classes = Math.min(colors.length, labels.length);
  const itemH = scalePx(22), itemW = scalePx(18), padding = scalePx(10), gapX = scalePx(8);

  ctx.font = `${scalePx(11)}px sans-serif`;
  let maxTextW = 0;
  for (const label of labels) {
    maxTextW = Math.max(maxTextW, ctx.measureText(label).width);
  }

  const legendW = padding * 2 + itemW + gapX + maxTextW + scalePx(10);
  const legendH = padding * 2 + scalePx(24) + classes * itemH;

  _drawLegendBox(ld, legendW, legendH, (lx, ly) => {
    ctx.fillStyle = ld.dark_mode ? "#00f2ff" : "#1e293b";
    ctx.font = `bold ${scalePx(12)}px sans-serif`;
    ctx.fillText(`字段: ${field}`, lx + padding, ly + padding + scalePx(12));

    for (let i = 0; i < classes; i++) {
      const iy = ly + padding + scalePx(24) + i * itemH;
      ctx.fillStyle = colors[i];
      ctx.fillRect(lx + padding, iy, itemW, itemH - scalePx(4));
      ctx.strokeStyle = "rgba(128,128,128,0.4)";
      ctx.lineWidth = scalePx(0.5);
      ctx.strokeRect(lx + padding, iy, itemW, itemH - scalePx(4));
      ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.85)" : "#334155";
      ctx.font = `${scalePx(11)}px sans-serif`;
      ctx.fillText(labels[i], lx + padding + itemW + gapX, iy + itemH - scalePx(8));
    }
  }, yOffset);
  return legendH + scalePx(10);
}

function _drawLegend(
  ctx: CanvasRenderingContext2D,
  opts: {
    dark_mode: boolean;
    scalePx: (v: number) => number;
    targetW: number;
    targetH: number;
    thematicLayer?: any;
    heatmapLegend?: { name?: string };
    legendSpec?: any;
  }
) {
  const ld: LegendDrawCtx = {
    ctx,
    dark_mode: opts.dark_mode,
    scalePx: opts.scalePx,
    targetW: opts.targetW,
    targetH: opts.targetH,
  };

  let yOffset = 0;

  // Legend 1: LegendSpec from layer.legend_spec (structured, typed)
  if (opts.legendSpec) {
    const spec = opts.legendSpec;
    if (spec.type === 'graduated' || spec.type === 'continuous' || spec.type === 'divergent') {
      const colors = spec.palette_colors || COLOR_PALETTES[spec.palette] || COLOR_PALETTES['YlOrRd'];
      const formatNum = (n: number) =>
        n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
        n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
        n.toFixed(1);
      const labels: string[] = [];
      if (spec.breaks && spec.breaks.length >= 2) {
        for (let i = 0; i < spec.breaks.length - 1; i++) {
          labels.push(`${formatNum(spec.breaks[i])} – ${formatNum(spec.breaks[i + 1])}`);
        }
      } else if (spec.type === 'continuous' && spec.min !== undefined && spec.max !== undefined) {
        labels.push(formatNum(spec.min));
        labels.push(formatNum((spec.min + spec.max) / 2));
        labels.push(formatNum(spec.max));
        while (labels.length < colors.length) labels.push('');
      }
      yOffset += _drawDiscreteLegend(ld, spec.field || '未知字段', colors, labels, yOffset);
    } else if (spec.type === 'categorical') {
      const colors = (spec.categories || []).map((c: any) => c.color);
      const labels = (spec.categories || []).map((c: any) => c.label || c.key);
      yOffset += _drawDiscreteLegend(ld, spec.field || '未知字段', colors, labels, yOffset);
    }
  }

  // Legend 2: Heatmap gradient legend
  if (opts.heatmapLegend) {
    yOffset += _drawHeatmapLegend(ld, opts.heatmapLegend.name, yOffset);
  }

  // Legend 3: Legacy thematicLayer (ThematicStyleDef shape)
  if (opts.thematicLayer) {
    const styleDef = opts.thematicLayer as any;
    const field = styleDef.field || '未知字段';
    let colors: string[] = styleDef.colors || [];
    let labels: string[] = styleDef.legend_labels || [];

    const meta = (styleDef.source as any)?.metadata;
    if (meta && meta.breaks && meta.palette) {
      colors = COLOR_PALETTES[meta.palette] ?? COLOR_PALETTES["YlOrRd"];
      const formatNum = (n: number) =>
        n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
        n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
        n.toFixed(1);
      labels = [];
      for (let i = 0; i < meta.breaks.length - 1; i++) {
        labels.push(`${formatNum(meta.breaks[i])} – ${formatNum(meta.breaks[i + 1])}`);
      }
    }

    if (colors.length > 0 && labels.length > 0) {
      yOffset += _drawDiscreteLegend(ld, field, colors, labels, yOffset);
    }
  }
}

/**
 * Export the composed canvas as a PDF using jsPDF (client-side, vector text).
 * @param canvas The composed export canvas (with map + layout elements already drawn)
 * @param title Map title
 * @param subtitle Optional subtitle
 * @param options Export options
 * @returns A Blob containing the PDF
 */
export async function exportToPDF(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: {
    paperSize?: 'A4' | 'A3';
    orientation?: 'landscape' | 'portrait';
    author?: string;
    dataSource?: string;
  } = {}
): Promise<Blob> {
  const { default: jsPDF } = await import('jspdf');
  const { paperSize = 'A4', orientation = 'landscape', author, dataSource } = options;

  const doc = new jsPDF({
    orientation,
    unit: 'mm',
    format: paperSize === 'A3' ? 'a3' : 'a4',
  });

  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 10;

  // Map image area
  const mapTop = 25;
  const mapBottom = 15;
  const mapW = pageW - margin * 2;
  const mapH = pageH - mapTop - mapBottom;
  const mapX = margin;
  const mapY = mapTop;

  // Add map image
  const imgData = canvas.toDataURL('image/png');
  doc.addImage(imgData, 'PNG', mapX, mapY, mapW, mapH);

  // Border around map
  doc.setDrawColor(200);
  doc.setLineWidth(0.3);
  doc.rect(mapX, mapY, mapW, mapH);

  // Title
  doc.setFontSize(16);
  doc.setTextColor(30, 41, 59);
  doc.text(title || 'WebGIS AI Agent', pageW / 2, 15, { align: 'center' });

  // Subtitle
  if (subtitle) {
    doc.setFontSize(10);
    doc.setTextColor(100, 116, 139);
    doc.text(subtitle, pageW / 2, 21, { align: 'center' });
  }

  // Footer
  const dateStr = new Date().toISOString().slice(0, 10);
  const footerParts = [`日期: ${dateStr}`];
  if (author) footerParts.push(`作者: ${author}`);
  if (dataSource) footerParts.push(`数据: ${dataSource}`);
  footerParts.push('Generated by WebGIS AI Agent');

  doc.setFontSize(7);
  doc.setTextColor(148, 163, 184);
  doc.text(footerParts.join('  |  '), pageW / 2, pageH - 5, { align: 'center' });

  // PDF metadata
  doc.setProperties({
    title: title || 'WebGIS AI Agent',
    author: author || 'WebGIS AI Agent',
    subject: subtitle || '',
    creator: 'WebGIS AI Agent',
  });

  return doc.output('blob');
}

export interface VectorExportOptions {
  title?: string;
  subtitle?: string;
  layoutId?: string;
  dpi?: number;
  width?: number;
  height?: number;
}

/**
 * Generates pure vector SVG string representation for a MapSpec and Print Layout.
 */
export async function generateMapSpecVectorSvgString(
  mapspec: any,
  options: VectorExportOptions = {}
): Promise<string> {
  const { compileMapSpecToSvg } = await import('../mapspec-compiler/mapspec-to-svg');
  const { renderSvgPrintLayout } = await import('./svg-marginalia');

  const dpi = options.dpi ?? 300;
  const width = options.width ?? 1200;
  const height = options.height ?? 848;
  const layoutId = options.layoutId ?? 'tmpl_ly_academic';

  // 1. Compile pure vector SVG layers from MapSpec
  const mapSvgContent = compileMapSpecToSvg(mapspec, {
    targetDpi: dpi,
    width,
    height,
  });

  // Extract layer inner markup (<g class="mapspec-vector-layers">...</g>)
  const layerMatch = mapSvgContent.match(/<g class="mapspec-vector-layers">[\s\S]*?<\/g>/);
  const layersMarkup = layerMatch ? layerMatch[0] : '';

  // 2. Generate Marginalia SVG print layout container
  const layoutSvg = renderSvgPrintLayout({
    layoutId,
    width,
    height,
    title: options.title ?? '高清矢量地图',
    subtitle: options.subtitle ?? 'WebGIS AI Agent HD Vector Export',
  });

  // Combine layers inside layout container before closing </svg>
  return layoutSvg.replace('</svg>', `  ${layersMarkup}\n</svg>`);
}

/**
 * Exports a MapSpec directly to a 100% resolution-independent SVG vector file Blob.
 */
export async function exportMapSpecToVectorSvg(
  mapspec: any,
  options: VectorExportOptions = {}
): Promise<Blob> {
  const svgText = await generateMapSpecVectorSvgString(mapspec, options);
  return new Blob([svgText], { type: 'image/svg+xml' });
}

/**
 * Exports a MapSpec to a high-definition 300+ DPI vector PDF file.
 */
export async function exportMapSpecToVectorPdf(
  mapspec: any,
  options: VectorExportOptions = {}
): Promise<Blob> {
  const { default: jsPDF } = await import('jspdf');
  const pdf = new jsPDF({
    orientation: 'landscape',
    unit: 'mm',
    format: 'a4',
  });

  const pageW = pdf.internal.pageSize.getWidth(); // ~297 mm
  const pageH = pdf.internal.pageSize.getHeight(); // ~210 mm

  // 1. Generate full vector SVG string from MapSpec and Print Layout
  const svgString = await generateMapSpecVectorSvgString(mapspec, {
    ...options,
    width: 1200,
    height: 848,
  });

  // SVG coordinate to PDF mm scale factors
  const scaleX = (pageW - 20) / 1200;
  const scaleY = (pageH - 24) / 848;
  const toMmX = (x: number) => 10 + x * scaleX;
  const toMmY = (y: number) => 12 + y * scaleY;

  // 2. Parse and render SVG circles
  const circleMatches = svgString.matchAll(/<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)" fill="([^"]+)"/g);
  for (const match of circleMatches) {
    const cx = toMmX(parseFloat(match[1]));
    const cy = toMmY(parseFloat(match[2]));
    const r = parseFloat(match[3]) * scaleX;
    const color = match[4];
    pdf.setFillColor(color);
    pdf.circle(cx, cy, r, 'F');
  }

  // 3. Parse and render SVG paths (LineString)
  const pathMatches = svgString.matchAll(/<path d="M ([^"]+)" stroke="([^"]+)" stroke-width="([\d.]+)"/g);
  for (const match of pathMatches) {
    const pointsStr = match[1];
    const color = match[2];
    const width = parseFloat(match[3]) * scaleX;
    const coords = pointsStr.split(' L ').map((p) => p.split(',').map(Number));

    pdf.setDrawColor(color);
    pdf.setLineWidth(Math.max(0.2, width));
    for (let i = 0; i < coords.length - 1; i++) {
      const [x1, y1] = coords[i];
      const [x2, y2] = coords[i + 1];
      if (!isNaN(x1) && !isNaN(y1) && !isNaN(x2) && !isNaN(y2)) {
        pdf.line(toMmX(x1), toMmY(y1), toMmX(x2), toMmY(y2));
      }
    }
  }

  // 4. Parse and render SVG polygons (Fill)
  const polyMatches = svgString.matchAll(/<polygon points="([^"]+)" fill="([^"]+)"/g);
  for (const match of polyMatches) {
    const pointsStr = match[1];
    const color = match[2];
    const coords = pointsStr.split(' ').map((p) => p.split(',').map(Number));
    if (coords.length >= 3) {
      pdf.setFillColor(color);
      const [startX, startY] = coords[0];
      const lines = coords.slice(1).map(([x, y]) => [toMmX(x) - toMmX(startX), toMmY(y) - toMmY(startY)]);
      pdf.lines(lines, toMmX(startX), toMmY(startY), [1, 1], 'F');
    }
  }

  // 5. Draw PDF Title Banner & Vector Marginalia Text
  pdf.setFontSize(14);
  pdf.setTextColor(30, 41, 59);
  pdf.text(options.title || 'WebGIS AI Agent HD Vector Map', pageW / 2, 10, { align: 'center' });

  if (options.subtitle) {
    pdf.setFontSize(9);
    pdf.setTextColor(100, 116, 139);
    pdf.text(options.subtitle, pageW / 2, 15, { align: 'center' });
  }

  pdf.setDrawColor(30, 58, 138);
  pdf.setLineWidth(0.5);
  pdf.rect(10, 18, pageW - 20, pageH - 26);

  pdf.setFontSize(8);
  pdf.setTextColor(148, 163, 184);
  pdf.text('Generated by WebGIS AI Agent (Infinite Resolution Vector PDF)', pageW / 2, pageH - 3, { align: 'center' });

  return pdf.output('blob');
}

/**
 * Triggers a file download for a Blob.
 * @param blob The Blob to download.
 * @param filename The name of the file.
 */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
