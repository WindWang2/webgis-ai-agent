/**
 * V5（ADR-0118 W5）：真矢量 SVG 导出 —— 复活 #844 删除的真矢量链路。
 *
 * 分工（不改任何既有语义源）：
 * - 数据层：既有孪生编译器 compileMapSpecToSvg（mapspec-to-svg.ts，TS/Python
 *   parity 锁定）产出矢量要素（path/circle/polygon/text）；
 * - 整饰层：svg-marginalia 的纯 SVG 生成器（图框/指北针/比例尺/图例）叠加
 *   在数据层之上（与 print layout 同 Z 序：地图在最底、title/整饰在上）；
 * - 诊断：编译/合成任何异常 → 调用方注入的 fallbackRaster 位图回退 +
 *   `vector_svg_fallback_raster`（warning）；成功路径发
 *   `basemap_omitted_vector_svg`（info —— 矢量件不含栅格底图，如实披露）。
 *
 * 长文本：对编译产物做确定性后处理 —— `<text>` 内容 >60 code points（与后端
 * MAX_SVG_LABEL_CHARS=60 同口径）截为前 59 + "…"（BMP 内与 Python 侧
 * code-point 截断一致），每处截断一条 `label_truncated` 诊断。
 */
import { compileMapSpecToSvg } from '@/lib/mapspec-compiler/mapspec-to-svg';
import {
  escapeSvgText,
  renderSvgFrameBorder,
  renderSvgLegend,
  renderSvgNorthArrow,
  renderSvgScalebar,
} from './svg-marginalia';
import { computeNiceScale, formatScaleLabel } from './scale-math';
import type { ExportChromeModel, ExportDegradation } from './export-chrome';
import type { LegendSpec } from './types';

/** 与后端 MAX_SVG_LABEL_CHARS=60 同口径：>60 code points 截为 59 + "…"。 */
const MAX_SVG_LABEL_CHARS = 60;

export interface VectorSvgExportInput {
  /** live 合成后的 MapSpec（sources 携带 inlineData/data 才有矢量要素）。 */
  spec: unknown;
  /** live 视口逻辑像素（screen 尺寸基准）。 */
  viewport: { width: number; height: number };
  paperSize?: 'screen' | 'A4' | 'A3';
  orientation?: 'landscape' | 'portrait';
  /**
   * 目标 DPI。矢量坐标不随 DPI 缩放（编译器以 targetDpi=72 产出 1:1 逻辑
   * 坐标，画幅由外层 viewBox 承担）—— 保留入参以对齐导出请求语义。
   */
  dpi?: number;
  title?: string;
  subtitle?: string;
  /** ADR-0081 chrome 模型（图例/标题语义与画布导出同源）。 */
  chromeModel?: ExportChromeModel | null;
  /** 比例尺换算输入（米/逻辑像素）；缺席 → 比例尺不画（不虚构比例）。 */
  metersPerPixel?: number;
  /**
   * 编译/合成异常时的位图回退生产者（exporter 注入 buildSvgWrapper 闭包）。
   * 缺席 → 异常原样上抛（调用方自行处置）。
   */
  fallbackRaster?: (error: unknown) => string | null;
}

export interface VectorSvgExportResult {
  svg: string;
  degradations: ExportDegradation[];
}

/** 纸张 → 逻辑画幅（A4/A3 取视口长边按 1.414 比例推导；screen 用视口原样）。 */
function resolvePaperDimensions(
  paperSize: VectorSvgExportInput['paperSize'],
  orientation: VectorSvgExportInput['orientation'],
  viewport: { width: number; height: number },
): { width: number; height: number } {
  if (paperSize === 'A4' || paperSize === 'A3') {
    const long = Math.max(viewport.width || 0, viewport.height || 0) || 1200;
    return orientation === 'portrait'
      ? { width: Math.round(long / 1.414), height: long }
      : { width: long, height: Math.round(long / 1.414) };
  }
  return {
    width: viewport.width || 1200,
    height: viewport.height || 800,
  };
}

function decodeSvgEntities(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

/**
 * 编译产物长文本确定性截断：只处理 `<text ...>content</text>` 的内容段
 *（编译器已转义 → 内容无裸 `<`），code-point 口径与 Python 孪生一致。
 */
function truncateCompiledLabels(svg: string, degradations: ExportDegradation[]): string {
  return svg.replace(/<text\b[^>]*>([^<]*)<\/text>/g, (match, rawContent: string) => {
    const decoded = decodeSvgEntities(rawContent);
    const codePoints = Array.from(decoded);
    if (codePoints.length <= MAX_SVG_LABEL_CHARS) return match;
    const clipped = codePoints.slice(0, MAX_SVG_LABEL_CHARS - 1).join('') + '…';
    degradations.push({
      code: 'label_truncated',
      detail: `${codePoints.length} 字符截断：${clipped.slice(0, 24)}…`,
    });
    // 只替换内容段（match 尾部 = rawContent + '</text>'；避免误伤属性段）。
    return (
      match.slice(0, match.length - rawContent.length - '</text>'.length) +
      escapeSvgText(clipped) +
      '</text>'
    );
  });
}

const _fmtNum = (n: number): string =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : n.toFixed(1);

/**
 * LegendSpec → marginalia 图例条目（与 live legends.tsx legendEntries /
 * export drawChromeLegend 同一派生语义；nodata 规则在场时追加无数据条目
 * —— 与 withNoDataGuard 的 nodata.color 同源）。无条目 → null（不画空卡）。
 */
function legendItemsOf(spec: LegendSpec | undefined): {
  title: string;
  items: Array<{ label: string; color: string; type: 'rect' }>;
} | null {
  if (!spec || spec.type === 'bivariate') return null;
  const items: Array<{ label: string; color: string; type: 'rect' }> = [];
  if (spec.type === 'categorical') {
    for (const c of spec.categories ?? []) {
      items.push({ label: c.label || String(c.key ?? ''), color: c.color || '#888', type: 'rect' });
    }
  } else if (spec.type === 'graduated') {
    const colors = spec.palette_colors ?? [];
    const n = Math.min(Math.max(spec.breaks.length - 1, 0), colors.length);
    for (let i = 0; i < n; i++) {
      const label = spec.labels?.[i];
      items.push({
        label:
          label != null && String(label).trim() !== ''
            ? String(label)
            : `${_fmtNum(spec.breaks[i])} – ${_fmtNum(spec.breaks[i + 1])}`,
        color: colors[i],
        type: 'rect',
      });
    }
  } else {
    // continuous / divergent：min/mid/max 三读数（与 drawChromeColorbar 同口径）
    if (typeof spec.min !== 'number' || typeof spec.max !== 'number') return null;
    const colors = spec.palette_colors ?? [];
    const mid = (spec.min + spec.max) / 2;
    const pick = (t: number) => colors[Math.min(colors.length - 1, Math.round(t * (colors.length - 1)))] || '#888';
    items.push({ label: _fmtNum(spec.min), color: pick(0), type: 'rect' });
    items.push({ label: _fmtNum(mid), color: pick(0.5), type: 'rect' });
    items.push({ label: _fmtNum(spec.max), color: pick(1), type: 'rect' });
  }
  const nodata = (spec as { nodata?: { color?: string; label?: string } }).nodata;
  if (nodata?.color) {
    items.push({ label: nodata.label || '无数据', color: nodata.color, type: 'rect' });
  }
  if (items.length === 0) return null;
  // W7 同语义：legend.title 优先（live 图例标题源），缺失回退既有字段格式。
  const title =
    (spec as { title?: string }).title ||
    (spec.field ? `字段: ${spec.field}` : '图例');
  return { title, items };
}

/**
 * 组装真矢量 SVG 导出件。编译/组装任何异常 → fallbackRaster（若提供），
 * 并附 `vector_svg_fallback_raster` 诊断；成功附 `basemap_omitted_vector_svg`。
 */
export function buildVectorSvgExport(input: VectorSvgExportInput): VectorSvgExportResult {
  try {
    const { width, height } = resolvePaperDimensions(input.paperSize, input.orientation, input.viewport);
    const margin = 28;

    // 数据层：targetDpi=72 → dpiScale=1，编译坐标系与外层画幅 1:1。
    const compiled = compileMapSpecToSvg(input.spec, {
      targetDpi: 72,
      width,
      height,
      padding: 0,
    });

    const degradations: ExportDegradation[] = [
      { code: 'basemap_omitted_vector_svg', detail: '矢量 SVG 不含栅格底图' },
    ];
    const dataSvg = truncateCompiledLabels(compiled, degradations);

    const ink = '#1e293b';
    const title = input.title || '';
    const subtitle = input.subtitle || '';

    // 整饰层（与 print layout 同 Z 序：数据在最底，图框/整饰/title 在上）。
    const parts: string[] = [];
    parts.push(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    );
    if (title) parts.push(`<title>${escapeSvgText(title)}</title>`);
    // 底色（独立文件不透明白底 —— 与编译器自带白底一致语义）
    parts.push(`<rect x="0" y="0" width="${width}" height="${height}" fill="#ffffff" />`);
    // 数据层（嵌套 <svg>，width/height 与编译选项一致 → 1:1 呈现）
    parts.push(dataSvg);
    // 图框
    parts.push(renderSvgFrameBorder({ width, height, margin }));
    // 标题 / 副标题（转义文本 —— 与画布 chrome 顶部横排同位）
    let cursorY = margin + 30;
    if (title) {
      parts.push(
        `<text x="${margin + 12}" y="${cursorY}" font-family="sans-serif" font-size="22" font-weight="bold" fill="${ink}">${escapeSvgText(title)}</text>`,
      );
      cursorY += 22;
    }
    if (subtitle) {
      parts.push(
        `<text x="${margin + 12}" y="${cursorY}" font-family="sans-serif" font-size="12" fill="${ink}" opacity="0.75">${escapeSvgText(subtitle)}</text>`,
      );
    }
    // 指北针（右上；renderSvgNorthArrow 自带 <svg> 根，嵌套合法）
    parts.push(
      `<g transform="translate(${width - margin - 60}, ${margin + 8})">${renderSvgNorthArrow({ width: 44, height: 44, color: '#1e3a8a' })}</g>`,
    );
    // 比例尺（右下；米/像素缺席 → 不画，不虚构比例）
    if (input.metersPerPixel && input.metersPerPixel > 0) {
      const len = 120;
      const nice = computeNiceScale(input.metersPerPixel, len);
      parts.push(
        `<g transform="translate(${width - margin - 16 - (len + 16)}, ${height - margin - 16 - 28})">${renderSvgScalebar({ lengthPx: nice.px, labelText: formatScaleLabel(nice.meters) })}</g>`,
      );
    }
    // 图例（左下；图例条目派生与 live/画布同源，空条目不画空卡）
    const legend = legendItemsOf(input.chromeModel?.legends?.[0]?.legendSpec ?? input.chromeModel?.legend?.legendSpec);
    if (legend) {
      const legendH = 24 + 18 + legend.items.length * 20;
      parts.push(
        `<g transform="translate(${margin + 12}, ${height - margin - 16 - legendH})">${renderSvgLegend({
          title: legend.title,
          items: legend.items,
          color: ink,
        })}</g>`,
      );
    }
    parts.push('</svg>');

    return { svg: parts.join('\n'), degradations };
  } catch (error) {
    const raster = input.fallbackRaster?.(error);
    if (!raster) throw error;
    return {
      svg: raster,
      degradations: [
        {
          code: 'vector_svg_fallback_raster',
          detail: error instanceof Error ? error.message : String(error),
        },
      ],
    };
  }
}
