/**
 * SVG Marginalia Vector Renderer — Pure SVG vector generators for Print Layouts.
 *
 * Generates resolution-independent SVG markup for North Arrows, Scalebars,
 * Legends, Title Blocks, and Frame Borders in standalone client-side SVG exports
 * and backend WeasyPrint PDF reports.
 */

// ── W5（ADR-0118）：真矢量导出对接增量 ─────────────────────────────────
// 既有导出（renderSvgNorthArrow 等）保持原样；以下为 vector-svg-export 的
// 组装层准备的增量 API —— 纯片段（不自带 <svg> 根）与共享转义。

/**
 * 转义进 SVG 文本/属性的字符串（html.escape(s, quote=True) 同链 —— 与孪生
 * 编译器 mapspec-to-svg.escapeSvgAttr 同一最小转义集，防属性逃逸/注入）。
 */
export function escapeSvgText(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export interface FrameBorderOptions {
  width: number;
  height: number;
  margin?: number;
  color?: string;
}

/** 图框（纯 <rect> 片段 —— 调用方组合进外层画布坐标系，与 academic 外框同形态）。 */
export function renderSvgFrameBorder(options: FrameBorderOptions): string {
  const margin = options.margin ?? 24;
  const color = options.color ?? "#1e3a8a";
  const x = margin;
  const y = margin;
  const w = Math.max(options.width - margin * 2, 0);
  const h = Math.max(options.height - margin * 2, 0);
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${escapeSvgText(color)}" stroke-width="2" rx="4" />`;
}

export interface NorthArrowOptions {
  width?: number;
  height?: number;
  color?: string;
  backgroundColor?: string;
}

export function renderSvgNorthArrow(options: NorthArrowOptions = {}): string {
  const width = options.width ?? 40;
  const height = options.height ?? 40;
  const color = options.color ?? "#2563eb";
  const bg = options.backgroundColor ?? "#ffffff";

  const halfW = width / 2;
  const topY = 6;
  const bottomY = height - 14;
  const midY = height / 2;

  const escColor = escapeSvgText(color);
  const escBg = escapeSvgText(bg);

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
  <polygon points="${halfW},${topY} ${halfW},${bottomY} 6,${midY}" fill="${escColor}" />
  <polygon points="${halfW},${topY} ${halfW},${bottomY} ${width - 6},${midY}" fill="${escBg}" stroke="${escColor}" stroke-width="1" />
  <text x="${halfW}" y="${height - 2}" font-family="sans-serif" font-size="12" font-weight="bold" fill="${escColor}" text-anchor="middle">N</text>
</svg>`;
}

export interface ScalebarOptions {
  lengthPx?: number;
  labelText?: string;
  color?: string;
  fontFamily?: string;
}

export function renderSvgScalebar(options: ScalebarOptions = {}): string {
  const len = options.lengthPx ?? 100;
  const label = options.labelText ?? "1 km";
  const color = options.color ?? "#1e293b";
  const fontFamily = options.fontFamily ?? "sans-serif";
  const height = 28;
  const midX = len / 2;

  const escColor = escapeSvgText(color);
  const escFont = escapeSvgText(fontFamily);
  const escLabel = escapeSvgText(label);

  return `<svg width="${len + 16}" height="${height}" viewBox="0 0 ${len + 16} ${height}" xmlns="http://www.w3.org/2000/svg">
  <line x1="8" y1="16" x2="${len + 8}" y2="16" stroke="${escColor}" stroke-width="2" stroke-linecap="square" />
  <line x1="8" y1="10" x2="8" y2="16" stroke="${escColor}" stroke-width="2" />
  <line x1="${midX + 8}" y1="12" x2="${midX + 8}" y2="16" stroke="${escColor}" stroke-width="1.5" />
  <line x1="${len + 8}" y1="10" x2="${len + 8}" y2="16" stroke="${escColor}" stroke-width="2" />
  <text x="${midX + 8}" y="8" font-family="${escFont}" font-size="10" font-weight="600" fill="${escColor}" text-anchor="middle">${escLabel}</text>
</svg>`;
}

export interface LegendItem {
  label: string;
  color: string;
  type?: "circle" | "line" | "rect";
}

export interface LegendOptions {
  title?: string;
  items?: LegendItem[];
  color?: string;
  backgroundColor?: string;
  fontFamily?: string;
}

export function renderSvgLegend(options: LegendOptions = {}): string {
  const title = options.title ?? "图例 Legend";
  const items = options.items ?? [];
  const color = options.color ?? "#1e293b";
  const bg = options.backgroundColor ?? "rgba(255, 255, 255, 0.9)";
  const fontFamily = options.fontFamily ?? "sans-serif";

  const padding = 12;
  const itemHeight = 20;
  const legendWidth = 160;
  const legendHeight = padding * 2 + 18 + items.length * itemHeight;

  const escColor = escapeSvgText(color);
  const escBg = escapeSvgText(bg);
  const escFont = escapeSvgText(fontFamily);
  const escTitle = escapeSvgText(title);

  let itemsSvg = "";
  items.forEach((item, idx) => {
    const y = padding + 22 + idx * itemHeight;
    const escItemColor = escapeSvgText(item.color);
    const escItemLabel = escapeSvgText(item.label);
    let symbolSvg = "";
    if (item.type === "line") {
      symbolSvg = `<line x1="${padding}" y1="${y - 4}" x2="${padding + 16}" y2="${y - 4}" stroke="${escItemColor}" stroke-width="3" />`;
    } else if (item.type === "rect") {
      symbolSvg = `<rect x="${padding}" y="${y - 10}" width="14" height="10" fill="${escItemColor}" rx="1" />`;
    } else {
      symbolSvg = `<circle cx="${padding + 7}" cy="${y - 5}" r="5" fill="${escItemColor}" />`;
    }

    itemsSvg += `${symbolSvg}
    <text x="${padding + 24}" y="${y}" font-family="${escFont}" font-size="11" fill="${escColor}">${escItemLabel}</text>`;
  });

  return `<svg width="${legendWidth}" height="${legendHeight}" viewBox="0 0 ${legendWidth} ${legendHeight}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="${legendWidth}" height="${legendHeight}" fill="${escBg}" stroke="#cbd5e1" stroke-width="1" rx="6" />
  <text x="${padding}" y="${padding + 12}" font-family="${escFont}" font-size="12" font-weight="bold" fill="${escColor}">${escTitle}</text>
  ${itemsSvg}
</svg>`;
}

export interface PrintLayoutOptions {
  layoutId?: string;
  width?: number;
  height?: number;
  title?: string;
  subtitle?: string;
  legendItems?: LegendItem[];
  scaleLabel?: string;
  theme?: "light" | "dark";
}

export function renderSvgPrintLayout(options: PrintLayoutOptions = {}): string {
  const layoutId = options.layoutId ?? "tmpl_ly_academic";
  const isEngineering = layoutId === "tmpl_ly_engineering";
  const isDarkReport = layoutId === "tmpl_ly_dark_report" || options.theme === "dark";

  // A4 Ratio default: 1200x848 (A4 landscape ratio)
  const width = options.width ?? (isDarkReport ? 1280 : 1200);
  const height = options.height ?? (isDarkReport ? 720 : 848);
  const title = options.title ?? "高清地图 Print Layout";
  const subtitle = options.subtitle ?? "WebGIS AI Agent High-Definition Export";
  const scaleLabel = options.scaleLabel ?? "5 km";

  const color = isDarkReport ? "#f8fafc" : "#0f172a";
  const borderColor = isDarkReport ? "#38bdf8" : isEngineering ? "#334155" : "#1e3a8a";
  const fontFamily = isEngineering ? "monospace" : "sans-serif";
  const margin = isEngineering ? 20 : 28;

  const escColor = escapeSvgText(color);
  const escBorderColor = escapeSvgText(borderColor);
  const escFont = escapeSvgText(fontFamily);
  const escTitle = escapeSvgText(title);
  const escSubtitle = escapeSvgText(subtitle);
  const escLayoutId = escapeSvgText(layoutId);

  const northArrowSvg = renderSvgNorthArrow({ width: 44, height: 44, color: borderColor });
  const scalebarSvg = renderSvgScalebar({ lengthPx: 120, labelText: scaleLabel, color, fontFamily });
  const legendSvg = renderSvgLegend({
    title: "图例 Legend",
    items: options.legendItems ?? [
      { label: "分析图层", color: "#3b82f6", type: "circle" },
      { label: "边界界线", color: "#ec4899", type: "line" },
    ],
    color,
    fontFamily,
    backgroundColor: isDarkReport ? "rgba(15, 23, 42, 0.85)" : "rgba(255, 255, 255, 0.9)",
  });

  const borderExtra = !isDarkReport && !isEngineering
    ? `<rect x="${margin + 4}" y="${margin + 4}" width="${width - (margin + 4) * 2}" height="${height - (margin + 4) * 2}" fill="none" stroke="${escBorderColor}" stroke-width="0.75" />`
    : "";

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" data-layout-id="${escLayoutId}" xmlns="http://www.w3.org/2000/svg">
  <!-- MAP_CONTENT_HERE: compiled MapSpec vector layers are injected at this
       anchor by generateMapSpecVectorSvgString. Placing it BEFORE the frame
       and marginalia groups ensures the map paints at the bottom of the
       Z-order, so title banner / north arrow / legend stay visible on top. -->
  <!-- Outer Print Frame Border -->
  <rect x="${margin}" y="${margin}" width="${width - margin * 2}" height="${height - margin * 2}" fill="none" stroke="${escBorderColor}" stroke-width="2" rx="${isEngineering ? 0 : 4}" />
  ${borderExtra}

  <!-- Title Block Banner -->
  <g transform="translate(${margin + 16}, ${margin + 16})">
    <text x="0" y="24" font-family="${escFont}" font-size="22" font-weight="bold" fill="${escColor}">${escTitle}</text>
    <text x="0" y="44" font-family="${escFont}" font-size="12" fill="${escColor}" opacity="0.75">${escSubtitle}</text>
  </g>

  <!-- North Arrow (Top Right) -->
  <g transform="translate(${width - margin - 60}, ${margin + 16})">
    ${northArrowSvg}
  </g>

  <!-- Legend Box (Bottom Left) -->
  <g transform="translate(${margin + 16}, ${height - margin - 110})">
    ${legendSvg}
  </g>

  <!-- Scalebar (Bottom Right) -->
  <g transform="translate(${width - margin - 160}, ${height - margin - 40})">
    ${scalebarSvg}
  </g>
</svg>`;
}
