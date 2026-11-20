import { reflowLines } from '../export/reflow-lines';
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
import { vectorSceneDegradeReceipt } from './scene-export-disclosure';
import type { LegendSpec } from './types';
import type { PublicationLayout } from '../export/layout-description';
import { verifyFlowMapSpec, type FlowExportVerification } from '@/lib/flow/flow-export';

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
  flow_verification?: FlowExportVerification;
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
 * G41 canonical 图例 → 真矢量 SVG（前端 SVG 导出链）。
 *
 * 旧实现把 bivariate 一律降级为 ``legend_entries_truncated`` 诊断（矩阵完全
 * 不画）。本函数消费与 live / canvas / 服务端 PDF 相同的 canonical 合同：
 * 双变量画 n×n 格子（屏幕序、逐格精确计数、区间 tooltip、方向箭头、单位、
 * 有效对与缺失），可靠性画估计色阶 + 具名误差量/level/方向/缺失梯度。
 * 旧 bivariate（无 cells）保持原降级诊断，绝不假装已支持。
 */
function canonicalLegendSvg(spec: LegendSpec | undefined, ink: string): string | null {
  if (!spec) return null;
  const s = spec as unknown as {
    type?: string; title?: string; n?: number;
    cells?: Array<{
      color: string; count: number; class_index: number; empty?: boolean;
      row_interval?: { label: string } | null;
      col_interval?: { label: string } | null;
    }>;
    axes?: {
      row?: { label?: string; unit?: string | null; arrow?: string };
      col?: { label?: string; unit?: string | null; arrow?: string };
    };
    counts?: { valid_pairs?: number };
    missing?: { label?: string; count?: number };
    boundary_rule?: string;
    label_a?: string; label_b?: string;
    estimate?: { label?: string; unit?: string | null; palette_colors?: string[] };
    measure?: { kind?: string; level?: number; unit_note?: string; visual_meaning?: string; source?: string };
    channel?: { kind?: string; clip?: string };
    buckets?: Array<{ key: string; label: string; output: number; count: number }>;
  };
  // 视觉宽度受限时显式标注省略；完整文本保存在 data-g41-*-full 属性与
  // <title>，导出件不丢关键轴名/单位/度量名（B32，复审 F5）。
  const short = (value: unknown, limit: number) => {
    const str = String(value ?? '');
    return str.length <= limit ? str : `${str.slice(0, Math.max(1, limit - 1))}…`;
  };
  const fullRow = String(s.axes?.row?.label ?? s.label_b ?? '');
  const fullCol = String(s.axes?.col?.label ?? s.label_a ?? '');
  const fullMeasureNote = String(s.measure?.unit_note ?? '');
  const fullVisualMeaning = String(s.measure?.visual_meaning ?? '');
  const title = escapeSvgText(short(s.title ?? '', 48));
  if (s.type === 'bivariate' && Array.isArray(s.cells)) {
    const n = Number(s.n ?? 3);
    if (!(n === 2 || n === 3) || s.cells.length !== n * n) return null;
    const cell = 15;
    const grid = n * cell;
    const parts: string[] = [
      `<g class="g41-canonical-bivariate-legend" data-g41-type="bivariate" data-g41-n="${n}" ` +
      `data-g41-row-label-full="${escapeSvgText(fullRow)}" data-g41-col-label-full="${escapeSvgText(fullCol)}">`,
      `<text x="0" y="11" font-size="11" font-weight="bold" fill="${escapeSvgText(ink)}"><title>${escapeSvgText(s.title ?? '')}</title>${title}</text>`,
    ];
    const arrow = s.axes?.row?.arrow === 'down' ? '↓' : '↑';
    parts.push(`<text x="0" y="${28 + cell / 2}" font-size="9" fill="${escapeSvgText(ink)}"><title>${escapeSvgText(fullRow)}</title>${arrow}${escapeSvgText(short(fullRow, 12))}</text>`);
    // cells 已按屏幕行主序排列（后端 legend.py 产出）；数组下标即屏幕位置。
    s.cells.forEach((c, i) => {
      const sr = Math.floor(i / n);
      const sc = i % n;
      const x = 34 + sc * cell;
      const y = 20 + sr * cell;
      const tip = `#${c.class_index} · ${c.col_interval?.label ?? ''} / ${c.row_interval?.label ?? ''} · n=${c.count}${c.empty ? ' · empty' : ''}`;
      parts.push(
        `<rect x="${x}" y="${y}" width="${cell - 1}" height="${cell - 1}" fill="${escapeSvgText(c.color)}" ` +
        `data-g41-cell="${c.class_index}" data-g41-count="${c.count}" data-g41-empty="${c.empty ? 'true' : 'false'}">` +
        `<title>${escapeSvgText(tip)}</title></rect>`,
      );
      parts.push(`<text x="${x + 2}" y="${y + cell - 4}" font-size="7" fill="#1e293b">${c.count}</text>`);
    });
    parts.push(`<text x="34" y="${20 + grid + 12}" font-size="9" fill="${escapeSvgText(ink)}"><title>${escapeSvgText(fullCol)}</title>→${escapeSvgText(short(fullCol, 20))}</text>`);
    parts.push(`<text x="0" y="${20 + grid + 26}" font-size="8" fill="${escapeSvgText(ink)}">pairs=${s.counts?.valid_pairs ?? 0} · ${escapeSvgText(s.missing?.label ?? 'no data')}:${s.missing?.count ?? 0} · ${escapeSvgText(s.axes?.col?.unit ?? '—')}/${escapeSvgText(s.axes?.row?.unit ?? '—')} · ${escapeSvgText(s.boundary_rule ?? '')}</text>`);
    parts.push('</g>');
    return parts.join('');
  }
  if (s.type === 'reliability_dual_channel') {
    const parts: string[] = [
      `<g class="g41-canonical-reliability-legend" data-g41-type="reliability_dual_channel" ` +
      `data-g41-measure-note-full="${escapeSvgText(fullMeasureNote)}" data-g41-visual-meaning-full="${escapeSvgText(fullVisualMeaning)}">`,
      `<text x="0" y="11" font-size="11" font-weight="bold" fill="${escapeSvgText(ink)}"><title>${escapeSvgText(s.title ?? '')}</title>${title}</text>`,
    ];
    (s.estimate?.palette_colors ?? []).slice(0, 7).forEach((c, i) => {
      parts.push(`<rect x="${i * 15}" y="18" width="14" height="12" fill="${escapeSvgText(c)}" data-g41-estimate-class="${i}" />`);
    });
    const lines = [
      `${s.estimate?.label ?? ''} · unit ${s.estimate?.unit ?? '—'}`,
      `measure ${s.measure?.kind ?? '—'} · level ${s.measure?.level ?? '—'}`,
      short(fullMeasureNote, 64),
      short(fullVisualMeaning, 64),
      `${s.missing?.label ?? ''}: ${s.missing?.count ?? 0} · clip=${s.channel?.clip ?? ''} · ${s.measure?.source ?? ''}`,
    ];
    lines.forEach((line, i) => {
      parts.push(`<text x="0" y="${44 + i * 13}" font-size="8" fill="${escapeSvgText(ink)}">${escapeSvgText(line)}</text>`);
    });
    (s.buckets ?? []).slice(0, 10).forEach((b, i) => {
      parts.push(`<rect x="${i * 15}" y="${44 + lines.length * 13 + 4}" width="14" height="11" fill="#64748b" fill-opacity="${Math.min(1, Math.max(0.05, b.output))}" data-g41-bucket="${escapeSvgText(b.key)}" data-g41-bucket-count="${b.count}" />`);
    });
    parts.push('</g>');
    return parts.join('');
  }
  return null;
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
    const refUnavailableLayers: string[] = [];
    const compilerDegradations: ExportDegradation[] = [];
    const compiled = compileMapSpecToSvg(input.spec, {
      targetDpi: 72,
      width,
      height,
      padding: 0,
      onDiagnostic: (code, detail) => {
        if (compilerDegradations.length < 64 && (code === 'layer_filter_unsupported' || code === 'label_expression_unsupported' || code === 'layer_coordinates_invalid')) {
          compilerDegradations.push({ code, detail: detail.slice(0, 200) });
        }
        if (code === 'layer_approximated_heatmap' || code === 'layer_approximated_extrusion') {
          layerApproximations += 1;
        }
        // H02（任务书 §D）：ref 未水合的层省略 → 结构化披露（不静默）。
        if (code === 'layer_ref_unavailable' && refUnavailableLayers.length < 8) {
          refUnavailableLayers.push(detail || 'unknown');
        }
      },
    });

    const flowVerification = verifyFlowMapSpec(input.spec, { renderedSvg: compiled });
    const degradations: ExportDegradation[] = [
      ...compilerDegradations,
      ...flowVerification.issues.map((issue) => ({ code: issue.code, detail: `${issue.layer_id}: ${issue.detail}` })),
      { code: 'basemap_omitted_vector_svg', detail: '矢量 SVG 不含栅格底图' },
    ];
    // G3D05（ADR-0275）：三维场景的矢量降级收据 —— scene.mode∈{3d,2.5d}
    // 必发（判定单源 scene-export-disclosure.vectorSceneDegradeReceipt，
    // 与后端孪生编译期同语义；2d 零噪音）。
    const sceneDegradeReceipt = vectorSceneDegradeReceipt(
      (input.spec as { scene?: { mode?: string } })?.scene?.mode ?? null,
    );
    if (sceneDegradeReceipt) {
      degradations.push({
        code: sceneDegradeReceipt.code,
        detail: sceneDegradeReceipt.detail,
      });
    }
    if (refUnavailableLayers.length > 0) {
      degradations.push({
        code: 'layer_ref_unavailable',
        detail: refUnavailableLayers.join(','),
      });
    }
    // H02（任务书 §C）：版面文本确定性截断披露（IR 单源标记）。
    const truncatedTexts = input.layout.texts.filter((t) => t.truncated);
    if (truncatedTexts.length > 0) {
      degradations.push({
        code: 'title_truncated',
        detail: truncatedTexts.map((t) => t.kind).join(','),
      });
    }
    // ADR-0283 D2：关键文本折行披露（与画布链同码同源）。
    const wrappedTexts = input.layout.texts.filter((t) => t.wrapped);
    if (wrappedTexts.length > 0) {
      degradations.push({
        code: 'key_text_wrapped',
        detail: wrappedTexts.map((t) => t.kind).join(','),
      });
    }
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
    // 标题 / 副标题（layout.texts 单源 —— 与画布 chrome 同回退链；
    // ADR-0283 D2：wrapped 条目按 lines 逐行绘制 —— 长标题不横向溢出）
    let cursorY = margin + 30;
    const titleEntry = layout.texts.find((t) => t.kind === 'title');
    const subtitleEntry = layout.texts.find((t) => t.kind === 'subtitle');
    if (title && (layout.chromeModel?.title || !layout.chromeModel?.fromSpec)) {
      const titleLines = reflowLines(
        titleEntry?.lines?.length ? titleEntry.lines : [title],
        width - (margin + 12) * 2,
        (text) => Array.from(text).length * 22,
      );
      const titleStep = 29; // 22px 字号 × 1.3 行距（确定性）
      titleLines.forEach((line, i) => {
        parts.push(
          `<text x="${margin + 12}" y="${cursorY + titleStep * i}" font-family="sans-serif" font-size="22" font-weight="bold" fill="${ink}">${escapeSvgText(line)}</text>`,
        );
      });
      cursorY += 22 + titleStep * (titleLines.length - 1);
    }
    if (subtitle && (layout.chromeModel?.subtitle || !layout.chromeModel?.fromSpec)) {
      const subtitleLines = reflowLines(
        subtitleEntry?.lines?.length ? subtitleEntry.lines : [subtitle],
        width - (margin + 12) * 2,
        (text) => Array.from(text).length * 12,
      );
      const subtitleStep = 16; // 12px 字号 × 1.3 行距
      subtitleLines.forEach((line, i) => {
        parts.push(
          `<text x="${margin + 12}" y="${cursorY + subtitleStep * i}" font-family="sans-serif" font-size="12" fill="${ink}" opacity="0.75">${escapeSvgText(line)}</text>`,
        );
      });
    }
    // 指北针（右上；renderSvgNorthArrow 自带 <svg> 根，嵌套合法）
    if (layout.chromeModel?.northArrow || !layout.chromeModel?.fromSpec) parts.push(
      `<g transform="translate(${width - margin - 60}, ${margin + 8})">${renderSvgNorthArrow({ width: 44, height: 44, color: '#1e3a8a' })}</g>`,
    );
    // 比例尺（右下；layout.scaleBar 单源数字 —— 米/像素缺席 → 不画，不虚构比例）
    if (layout.scaleBar && (layout.chromeModel?.scaleBar || !layout.chromeModel?.fromSpec)) {
      const nice = layout.scaleBar.nice;
      parts.push(
        `<g transform="translate(${width - margin - 16 - (nice.px + 16)}, ${height - margin - 16 - 28})">${renderSvgScalebar({ lengthPx: nice.px, labelText: layout.scaleBar.label })}</g>`,
      );
    }
    // 图例（左下；chromeModel.legends 全实例逐个堆叠 —— 多图层语义与画布一致）
    const legendElements = layout.chromeModel?.legends ?? [];
    let legendBottom = height - margin - 16;
    // G41：canonical 双变量/可靠性图例由真矢量绘制器消费（不再省略矩阵）。
    // 只有「旧 bivariate 且无 canonical cells」才保留诚实的省略诊断。
    legendElements.forEach((element) => {
      const spec = element.legendSpec;
      const svg = canonicalLegendSvg(spec, ink);
      if (!svg) {
        if (spec?.type === 'bivariate') {
          degradations.push({
            code: 'legend_entries_truncated',
            detail: 'legacy bivariate matrix omitted in vector SVG (no canonical cells)',
          });
        }
        return;
      }
      const isMatrix = spec?.type === 'bivariate';
      const cellsN = Number((spec as { n?: number } | undefined)?.n ?? 3) || 3;
      const h = isMatrix ? 20 + cellsN * 15 + 30 : 130;
      legendBottom -= h + 8;
      parts.push(
        `<g transform="translate(${margin + 12}, ${Math.max(margin, legendBottom)})">${svg}</g>`,
      );
    });
    const legendModels = legendElements
      // canonical 图例已由上面的真矢量绘制器消费，不再走扁平条目卡。
      .filter((el) => !['bivariate', 'reliability_dual_channel']
        .includes(String(el.legendSpec?.type ?? '')))
      .map((el) => legendItemsOf(el.legendSpec))
      .filter((l): l is NonNullable<typeof l> => l !== null && l.items.length > 0);
    for (const legend of legendModels) {
      const available = Math.max(0, Math.floor((legendBottom - cursorY - 62) / 20));
      if (legend.items.length > available) {
        degradations.push({ code: 'legend_entries_truncated', detail: `${legend.items.length - available} entries omitted` });
        legend.items = legend.items.slice(0, available);
      }
      if (legend.items.length === 0) continue;
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

    return { svg: parts.join('\n'), degradations, flow_verification: flowVerification };
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
