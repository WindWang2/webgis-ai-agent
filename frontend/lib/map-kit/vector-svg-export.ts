/**
 * V5（ADR-0118 W5）真矢量 SVG 导出；ac-08（ADR-0157 P2）升为版面描述
 * 中间层（layout-description.ts）的一等消费方。
 *
 * P2 漂移消除（对照 recon §3 差异表）：
 * - 标题/副标题：旧实现读 `input.title || ''`（请求链），与画布 chrome 的
 *   「请求 > spec 组件」回退链漂移 → 现消费 `layout.texts`（三链同一记录）；
 * - 图例：旧实现只画 `legends[0]` → 现遍历 `chromeModel.legends` 全部实例
 *   （多图层图例逐实例堆叠，语义与画布一致）；
 * - 比例尺：旧实现自算 nice（目标条长硬编码 120）→ 现消费 `layout.scaleBar`
 *  （scale-math 单源数字，与画布 drawChromeScaleBar 同记录）；
 * - 署名：旧实现缺席 → 现消费 `layout.texts`（attribution 条目）；
 * - 出版档（P5）：消费 `layout.page`（colorMode/bleedMm/cropMarks）——
 *   cmyk 档渲染出血边与裁切标记。
 *
 * 不变的分工：
 * - 数据层：孪生编译器 compileMapSpecToSvg（TS/Python 字节 parity 锁定）；
 * - 诊断：成功发 `basemap_omitted_vector_svg`；异常 → fallbackRaster +
 *   `vector_svg_fallback_raster`；长文本 >60 code points 截断（与后端
 *   MAX_SVG_LABEL_CHARS=60 同口径）。
 */
import { compileMapSpecToSvg } from '@/lib/mapspec-compiler/mapspec-to-svg';
import {
  escapeSvgText,
  renderSvgCropMarks,
  renderSvgFrameBorder,
  renderSvgLegend,
  renderSvgNorthArrow,
  renderSvgScalebar,
} from './svg-marginalia';
import { deriveLegendModel } from './legend-model';
import type { ExportDegradation } from './export-chrome';
import type { LegendSpec } from './types';
import type { PublicationLayout } from '../export/layout-description';

/** 与后端 MAX_SVG_LABEL_CHARS=60 同口径：>60 code points 截为 59 + "…"。 */
const MAX_SVG_LABEL_CHARS = 60;

export interface VectorSvgExportInput {
  /** live 合成后的 MapSpec（sources 携带 inlineData/data 才有矢量要素）。 */
  spec: unknown;
  /** 出版版面描述（P2 单源 —— 版面决策的唯一输入）。 */
  layout: PublicationLayout;
  /**
   * 目标 DPI。矢量坐标不随 DPI 缩放（编译器以 targetDpi=72 产出 1:1 逻辑
   * 坐标，画幅由外层 viewBox 承担）—— 保留入参以对齐导出请求语义。
   */
  dpi?: number;
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

/**
 * W5：条目推导收敛至 legend-model 单源。bivariate 无通用条目卡（专用色阵
 * 渲染器语义）→ null。P2：对 chromeModel.legends 全实例逐个派生。
 */
function legendItemsOf(spec: LegendSpec | undefined): {
  title: string;
  items: Array<{ label: string; color: string; type: 'rect' }>;
} | null {
  const model = deriveLegendModel(spec);
  if (!model || model.kind === 'bivariate') return null;
  return {
    title: model.title,
    items: model.entries.map((e) => ({ label: e.label, color: e.color, type: 'rect' as const })),
  };
}

/**
 * 组装真矢量 SVG 导出件 —— 版面决策全部来自 layout（IR），本函数只负责
 * SVG 语法与 Z 序（数据最底、图框/整饰在上）。异常 → fallbackRaster。
 */
export function buildVectorSvgExport(input: VectorSvgExportInput): VectorSvgExportResult {
  try {
    const layout = input.layout;
    const { widthPx: width, heightPx: height, bleedMm, cropMarks, colorMode } = layout.page;
    const margin = 28;
    // 出版档出血：内容画幅不变，出血区以页面边缘带呈现（打印裁切安全边）。
    const bleedPx = Math.round(bleedMm * (72 / 25.4)); // 1mm @72dpi
    const outerW = width + bleedPx * 2;
    const outerH = height + bleedPx * 2;

    // 数据层：targetDpi=72 → dpiScale=1，编译坐标系与图框 1:1。
    // §0.5/§5 契约：矢量不支持的层类型（extrusion 压平 / heatmap 近似 /
    // 栅格外链）→ 编译器逐层诚实标记 + `layer_approximated_*` 诊断，根节点
    // 补 data-export-content="mixed"；label 截断/预算诊断不属于层近似，
    // 不参与 mixed 判定（review-r1 修正：长标签不再误标 mixed）。
    let layerApproximations = 0;
    const compiled = compileMapSpecToSvg(input.spec, {
      targetDpi: 72,
      width,
      height,
      padding: 0,
      onDiagnostic: (code) => {
        if (code === 'layer_approximated_heatmap' || code === 'layer_approximated_extrusion') {
          layerApproximations += 1;
        }
      },
    });

    const degradations: ExportDegradation[] = [
      { code: 'basemap_omitted_vector_svg', detail: '矢量 SVG 不含栅格底图' },
    ];
    const dataSvg = truncateCompiledLabels(compiled, degradations);
    // 内容态：层近似诊断（heatmap/extrusion）或产物内近似标记在场 → mixed；
    // 纯矢量数据层 → vector（label 截断不算层近似）。
    const hasApproximatedLayer = /data-export-degraded="true"/.test(dataSvg);
    const contentMode =
      layerApproximations > 0 || hasApproximatedLayer ? 'mixed' : 'vector';

    const ink = colorMode === 'cmyk' ? '#000000' : '#1e293b';
    const title = layout.texts.find((t) => t.kind === 'title')?.text ?? '';
    const subtitle = layout.texts.find((t) => t.kind === 'subtitle')?.text ?? '';
    const attribution = layout.texts.find((t) => t.kind === 'attribution')?.text ?? '';

    // 整饰层（与 print layout 同 Z 序：数据在最底，图框/整饰/title 在上）。
    const parts: string[] = [];
    parts.push(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${outerW}" height="${outerH}" viewBox="0 0 ${outerW} ${outerH}" data-export-content="${contentMode}">`,
    );
    if (title) parts.push(`<title>${escapeSvgText(title)}</title>`);
    // 出血底（页面最外层）+ 内容底色
    parts.push(`<rect x="0" y="0" width="${outerW}" height="${outerH}" fill="#ffffff" />`);
    parts.push(`<g transform="translate(${bleedPx}, ${bleedPx})">`);
    // 数据层（嵌套 <svg>，width/height 与编译选项一致 → 1:1 呈现）
    parts.push(dataSvg);
    // 图框
    parts.push(renderSvgFrameBorder({ width, height, margin }));
    // 标题 / 副标题（layout.texts 单源 —— 与画布 chrome 同回退链）
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
    // 比例尺（右下；layout.scaleBar 单源数字 —— 米/像素缺席 → 不画，不虚构比例）
    if (layout.scaleBar) {
      const nice = layout.scaleBar.nice;
      parts.push(
        `<g transform="translate(${width - margin - 16 - (nice.px + 16)}, ${height - margin - 16 - 28})">${renderSvgScalebar({ lengthPx: nice.px, labelText: layout.scaleBar.label })}</g>`,
      );
    }
    // 图例（左下；chromeModel.legends 全实例逐个堆叠 —— 多图层语义与画布一致）
    const legendModels = (layout.chromeModel?.legends ?? [])
      .map((el) => legendItemsOf(el.legendSpec))
      .filter((l): l is NonNullable<typeof l> => l !== null && l.items.length > 0);
    let legendBottom = height - margin - 16;
    for (const legend of legendModels) {
      const legendH = 24 + 18 + legend.items.length * 20;
      const top = legendBottom - legendH;
      parts.push(
        `<g transform="translate(${margin + 12}, ${top})">${renderSvgLegend({
          title: legend.title,
          items: legend.items,
          color: ink,
        })}</g>`,
      );
      legendBottom = top - 8;
    }
    // 署名（左下、图例带之下 —— layout.texts 单源；旧实现缺席）
    if (attribution) {
      parts.push(
        `<text x="${margin + 12}" y="${height - margin - 4}" font-family="sans-serif" font-size="9" fill="${ink}" opacity="0.6">${escapeSvgText(attribution)}</text>`,
      );
    }
    // 裁切标记（出版档；出血外缘四角十字线）
    if (cropMarks && bleedPx > 0) {
      parts.push(
        `<g transform="translate(${bleedPx}, ${bleedPx})">${renderSvgCropMarks({ width, height, bleedPx })}</g>`,
      );
    }
    parts.push('</g>');
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
