/**
 * SVG Marginalia Vector Renderer — Pure SVG vector generators for Print Layouts.
 *
 * Generates resolution-independent SVG markup for North Arrows, Scalebars,
 * Legends, Title Blocks, and Frame Borders in standalone client-side SVG exports
 * and backend WeasyPrint PDF reports.
 */

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

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
  <polygon points="${halfW},${topY} ${halfW},${bottomY} 6,${midY}" fill="${color}" />
  <polygon points="${halfW},${topY} ${halfW},${bottomY} ${width - 6},${midY}" fill="${bg}" stroke="${color}" stroke-width="1" />
  <text x="${halfW}" y="${height - 2}" font-family="sans-serif" font-size="12" font-weight="bold" fill="${color}" text-anchor="middle">N</text>
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

  return `<svg width="${len + 16}" height="${height}" viewBox="0 0 ${len + 16} ${height}" xmlns="http://www.w3.org/2000/svg">
  <line x1="8" y1="16" x2="${len + 8}" y2="16" stroke="${color}" stroke-width="2" stroke-linecap="square" />
  <line x1="8" y1="10" x2="8" y2="16" stroke="${color}" stroke-width="2" />
  <line x1="${midX + 8}" y1="12" x2="${midX + 8}" y2="16" stroke="${color}" stroke-width="1.5" />
  <line x1="${len + 8}" y1="10" x2="${len + 8}" y2="16" stroke="${color}" stroke-width="2" />
  <text x="${midX + 8}" y="8" font-family="${fontFamily}" font-size="10" font-weight="600" fill="${color}" text-anchor="middle">${label}</text>
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

  let itemsSvg = "";
  items.forEach((item, idx) => {
    const y = padding + 22 + idx * itemHeight;
    let symbolSvg = "";
    if (item.type === "line") {
      symbolSvg = `<line x1="${padding}" y1="${y - 4}" x2="${padding + 16}" y2="${y - 4}" stroke="${item.color}" stroke-width="3" />`;
    } else if (item.type === "rect") {
      symbolSvg = `<rect x="${padding}" y="${y - 10}" width="14" height="10" fill="${item.color}" rx="1" />`;
    } else {
      symbolSvg = `<circle cx="${padding + 7}" cy="${y - 5}" r="5" fill="${item.color}" />`;
    }

    itemsSvg += `${symbolSvg}
    <text x="${padding + 24}" y="${y}" font-family="${fontFamily}" font-size="11" fill="${color}">${item.label}</text>`;
  });

  return `<svg width="${legendWidth}" height="${legendHeight}" viewBox="0 0 ${legendWidth} ${legendHeight}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="${legendWidth}" height="${legendHeight}" fill="${bg}" stroke="#cbd5e1" stroke-width="1" rx="6" />
  <text x="${padding}" y="${padding + 12}" font-family="${fontFamily}" font-size="12" font-weight="bold" fill="${color}">${title}</text>
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
    ? `<rect x="${margin + 4}" y="${margin + 4}" width="${width - (margin + 4) * 2}" height="${height - (margin + 4) * 2}" fill="none" stroke="${borderColor}" stroke-width="0.75" />`
    : "";

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" data-layout-id="${layoutId}" xmlns="http://www.w3.org/2000/svg">
  <!-- Outer Print Frame Border -->
  <rect x="${margin}" y="${margin}" width="${width - margin * 2}" height="${height - margin * 2}" fill="none" stroke="${borderColor}" stroke-width="2" rx="${isEngineering ? 0 : 4}" />
  ${borderExtra}

  <!-- Title Block Banner -->
  <g transform="translate(${margin + 16}, ${margin + 16})">
    <text x="0" y="24" font-family="${fontFamily}" font-size="22" font-weight="bold" fill="${color}">${title}</text>
    <text x="0" y="44" font-family="${fontFamily}" font-size="12" fill="${color}" opacity="0.75">${subtitle}</text>
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
