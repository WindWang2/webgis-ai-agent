import maplibregl from 'maplibre-gl';
import type { LegendSpec } from './types';
import { resolveStyle, type LayoutStyle } from './layout-style';
// Re-export the shared oversample helper so existing callers importing from
// './exporter' keep working, while the single source of truth lives in
// ./oversample (shared with the MapSpec-to-SVG compiler).
export { getOversampledZoom, computeOversampleBoost } from './oversample';

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

  // Combine: inject the map vector layers at the MAP_CONTENT_HERE anchor so
  // they paint at the bottom of the Z-order (marginalia stay on top).
  // P0-2 fix: previously injected before </svg>, making the map the LAST
  // child and painting it over the title banner / north arrow / legend.
  const PLACEHOLDER = '<!-- MAP_CONTENT_HERE -->';
  if (layoutSvg.includes(PLACEHOLDER)) {
    return layoutSvg.replace(PLACEHOLDER, layersMarkup);
  }
  // Fallback: layout without the anchor — inject right after the opening
  // <svg ...> tag so the map is still the first painted element.
  const openTagEnd = layoutSvg.indexOf('>');
  if (openTagEnd !== -1) {
    return layoutSvg.slice(0, openTagEnd + 1) + `\n  ${layersMarkup}\n` + layoutSvg.slice(openTagEnd + 1);
  }
  return layoutSvg;
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
 *
 * P0-3a fix: the SVG is now parsed with DOMParser and walked recursively,
 * accumulating ancestor `<g transform="translate(...)">` offsets, so the
 * north arrow / legend / scalebar render at their correct positions and
 * `<text>` / `<rect>` / `<line>` elements (previously dropped entirely by
 * order-sensitive regexes) are now rendered. fill/stroke opacity attributes
 * no longer break attribute parsing.
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

  // 2. Parse the SVG into a DOM tree and walk it, accumulating transforms.
  if (typeof DOMParser === 'undefined') {
    throw new Error('DOMParser is not available in the current environment');
  }
  const domParser = new DOMParser();
  const doc = domParser.parseFromString(svgString, 'image/svg+xml');
  const parseError = doc.querySelector ? doc.querySelector('parsererror') : null;
  if (parseError) {
    // Malformed SVG — fall back to an empty content PDF (title + frame only)
    // rather than throwing, so the export button never hard-fails.
  }

  // Accumulated translate offset from ancestor <g transform="translate(x,y)">.
  interface Offset { x: number; y: number; }

  function parseTranslate(transform: string | null): Offset {
    if (!transform) return { x: 0, y: 0 };
    // Handle "translate(x, y)" and "translate(x y)".
    const m = transform.match(/translate\(\s*([\d.\-]+)[\s,]+([\d.\-]+)\s*\)/);
    if (m) return { x: parseFloat(m[1]), y: parseFloat(m[2]) };
    const single = transform.match(/translate\(\s*([\d.\-]+)\s*\)/);
    if (single) return { x: parseFloat(single[1]), y: 0 };
    return { x: 0, y: 0 };
  }

  function attr(el: Element, name: string, fallback = '0'): string {
    return el.getAttribute(name) ?? fallback;
  }

  function num(el: Element, name: string, fallback = 0): number {
    const v = el.getAttribute(name);
    return v == null ? fallback : parseFloat(v);
  }

  // Convert a hex (#rrggbb) or #rgb color to [r,g,b] for jsPDF; returns null
  // for non-hex (e.g. named/css) so the caller can skip or default.
  function hexToRgb(hex: string): [number, number, number] | null {
    let h = hex.trim().replace(/^#/, '');
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16),
    ];
  }

  function applyFillColor(pdf: any, fill: string): boolean {
    const rgb = hexToRgb(fill);
    if (rgb) { pdf.setFillColor(rgb[0], rgb[1], rgb[2]); return true; }
    return false;
  }

  function applyDrawColor(pdf: any, stroke: string): boolean {
    const rgb = hexToRgb(stroke);
    if (rgb) { pdf.setDrawColor(rgb[0], rgb[1], rgb[2]); return true; }
    return false;
  }

  function renderNode(el: Element, offset: Offset): void {
    const tag = el.tagName.toLowerCase();
    // Descend into groups, accumulating their translate offset.
    if (tag === 'g' || tag === 'svg') {
      const t = parseTranslate(el.getAttribute('transform'));
      // Nested <svg> may carry x/y attributes too.
      const nx = num(el, 'x', 0);
      const ny = num(el, 'y', 0);
      const childOffset = { x: offset.x + t.x + nx, y: offset.y + t.y + ny };
      for (const child of Array.from(el.children)) {
        renderNode(child, childOffset);
      }
      return;
    }

    const ox = offset.x;
    const oy = offset.y;

    if (tag === 'circle') {
      const cx = num(el, 'cx') + ox;
      const cy = num(el, 'cy') + oy;
      const r = num(el, 'r');
      const fill = attr(el, 'fill', '#000000');
      if (applyFillColor(pdf, fill)) {
        pdf.circle(toMmX(cx), toMmY(cy), Math.max(0.1, r * scaleX), 'F');
      }
      return;
    }

    if (tag === 'line') {
      const x1 = num(el, 'x1') + ox;
      const y1 = num(el, 'y1') + oy;
      const x2 = num(el, 'x2') + ox;
      const y2 = num(el, 'y2') + oy;
      const stroke = attr(el, 'stroke', '#000000');
      const sw = num(el, 'stroke-width', 1);
      if (applyDrawColor(pdf, stroke)) {
        pdf.setLineWidth(Math.max(0.1, sw * scaleX));
        pdf.line(toMmX(x1), toMmY(y1), toMmX(x2), toMmY(y2));
      }
      return;
    }

    if (tag === 'rect') {
      const x = num(el, 'x') + ox;
      const y = num(el, 'y') + oy;
      const w = num(el, 'width');
      const h = num(el, 'height');
      const fill = el.getAttribute('fill');
      const stroke = el.getAttribute('stroke');
      if (fill && fill !== 'none' && applyFillColor(pdf, fill)) {
        pdf.rect(toMmX(x), toMmY(y), w * scaleX, h * scaleY, 'F');
      }
      if (stroke && stroke !== 'none' && applyDrawColor(pdf, stroke)) {
        const sw = num(el, 'stroke-width', 1);
        pdf.setLineWidth(Math.max(0.1, sw * scaleX));
        pdf.rect(toMmX(x), toMmY(y), w * scaleX, h * scaleY, 'S');
      }
      return;
    }

    if (tag === 'polygon') {
      const pointsStr = attr(el, 'points', '');
      const coords = pointsStr.trim().split(/[\s,]+/).map(Number).filter((n) => !isNaN(n));
      if (coords.length < 6) return; // need at least 3 (x,y) pairs
      const pts: [number, number][] = [];
      for (let i = 0; i + 1 < coords.length; i += 2) {
        pts.push([coords[i] + ox, coords[i + 1] + oy]);
      }
      const fill = el.getAttribute('fill');
      if (fill && fill !== 'none' && applyFillColor(pdf, fill) && pts.length >= 3) {
        const [sx, sy] = pts[0];
        const lines = pts.slice(1).map(([px, py]) => [
          toMmX(px) - toMmX(sx),
          toMmY(py) - toMmY(sy),
        ]);
        pdf.lines(lines, toMmX(sx), toMmY(sy), [1, 1], 'F');
      }
      const stroke = el.getAttribute('stroke');
      if (stroke && stroke !== 'none' && applyDrawColor(pdf, stroke) && pts.length >= 2) {
        const sw = num(el, 'stroke-width', 1);
        pdf.setLineWidth(Math.max(0.1, sw * scaleX));
        for (let i = 0; i < pts.length; i++) {
          const [x1, y1] = pts[i];
          const [x2, y2] = pts[(i + 1) % pts.length];
          pdf.line(toMmX(x1), toMmY(y1), toMmX(x2), toMmY(y2));
        }
      }
      return;
    }

    if (tag === 'path') {
      // Compiler emits "M x1,y1 L x2,y2 L ..." polylines only.
      const d = attr(el, 'd', '');
      const m = d.match(/M\s*([\d.\-,\s]+?)\s*L\s*([\d.\-,\s]+)/);
      if (!m) return;
      const head = m[1].split(',').map(Number);
      const allPts: [number, number][] = [[head[0] + ox, head[1] + oy]];
      const rest = m[2].split(' L ');
      for (const seg of rest) {
        const p = seg.split(',').map(Number);
        if (p.length === 2) allPts.push([p[0] + ox, p[1] + oy]);
      }
      const stroke = attr(el, 'stroke', '#000000');
      const sw = num(el, 'stroke-width', 1);
      if (applyDrawColor(pdf, stroke)) {
        pdf.setLineWidth(Math.max(0.1, sw * scaleX));
        for (let i = 0; i < allPts.length - 1; i++) {
          pdf.line(toMmX(allPts[i][0]), toMmY(allPts[i][1]), toMmX(allPts[i + 1][0]), toMmY(allPts[i + 1][1]));
        }
      }
      return;
    }

    if (tag === 'text') {
      const x = num(el, 'x') + ox;
      const y = num(el, 'y') + oy;
      const fontSize = num(el, 'font-size', 12);
      const fill = attr(el, 'fill', '#000000');
      const rgb = hexToRgb(fill);
      if (rgb) pdf.setTextColor(rgb[0], rgb[1], rgb[2]);
      // font-size in SVG px → pt: PDF unit is mm; jsPDF font size is pt.
      // Approximate px→pt (1px ≈ 0.75pt at 96dpi), keep legible minimum.
      pdf.setFontSize(Math.max(6, fontSize * 0.75));
      const txt = (el.textContent || '').trim();
      if (txt) pdf.text(txt, toMmX(x), toMmY(y));
      return;
    }
  }

  // Walk from the root <svg> element (skip the white background <rect> which
  // the PDF page already provides).
  const root = doc.documentElement;
  if (root) {
    for (const child of Array.from(root.children)) {
      renderNode(child, { x: 0, y: 0 });
    }
  }

  // 3. PDF-level Title Banner & footer (vector text, drawn last → on top).
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

  // Build the Blob from an ArrayBuffer rather than relying on jsPDF's
  // output('blob'). In jsdom the latter returns a Blob whose bytes are not
  // readable via the standard Response/arrayBuffer APIs (it stringifies to
  // "[object Blob]"), which broke the E2E magic-bytes test (#271). Going via
  // output('arraybuffer') + Uint8Array yields a Blob that reads correctly in
  // both browser and test environments.
  const ab = pdf.output('arraybuffer') as ArrayBuffer;
  return new Blob([new Uint8Array(ab)], { type: 'application/pdf' });
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
