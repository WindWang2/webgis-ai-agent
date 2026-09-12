/**
 * Export chrome — shared placement semantics for the canvas exporter (ADR-0081).
 *
 * live chrome 的 placement 语义（anchor 七槽 / floating 像素坐标）此前在
 * exporter 里完全没有对应物（全部硬编码固定槽）。本模块把
 * `resolveMapComponents`（live/export 共用解析层）的输出映射到导出画布：
 *
 * - anchor → 画布槽位矩形（margin + 槽内堆叠）；
 * - floating → 视口像素按画布/视口比例缩放（确定性换算）；
 * - 组件族绘制（title/subtitle/罗盘/比例尺/图例/色条/署名/统计卡/图表）
 *   从同一 ResolvedMapComponent 模型出发 —— 语义一致，不要求像素级相同。
 *
 * 无 spec 组件时（旧会话/无 committed spec）exporter 走 legacy 固定槽路径，
 * 行为不变 —— parity 路径只在 spec 存在时激活。
 */

import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { LegendSpec } from './types';
import type { LayoutStyle } from './layout-style';
import {
  DEFAULT_COMPONENT_ANCHOR,
  resolveMapComponents,
  scaleFloatingRect,
  type ChromeAnchor,
  type ResolvedMapComponent,
} from '@/lib/map-components/resolve-components';
import { resolveComponentLayout } from '@/lib/map-components/resolve-layout';
import { uncertaintyKindLabel as _uncertaintyKindLabel } from './disclosure-labels';
import {
  anchorFractionInBounds,
  boundsFromCenterZoom,
} from '@/lib/map-components/geo-anchor';
import { computeNiceScale, formatScaleLabel } from './scale-math';
import { deriveLegendModel } from './legend-model';

export interface StatsPanelData {
  title?: string;
  items: Array<{ label?: string; value?: string | number; unit?: string }>;
}

export interface ChartPanelDataPoint {
  name: string;
  value?: number;
  x?: number;
  y?: number;
  /** V4 box_plot 五数扩展（value=median；与 lib/types ChartDataPoint 同源）。 */
  q1?: number;
  q3?: number;
  min?: number;
  max?: number;
}

/** V4 多序列（grouped/stacked bar、radar、heat_matrix 行；与 ChartSeries 同源）。 */
export interface ChartPanelSeries {
  name: string;
  data: ChartPanelDataPoint[];
}

export interface ChartPanelData {
  /** chart_kinds 词表（18 种 native，与 lib/types ChartKind 同源）；未知 kind 由绘制端诚实降级。 */
  type: string;
  title: string;
  data: ChartPanelDataPoint[];
  series?: ChartPanelSeries[];
  /** stacked_bar 的堆叠语义开关（grouped_bar 缺省并排）。 */
  stacked?: boolean;
  x_label?: string;
  y_label?: string;
}

/**
 * V4：table_panel 导出载荷（有界快照 —— 绘制面 ≤8 行 / ≤6 列，快照外的
 * 行列总量走尾注披露；行数据与 live table-data 同形（props 记录））。
 */
export interface TablePanelData {
  title: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  /** 快照外的总行数（诚实披露「…N 行未显示」）。 */
  totalCount: number;
  /** 全量列数（列被快照裁剪时用于「…N 列未显示」披露）。 */
  totalColumns?: number;
}

/** 导出侧组件元素（从 ResolvedMapComponent 派生，含画布坐标）。 */
export interface ExportChromeElement {
  kind: string;
  anchor: ChromeAnchor;
  /** floating 缩放后的画布坐标（anchor 元素为 undefined）。 */
  rect?: { x: number; y: number; width?: number; height?: number };
  /** 槽内堆叠序（ADR-0084 共享求解器；0 贴边 —— 消费端加 index×层距偏移）。 */
  stackIndex?: number;
  /** 槽内组件总数（≤1 时消费方无需偏移）。 */
  slotSize?: number;
  text?: string;
  /** 组件 variant（map_border 等变体驱动型组件）。 */
  variant?: string;
  legendSpec?: LegendSpec;
  stats?: StatsPanelData;
  chart?: ChartPanelData;
  /** V4：表格面板有界快照（buildExportChrome 双通道装配；无数据 → 面板缺席）。 */
  table?: TablePanelData;
  /** v2 注记 callout：地理锚点 [lng, lat]（与 live annotation.tsx 同链投影）。 */
  anchorCoordinate?: [number, number];
  /** v2 注记 group：多条相关注记（≤12 条，与后端 MAX_ANNOTATION_ITEMS 同值）。 */
  items?: Array<{ text: string; anchor?: [number, number] }>;
  /** v2 插图：插图 bbox / 主图指示范围 / 边界折线（已夹取 ≤512 点）。 */
  insetBbox?: { west: number; south: number; east: number; north: number };
  insetMainBbox?: { west: number; south: number; east: number; north: number };
  insetBoundary?: [number, number][];
  /**
   * V3（ADR-0101 D6）：披露族（methodology/uncertainty/decision）导出载荷
   * —— 三种面板归一化为「标题 + 文本行」，与 live 渲染器同一防御式解析
   * 语义（坏载荷 → 面板缺席，不伪造）。
   */
  disclosure?: {
    title: string;
    rows: string[];
    accent: boolean;
    /** 需要删除线呈现的行下标（decision vetoed —— 与 live line-through 同义）。 */
    strikeRows?: number[];
  };
}

/** Wave 9：显式降级诊断（degradation matrix 的最小闭环）—— 替代静默
 * continue。V5（ADR-0118 D1）：code 词表升为后端权威词表的子集
 * （component-catalog.generated.json `renderDiagnostics` 段，唯一权威
 * app/lib/cartography/render_diagnostics.py）；detail 有界（≤200 字符）。
 * 后端专属码（label_truncated / features_truncated 等）由孪生编译侧发射，
 * 前端不发射但类型保持同词表以便 sidecar 透传。 */
export type ExportDegradationCode =
  | 'chart_ref_unavailable'
  | 'chart_kind_unsupported_export'
  | 'table_ref_unavailable'
  | 'component_skipped_invalid'
  | 'label_truncated'
  /* review-r1：label_suppressed_too_long 移除 —— 词表唯一无发射器死码，
   * 后端权威词表同步删除（待放置求解器接入导出链再回归）。 */
  | 'legend_entries_truncated'
  | 'features_truncated'
  | 'export_timeout_partial'
  | 'vector_svg_fallback_raster'
  | 'basemap_omitted_vector_svg'
  | 'pdf_text_rasterized_cjk'
  | 'comparison_second_view_not_exported'
  | 'comparison_export_composed'
  | 'cartogram_unsupported'
  | 'small_multiple_panel_skipped'
  | 'atlas_page_skipped'
  | 'atlas_page_limit_truncated'
  | 'terrain_3d_scale_caveat'
  /* ADR-0157 P1：高 DPI 渲染策略（发射器 lib/export/highdpi.ts）。 */
  | 'highdpi_rerender_timeout_degraded'
  | 'raster_tile_detail_limited_highdpi'
  /* ADR-0157 P4：所见即所得范围契约（发射器 lib/export/layout-description.ts）。 */
  | 'extent_overflow_data'
  | 'extent_fit_timeout_degraded'
  /* ADR-0157 P5：出版档栅格件 CMYK 近似披露。 */
  | 'cmyk_approximate_raster'
  /* ADR-0157 P3：PDF 出版字体嵌入（中文文本层可选取可检索）。 */
  | 'pdf_cjk_font_embedded';

export interface ExportDegradation {
  code: ExportDegradationCode;
  componentId?: string;
  detail?: string;
}

export interface ExportChromeModel {
  /** 有任何 spec chrome 元素时为 true（false → exporter 走 legacy 槽位）。 */
  fromSpec: boolean;
  /** Wave 9：本次导出的显式降级清单（空 = 无降级）。exporter 展示给用户。 */
  degradations: ExportDegradation[];
  title?: ExportChromeElement;
  subtitle?: ExportChromeElement;
  northArrow?: ExportChromeElement;
  scaleBar?: ExportChromeElement;
  /**
   * v2：图例族多实例 —— 每个绑定层的图例/色条独立成元素（旧字段
   * legend/colorbar 语义 = 第一个实例，保留供旧消费者/测试兼容）。
   */
  legend?: ExportChromeElement;
  colorbar?: ExportChromeElement;
  legends: ExportChromeElement[];
  colorbars: ExportChromeElement[];
  attribution?: ExportChromeElement;
  border?: ExportChromeElement;
  /** P6：spec graticule 组件 enabled → 导出绘制经纬网（live 无渲染器）。 */
  graticuleEnabled?: boolean;
  /** v2：区位插图元素（纯 SVG 投影语义，与 live inset-map 渲染器同链）。 */
  insets: ExportChromeElement[];
  panels: ExportChromeElement[];
}

export interface BuildExportChromeOptions {
  /** committed MapSpec（layout.components 的事实源）。 */
  spec: { layout?: { components?: MapSpecComponent[] } } | null | undefined;
  /** live 视口尺寸（floating 坐标缩放基准；<=0 时视为 1:1）。 */
  viewport: { width: number; height: number };
  /** 请求参数覆盖（title/subtitle 显式请求优先于 spec）。 */
  requestTitle?: string;
  requestSubtitle?: string;
  /** 图例/色条数据：layerId → legend_spec（来自 spec.layers）。 */
  legendSpecsByLayer: Record<string, LegendSpec>;
  /** HUD 发现的兜底图例（spec 无图例组件时使用）。 */
  fallbackLegendSpec?: LegendSpec;
  /** chartRef → ChartData 的异步加载器（大载荷走 session artifact）。 */
  loadChart?: (ref: string) => Promise<ChartPanelData | null>;
  /**
   * V4：tableRef → 原始表格载荷（{table:{columns,rows}} / {columns,rows} /
   * 记录数组）的异步加载器 —— 与 loadChart 同一 artifact 装配模式；
   * 快照有界化（≤8 行/≤6 列）由本模块统一执行。
   */
  loadTable?: (ref: string) => Promise<unknown | null>;
  /** V4：layerId → 图层属性记录数组的异步解析器（live 表格 layer 通道同源）。 */
  loadLayerTable?: (layerId: string) => Promise<Array<Record<string, unknown>> | null>;
  /** v2：live 视口地理 bounds（inset 指示框缺省 mainBbox 时使用）。 */
  viewportBounds?: { west: number; south: number; east: number; north: number };
}

function _anchorOf(c: ResolvedMapComponent): ChromeAnchor {
  return c.anchor ?? DEFAULT_COMPONENT_ANCHOR[c.type] ?? 'none';
}

/** [w, s, e, n] → GeoBounds（无效/退化 → null）。 */
function parseBbox4(raw: unknown): { west: number; south: number; east: number; north: number } | null {
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const w = Number(raw[0]);
  const s = Number(raw[1]);
  const e = Number(raw[2]);
  const n = Number(raw[3]);
  if (![w, s, e, n].every(Number.isFinite)) return null;
  if (e <= w || n <= s) return null;
  return { west: w, south: s, east: e, north: n };
}

/** 边界折线（≤512 点；与 live inset-map 同一上限）。 */
function parseBoundary(raw: unknown): [number, number][] | null {
  if (!Array.isArray(raw)) return null;
  const pts: [number, number][] = [];
  for (const pt of raw.slice(0, 512)) {
    if (Array.isArray(pt) && pt.length === 2 && Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1]))) {
      pts.push([Number(pt[0]), Number(pt[1])]);
    }
  }
  return pts.length >= 3 ? pts : null;
}

/** 注记 callout 锚点（[lng, lat] 合法性校验）。 */
function parseAnchorCoordinate(raw: unknown): [number, number] | null {
  if (!Array.isArray(raw) || raw.length !== 2) return null;
  const lng = Number(raw[0]);
  const lat = Number(raw[1]);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  if (lng < -180 || lng > 180 || lat < -90 || lat > 90) return null;
  return [lng, lat];
}

/** 注记 group 条目（≤12 条；与后端 MAX_ANNOTATION_ITEMS 同值）。 */
function parseAnnotationItems(raw: unknown): Array<{ text: string; anchor?: [number, number] }> | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const items: Array<{ text: string; anchor?: [number, number] }> = [];
  for (const entry of raw.slice(0, 12)) {
    if (!entry || typeof entry !== 'object') continue;
    const rec = entry as Record<string, unknown>;
    if (typeof rec['text'] !== 'string' || !rec['text'].trim()) continue;
    const anchor = parseAnchorCoordinate(rec['anchor']);
    items.push(anchor ? { text: rec['text'], anchor } : { text: rec['text'] });
  }
  return items;
}

function _floatingRectOf(
  c: ResolvedMapComponent,
  viewport: BuildExportChromeOptions['viewport'],
  canvas: { width: number; height: number },
): ExportChromeElement['rect'] | undefined {
  if (!c.floating || !c.floatingRect) return undefined;
  const scaled = scaleFloatingRect(c.floatingRect, viewport, canvas);
  return scaled;
}

function _parseStats(raw: unknown): StatsPanelData | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const items = (raw as { items?: unknown }).items;
  if (!Array.isArray(items) || items.length === 0) return undefined;
  const parsed = items
    .filter((it): it is Record<string, unknown> => !!it && typeof it === 'object')
    .map((it) => ({
      label: typeof it['label'] === 'string' ? it['label'] : undefined,
      value:
        typeof it['value'] === 'string' || typeof it['value'] === 'number'
          ? it['value']
          : undefined,
      unit: typeof it['unit'] === 'string' ? it['unit'] : undefined,
    }))
    .filter((it) => it.label !== undefined || it.value !== undefined);
  if (parsed.length === 0) return undefined;
  const title = (raw as { title?: unknown }).title;
  return { title: typeof title === 'string' ? title : undefined, items: parsed };
}

/** V4 多序列解析（宽容：name 必为字符串；数值字段经 isFinite 过滤）。 */
function _parseSeries(raw: unknown): ChartPanelSeries[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: ChartPanelSeries[] = [];
  for (const entry of raw.slice(0, 12)) {
    if (!entry || typeof entry !== 'object') continue;
    const rec = entry as Record<string, unknown>;
    if (typeof rec['name'] !== 'string' || !Array.isArray(rec['data'])) continue;
    const data = (rec['data'] as unknown[])
      .filter(
        (p): p is Record<string, unknown> =>
          !!p && typeof p === 'object' && typeof (p as Record<string, unknown>)['name'] === 'string',
      )
      .map((p) => {
        const point: ChartPanelDataPoint = { name: String(p['name']) };
        for (const key of ['value', 'x', 'y', 'q1', 'q3', 'min', 'max'] as const) {
          const v = p[key];
          if (typeof v === 'number' && Number.isFinite(v)) point[key] = v;
        }
        return point;
      });
    out.push({ name: rec['name'], data });
  }
  return out.length > 0 ? out : undefined;
}

function _parseChart(raw: unknown): ChartPanelData | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const r = raw as Record<string, unknown>;
  // V4：type 放宽为 chart_kinds 词表字符串（18 种 native）；未知 kind 仍
  // 进入模型 —— 绘制端画「暂不支持导出」诚实降级（不静默丢面板）。
  const type = typeof r['type'] === 'string' && r['type'].trim() ? r['type'].trim() : undefined;
  const data = r['data'];
  const title = r['title'];
  if (!type || !Array.isArray(data) || data.length === 0 || typeof title !== 'string') {
    return undefined;
  }
  const series = _parseSeries(r['series']);
  return {
    type,
    title,
    data: data as ChartPanelData['data'],
    ...(series ? { series } : {}),
    ...(r['stacked'] === true ? { stacked: true } : {}),
    x_label: typeof r['x_label'] === 'string' ? r['x_label'] : undefined,
    y_label: typeof r['y_label'] === 'string' ? r['y_label'] : undefined,
  };
}

// ── V4：table_panel 导出装配（有界快照）────────────────────────────────
// 绘制面行/列上限；快照外的总量走尾注披露（「…N 行未显示」），不画全量、
// 更不画空表冒充。取数双通道与 live table-panel 同源（tableRef artifact /
// layerId 图层属性 / inline table）。

const EXPORT_TABLE_MAX_ROWS = 8;
const EXPORT_TABLE_MAX_COLUMNS = 6;

/** 行数组（列序对齐）→ 记录；非数组行透传对象。 */
function _rowToRecord(columns: unknown, row: unknown): Record<string, unknown> {
  if (!Array.isArray(row)) {
    return row && typeof row === 'object' ? (row as Record<string, unknown>) : {};
  }
  const out: Record<string, unknown> = {};
  const cols = Array.isArray(columns) ? columns.map(String) : [];
  for (let i = 0; i < row.length && i < cols.length; i++) out[cols[i]] = row[i];
  return out;
}

/** 列推导（live deriveColumns 同式：首 20 行键序、排除 geometry、≤32 列）。 */
function _deriveColumns(records: Array<Record<string, unknown>>): string[] {
  const seen: string[] = [];
  for (const rec of records.slice(0, 20)) {
    if (!rec || typeof rec !== 'object') continue;
    for (const key of Object.keys(rec)) {
      if (!seen.includes(key) && key !== 'geometry') seen.push(key);
      if (seen.length >= 32) return seen;
    }
  }
  return seen;
}

/**
 * 原始表格载荷 → 有界 TablePanelData（宽容规整 + 快照裁剪）。
 * 无有效行/列 → null（调用方面板缺席，不伪造）。
 */
export function buildExportTableData(
  title: string,
  payload: unknown,
  preferredColumns?: string[],
): TablePanelData | null {
  let records: Array<Record<string, unknown>>;
  let payloadColumns: string[] | undefined;
  if (Array.isArray(payload)) {
    records = payload as Array<Record<string, unknown>>;
  } else if (payload && typeof payload === 'object') {
    const obj = payload as { table?: unknown; columns?: unknown; rows?: unknown };
    if (Array.isArray(obj.table)) {
      records = obj.table as Array<Record<string, unknown>>;
    } else if (obj.table && typeof obj.table === 'object') {
      const t = obj.table as { columns?: unknown; rows?: unknown };
      if (!Array.isArray(t.rows)) return null;
      records = t.rows.map((r) => _rowToRecord(t.columns, r));
      payloadColumns = Array.isArray(t.columns) ? t.columns.map(String) : undefined;
    } else if (Array.isArray(obj.rows)) {
      records = obj.rows as Array<Record<string, unknown>>;
      payloadColumns = Array.isArray(obj.columns) ? obj.columns.map(String) : undefined;
    } else {
      return null;
    }
  } else {
    return null;
  }
  records = records.filter(
    (r): r is Record<string, unknown> => !!r && typeof r === 'object' && !Array.isArray(r),
  );
  if (records.length === 0) return null;
  const fullColumns =
    preferredColumns && preferredColumns.length
      ? preferredColumns
      : payloadColumns && payloadColumns.length
        ? payloadColumns
        : _deriveColumns(records);
  const columns = fullColumns.slice(0, EXPORT_TABLE_MAX_COLUMNS);
  if (columns.length === 0) return null;
  return {
    title,
    columns,
    rows: records.slice(0, EXPORT_TABLE_MAX_ROWS),
    totalCount: records.length,
    ...(fullColumns.length > columns.length ? { totalColumns: fullColumns.length } : {}),
  };
}



// ── V3（ADR-0101 D6）：披露族导出解析（与 live 渲染器同语义）────────────
// 归一化为「标题 + 文本行」；坏载荷 → undefined（面板缺席，不伪造）。

function _parseMethodology(raw: unknown): ExportChromeElement['disclosure'] {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const rows: string[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    if (typeof rec['text'] !== 'string' || !rec['text'].trim()) continue;
    const code = typeof rec['code'] === 'string' && rec['code'] ? `${rec['code']} ` : '';
    rows.push(`${code}${rec['text']}`);
  }
  return rows.length > 0 ? { title: '方法论披露', rows, accent: true } : undefined;
}

function _parseUncertainty(raw: unknown): ExportChromeElement['disclosure'] {
  if (!raw || typeof raw !== 'object') return undefined;
  const rec = raw as Record<string, unknown>;
  const rows: string[] = [];
  if (Array.isArray(rec['items'])) {
    for (const item of rec['items']) {
      if (!item || typeof item !== 'object') continue;
      const r = item as Record<string, unknown>;
      if (typeof r['label'] !== 'string' || !r['label'].trim()) continue;
      // kind → live 同款中文标签（共享 UNCERTAINTY_KIND_LABELS，不漏内码）
      const kindRaw = typeof r['kind'] === 'string' ? r['kind'] : '';
      const kind = _uncertaintyKindLabel(kindRaw);
      const detail = typeof r['detail'] === 'string' && r['detail'] ? `：${r['detail']}` : '';
      rows.push(`${kind} · ${r['label']}${detail}`);
    }
  }
  if (typeof rec['sampleNote'] === 'string' && rec['sampleNote'].trim()) {
    rows.push(rec['sampleNote']);
  }
  return rows.length > 0 ? { title: '不确定性', rows, accent: false } : undefined;
}

function _parseDecision(raw: unknown): ExportChromeElement['disclosure'] {
  if (!raw || typeof raw !== 'object') return undefined;
  const rec = raw as Record<string, unknown>;
  const method = typeof rec['method'] === 'string' && rec['method'].trim() ? rec['method'] : '';
  const title = method ? `决策（${method}）` : '决策';
  const rows: string[] = [];
  const strike: number[] = [];
  // weightSource 首行（与 live 顺序一致）
  if (typeof rec['weightSource'] === 'string' && rec['weightSource'].trim()) {
    rows.push(`权重来源：${rec['weightSource']}`);
  }
  const rowsRaw = rec['rows'];
  if (Array.isArray(rowsRaw)) {
    // 与 live decision-panel 同语义：行数不设上限（boxH 随行数伸缩）；
    // rank 缺失回退 rows.length+1（live 同式）；basis 仅区分 vetoed
    //（删除线），不做内联文本（live 用 data-basis）。
    let seq = 0;
    for (const item of rowsRaw) {
      if (!item || typeof item !== 'object') continue;
      const r = item as Record<string, unknown>;
      if (typeof r['name'] !== 'string' || !r['name'].trim()) continue;
      seq += 1;
      const rank = typeof r['rank'] === 'number' ? `${r['rank']}. ` : `${seq}. `;
      const score = typeof r['score'] === 'number' || typeof r['score'] === 'string' ? ` — ${r['score']}` : '';
      const vetoed = r['basis'] === 'vetoed';
      if (vetoed) strike.push(rows.length);
      rows.push(`${rank}${r['name']}${score}`);
    }
  }
  const vetoesRaw = rec['vetoes'];
  if (Array.isArray(vetoesRaw)) {
    const vetoes = vetoesRaw.filter((v): v is string => typeof v === 'string' && !!v.trim());
    if (vetoes.length) {
      rows.push('硬约束否决：');
      for (const v of vetoes) rows.push(`· ${v}`);
    }
  }
  // 出现条件与 live 对齐：live 仅在 rows 或 vetoes 存在时渲染正文
  //（weightSource 单独存在 → live 空态『暂无决策结果』；导出等价物是
  // 面板缺席 —— 导出画布不画空态卡）。
  return rows.length > 0 ? { title, rows, accent: false, strikeRows: strike } : undefined;
}

/**
 * 导出侧「可视组件」词表（Wave 9 提升到模块级并导出）：fromSpec 门与
 * chrome 路径切换用它判定 —— live 侧 CHROME_RENDERABLE_TYPES 必须包含
 * 它（⊇ 包含测试 export-chrome.parity.test.ts 锁定反向 parity）。
 */
// Review R1（MAJOR-3）：词表本体在 lib/map-components/chrome-types.ts
//（live/export 单一来源）；此处 re-export 维持既有导入面。
import { CHROME_RENDERABLE_TYPES as _SHARED_TYPES } from '@/lib/map-components/chrome-types';
export const VISUAL_TYPES: ReadonlySet<string> = new Set(_SHARED_TYPES);

/**
 * 构建导出 chrome 模型（异步：chartRef 可能需要拉取 session artifact）。
 * 纯派生 —— 不读 DOM、不碰 map 实例；画布尺寸由调用方传入。
 */
export async function buildExportChrome(
  opts: BuildExportChromeOptions,
  canvas: { width: number; height: number },
): Promise<ExportChromeModel> {
  const resolved = resolveMapComponents(opts.spec);
  // review P0：只有**可视**组件在场才走 chrome 路径 —— 仅携带 export_layout
  // 等非可视组件的 spec（#805 场景）不得把导出切到 chrome 路径（否则罗盘/
  // 比例尺/默认标题全部从 legacy 回退中消失）。E-10：enabled 过滤与 live
  // 的 hasSpecChrome 对齐 —— 只有禁用 title 的 spec 走 HUD chrome 栈。
  // graticule（终审 F4）：属于可视输出（导出经纬网），计入 fromSpec ——
  // graticule-only spec 也走 chrome 路径（fallback 罗盘/比例尺 + 网格）。
  const model: ExportChromeModel = {
    fromSpec: resolved.some((c) => VISUAL_TYPES.has(c.type) && c.enabled),
    degradations: [],
    legends: [],
    colorbars: [],
    insets: [],
    panels: [],
  };
  // graticule 组件通道在任何路径下都置位（早退前 —— 终审 F4：此前
  // graticule-only spec 在此处早退，通道被自己的门饿死）
  model.graticuleEnabled = resolved.some(
    (c) => c.type === 'graticule' && c.enabled,
  );
  if (!model.fromSpec) return model;

  // ADR-0084（E-1）：槽位堆叠走共享求解器（与 live 同一实现）—— 导出
  // 此前完全没有堆叠，scale_bar 与 continuous_colorbar 同锚 bottom-right
  // 互相遮挡。floating 组件不参与（用户固定，坐标即位置）。
  // 终审 F2：fallback 注入的罗盘/比例尺（类型缺席时的注入物）也作为
  // 参与者进求解器 —— 否则注入物不占槽位，与 spec 组件同槽遮挡。
  const solverParticipants = resolved
    .filter((c) => c.enabled && !c.floating)
    .map((c) => ({
      id: c.id,
      type: c.type,
      anchor: _anchorOf(c),
      floating: false,
      origin: 'auto' as const,
    }));
  const northAbsent = !resolved.some(
    (c) => c.type === 'north_arrow' && c.enabled,
  );
  const scaleAbsent = !resolved.some(
    (c) => c.type === 'scale_bar' && c.enabled,
  );
  if (northAbsent) {
    solverParticipants.push({
      id: '__fallback_north_arrow',
      type: 'north_arrow',
      anchor: DEFAULT_COMPONENT_ANCHOR['north_arrow'],
      floating: false,
      origin: 'auto' as const,
    });
  }
  if (scaleAbsent) {
    solverParticipants.push({
      id: '__fallback_scale_bar',
      type: 'scale_bar',
      anchor: DEFAULT_COMPONENT_ANCHOR['scale_bar'],
      floating: false,
      origin: 'auto' as const,
    });
  }
  const solved = resolveComponentLayout(solverParticipants, canvas);
  const _stackOf = (c: ResolvedMapComponent) => solved.slots.get(c.id);
  const _fallbackStack = (id: string) => solved.slots.get(id);
  /** 生效锚点 = 求解器裁决槽（user 浮动碰撞侧让后可能换槽）。 */
  const _effectiveAnchor = (c: ResolvedMapComponent): ChromeAnchor =>
    (_stackOf(c)?.slot as ChromeAnchor | undefined) ?? _anchorOf(c);

  const titleComp = resolved.find((c) => c.type === 'title' && c.enabled);
  if (titleComp) {
    model.title = {
      kind: 'title',
      anchor: _effectiveAnchor(titleComp),
      rect: _floatingRectOf(titleComp, opts.viewport, canvas),
      stackIndex: _stackOf(titleComp)?.index ?? 0,
      slotSize: _stackOf(titleComp)?.slotSize ?? 0,
      text: opts.requestTitle || titleComp.text,
    };
  } else if (opts.requestTitle) {
    model.title = { kind: 'title', anchor: 'top-center', text: opts.requestTitle };
  }

  const subtitleComp = resolved.find((c) => c.type === 'subtitle' && c.enabled);
  if (subtitleComp) {
    model.subtitle = {
      kind: 'subtitle',
      anchor: _effectiveAnchor(subtitleComp),
      rect: _floatingRectOf(subtitleComp, opts.viewport, canvas),
      stackIndex: _stackOf(subtitleComp)?.index ?? 0,
      slotSize: _stackOf(subtitleComp)?.slotSize ?? 0,
      text: opts.requestSubtitle || subtitleComp.text,
    };
  } else if (opts.requestSubtitle) {
    model.subtitle = { kind: 'subtitle', anchor: 'top-center', text: opts.requestSubtitle };
  }

  const northComp = resolved.find((c) => c.type === 'north_arrow');
  if (northComp && northComp.enabled) {
    model.northArrow = {
      kind: 'north_arrow',
      anchor: _effectiveAnchor(northComp),
      rect: _floatingRectOf(northComp, opts.viewport, canvas),
      stackIndex: _stackOf(northComp)?.index ?? 0,
      slotSize: _stackOf(northComp)?.slotSize ?? 0,
    };
  } else if (!northComp) {
    // review P0：live 对缺席的 north_arrow 注入 fallback（map-spec-chrome），
    // 导出同款 —— 类型缺席不是"用户显式关闭"（那会是 enabled=false）。
    // 终审 F2：注入物带求解器槽位（与 spec 组件同槽时参与堆叠）。
    model.northArrow = {
      kind: 'north_arrow',
      anchor:
        (_fallbackStack('__fallback_north_arrow')?.slot as ChromeAnchor | undefined)
        ?? DEFAULT_COMPONENT_ANCHOR['north_arrow'],
      stackIndex: _fallbackStack('__fallback_north_arrow')?.index ?? 0,
      slotSize: _fallbackStack('__fallback_north_arrow')?.slotSize ?? 0,
    };
  }

  const scaleComp = resolved.find((c) => c.type === 'scale_bar');
  if (scaleComp && scaleComp.enabled) {
    model.scaleBar = {
      kind: 'scale_bar',
      anchor: _effectiveAnchor(scaleComp),
      rect: _floatingRectOf(scaleComp, opts.viewport, canvas),
      stackIndex: _stackOf(scaleComp)?.index ?? 0,
      slotSize: _stackOf(scaleComp)?.slotSize ?? 0,
      // V3：dual_unit 变体驱动导出第二行英制换算（与 live scale-bar 同式）
      variant: scaleComp.variant || undefined,
    };
  } else if (!scaleComp) {
    model.scaleBar = {
      kind: 'scale_bar',
      anchor:
        (_fallbackStack('__fallback_scale_bar')?.slot as ChromeAnchor | undefined)
        ?? DEFAULT_COMPONENT_ANCHOR['scale_bar'],
      stackIndex: _fallbackStack('__fallback_scale_bar')?.index ?? 0,
      slotSize: _fallbackStack('__fallback_scale_bar')?.slotSize ?? 0,
    };
  }

  // 图例族：spec 组件的 layerId → legend_spec；组件 disabled → 不出图例
  // （此前导出无视 spec enabled，由 HUD 发现独裁 —— parity 修复）。
  // v2：图例族多实例 —— 每个绑定层一个元素（各绑各的 legend_spec），
  // 未绑定 layerId 的实例按类型兜底发现一次（HUD 发现语义，防丢图例）。
  const legendFamily = resolved.filter(
    (c) => (c.type === 'legend' || c.type === 'categorical_legend' || c.type === 'continuous_colorbar'),
  );
  const anyLegendFamily = legendFamily.length > 0;
  let usedFallbackLegend = false;
  for (const comp of legendFamily) {
    if (!comp.enabled) continue;
    const isColorbar = comp.type === 'continuous_colorbar';
    const spec =
      (comp.layerId && opts.legendSpecsByLayer[comp.layerId]) ||
      Object.values(opts.legendSpecsByLayer).find(
        (s) => isColorbar
          ? s.type === 'continuous' || s.type === 'divergent'
          // V4：bivariate 加入 legend 组件的类型兜底发现（与 live
          // legends.tsx legendForComponent 的 wanted 词表同源）
          : s.type === 'graduated' || s.type === 'categorical' || s.type === 'bivariate',
      ) ||
      (!isColorbar ? opts.fallbackLegendSpec : undefined);
    if (!spec) {
      // W7（ADR-0118）：绑定缺失不再静默 —— live 同场景图例缺席，导出侧
      // 补披露诊断（用户知道图例没进导出件及其原因）。
      model.degradations.push({
        code: 'component_skipped_invalid',
        componentId: comp.id,
        detail: `图例组件绑定层无 legend_spec（layerId: ${comp.layerId || '未绑定'}）`,
      });
      continue;
    }
    if (comp.layerId === '' ) {
      // 未绑定实例共用一次兜底发现，避免 N 个未绑定实例画 N 份相同图例
      if (usedFallbackLegend) continue;
      usedFallbackLegend = true;
    }
    // W7：与 live 的内容差异披露 —— live 图例仅示前 8 条（legends.tsx
    // entries.slice(0, 8) + 「…+N」指示），导出件画全集；条目超限时显式披露
    // 该差异。
    // W5：entryCount 口径收敛至 legend-model 单源（条目 = 模型 entries，
    // nodata 已计入末尾 —— 与 live legendEntries、render-scene oracle 同源）。
    // 行为 delta（ADR-0120 收敛表）：continuous 此前计 0，现按三读数计 3。
    const entryCount = deriveLegendModel(spec)?.entries.length ?? 0;
    if (entryCount > 8) {
      model.degradations.push({
        code: 'legend_entries_truncated',
        componentId: comp.id,
        detail: '8',
      });
    }
    const el: ExportChromeElement = {
      kind: isColorbar ? 'colorbar' : 'legend',
      anchor: _effectiveAnchor(comp),
      rect: _floatingRectOf(comp, opts.viewport, canvas),
      stackIndex: _stackOf(comp)?.index ?? 0,
      slotSize: _stackOf(comp)?.slotSize ?? 0,
      legendSpec: spec,
      // V3（R2）：色条变体进导出 —— vertical（方向回退）/scientific/stepped
      // 与 live colorbar 同词表（options.orientation 优先，variant 兜底）
      variant:
        isColorbar
          ? (comp.variant ||
             (comp.options?.['orientation'] === 'vertical' ? 'vertical' : undefined))
          : undefined,
    };
    if (isColorbar) {
      model.colorbars.push(el);
      if (!model.colorbar) model.colorbar = el;
    } else {
      model.legends.push(el);
      if (!model.legend) model.legend = el;
    }
  }
  if (!anyLegendFamily && opts.fallbackLegendSpec) {
    // spec 无图例组件（旧 spec）→ HUD 兜底（原行为），槽位用类型默认
    model.legend = {
      kind: 'legend',
      anchor: DEFAULT_COMPONENT_ANCHOR['legend'],
      legendSpec: opts.fallbackLegendSpec,
    };
    model.legends.push(model.legend);
  }

  const attrComp = resolved.find((c) => c.type === 'attribution' && c.enabled);
  if (attrComp && attrComp.text) {
    model.attribution = {
      kind: 'attribution',
      anchor: _effectiveAnchor(attrComp),
      rect: _floatingRectOf(attrComp, opts.viewport, canvas),
      stackIndex: _stackOf(attrComp)?.index ?? 0,
      slotSize: _stackOf(attrComp)?.slotSize ?? 0,
      text: attrComp.text,
    };
  }

  // P6：图框组件（全画布，anchor 'none' —— 不参与槽位堆叠）
  const borderComp = resolved.find((c) => c.type === 'map_border' && c.enabled);
  if (borderComp) {
    model.border = {
      kind: 'map_border',
      anchor: 'none',
      variant:
        borderComp.variant ||
        (borderComp.component.variant as string | undefined) ||
        'minimal',
    };
  }

  // 终审 F1：annotation 此前在 VISUAL_TYPES 里翻转 chrome 路径却从不导出
  // （导出静默丢注释 + 压制 legacy 回退）—— 现按 live 语义导出文本注释卡。
  // v2：callout（anchorCoordinate）/ group（items）同链导出 —— 与
  // live annotation.tsx 共用 geo-anchor 投影语义（画布像素侧由
  // drawChromeAnnotation 换算，方向/避让规则一致）。
  for (const c of resolved) {
    if (c.type !== 'annotation' || !c.enabled) continue;
    const opts_ = c.options;
    const items = parseAnnotationItems(opts_['items']);
    const anchorCoord = parseAnchorCoordinate(opts_['anchor']);
    if (items) {
      if (!items.length) continue;
      model.panels.push({
        kind: 'annotation',
        anchor: _effectiveAnchor(c),
        rect: _floatingRectOf(c, opts.viewport, canvas),
        stackIndex: _stackOf(c)?.index ?? 0,
        slotSize: _stackOf(c)?.slotSize ?? 0,
        items,
        text: undefined,
      });
      continue;
    }
    if (!c.text.trim()) continue;
    model.panels.push({
      kind: 'annotation',
      anchor: _effectiveAnchor(c),
      rect: _floatingRectOf(c, opts.viewport, canvas),
      stackIndex: _stackOf(c)?.index ?? 0,
      slotSize: _stackOf(c)?.slotSize ?? 0,
      text: c.text,
      ...(anchorCoord ? { anchorCoordinate: anchorCoord } : {}),
    });
  }

  // v2：区位插图 —— bbox 必备（缺省自弃，与 live inset-map 渲染器同门）；
  // 主图指示范围缺省用请求携带的 live bounds（调用方传 viewportBounds）。
  for (const c of resolved) {
    if (c.type !== 'inset_map' || !c.enabled) continue;
    const insetBbox = parseBbox4(c.options['bbox']);
    if (!insetBbox) {
      // W7：bbox 缺失/非法（live 同场景自弃）→ 导出缺席 + 显式披露。
      model.degradations.push({
        code: 'component_skipped_invalid',
        componentId: c.id,
        detail: 'inset_map 缺有效 bbox',
      });
      continue;
    }
    const mainBbox = parseBbox4(c.options['mainBbox']) ?? opts.viewportBounds ?? undefined;
    const labelOpt = c.options['label'];
    const boundary = parseBoundary(c.options['boundary']);
    model.insets.push({
      kind: 'inset_map',
      anchor: _effectiveAnchor(c),
      rect: _floatingRectOf(c, opts.viewport, canvas),
      stackIndex: _stackOf(c)?.index ?? 0,
      slotSize: _stackOf(c)?.slotSize ?? 0,
      variant: c.variant || 'overview',
      text: typeof labelOpt === 'string' && labelOpt ? labelOpt : undefined,
      insetBbox,
      ...(mainBbox ? { insetMainBbox: mainBbox } : {}),
      ...(boundary ? { insetBoundary: boundary } : {}),
    });
  }

  // 浮动面板族：statistics_panel / chart_panel（collapsed 面板导出为折叠
  // 标题条 —— 与 live 语义一致，不展开用户折叠的面板）。
  for (const c of resolved) {
    if (!c.enabled) continue;
    if (c.type === 'statistics_panel') {
      const stats = _parseStats(c.options['stats']);
      if (stats) {
        model.panels.push({
          kind: 'statistics',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          stats,
          // E-2：collapsed 是 mode 无关字段（锚定面板的折叠此前在导出侧
          // 永远丢失 —— live 折叠、导出展开）。
          text: c.collapsed ? stats.title || '统计' : undefined,
        });
      }
    } else if (c.type === 'chart_panel') {
      const inline = _parseChart(c.options['chart']);
      const chartRef = c.options['chartRef'];
      let chart = inline;
      if (!chart && typeof chartRef === 'string' && chartRef && opts.loadChart) {
        try {
          chart = (await opts.loadChart(chartRef)) ?? undefined;
        } catch {
          /* 拉取失败 → 面板缺席（如实：无数据不伪造） */
        }
      }
      if (!chart && c.enabled) {
        // Wave 9：拉取失败/载荷缺失 → 此前面板静默缺席；现在记录显式降级
        //（exporter 汇入导出后系统消息，用户知道图表面板没进导出件）。
        model.degradations.push({
          code: 'chart_ref_unavailable',
          componentId: c.id,
          detail: typeof chartRef === 'string' ? `ref ${chartRef} 不可用` : 'inline 载荷非法',
        });
      }
      if (chart && !DRAWN_CHART_KINDS.has(chart.type)) {
        // W7（ADR-0118）：死词表激活 —— 未知 kind 此前只在绘制端画占位文案，
        // 词表码无人发射；现在模型侧显式披露（detail=kind 内码）。
        model.degradations.push({
          code: 'chart_kind_unsupported_export',
          componentId: c.id,
          detail: chart.type,
        });
      }
      if (chart) {
        model.panels.push({
          kind: 'chart',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          chart,
          // E-2 对称修复（§13 collapsed parity）：collapsed 的 chart 面板
          // 此前导出仍展开 —— live 折叠、导出展开。text 携带标题 →
          // drawChromeChartPanel 绘制折叠标题条（与 statistics 同约定）。
          text: c.collapsed ? chart.title : undefined,
        });
      }
    } else if (c.type === 'methodology_note') {
      const disclosure = _parseMethodology(c.options['warnings']);
      if (disclosure) {
        model.panels.push({
          kind: 'methodology',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          disclosure,
          text: c.collapsed ? disclosure.title : undefined,
        });
      }
    } else if (c.type === 'uncertainty_panel') {
      const disclosure = _parseUncertainty(c.options['uncertainty']);
      if (disclosure) {
        model.panels.push({
          kind: 'uncertainty',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          disclosure,
          text: c.collapsed ? disclosure.title : undefined,
        });
      }
    } else if (c.type === 'decision_panel') {
      const disclosure = _parseDecision(c.options['decision']);
      if (disclosure) {
        model.panels.push({
          kind: 'decision',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          disclosure,
          text: c.collapsed ? disclosure.title : undefined,
        });
      }
    } else if (c.type === 'table_panel') {
      // V4：表格导出 —— 与 live table-panel 同双通道取数（tableRef
      // artifact / layerId 图层属性；inline table 仅在两者缺席时消费）。
      // 有界快照（≤8 行 ≤6 列）+ 总量尾注披露；无数据 → 面板缺席
      //（不画空表冒充，与 chart 拉取失败同纪律）。
      const titleOpt =
        typeof c.options['title'] === 'string' && (c.options['title'] as string).trim()
          ? (c.options['title'] as string)
          : undefined;
      const columnsOpt = Array.isArray(c.options['columns'])
        ? (c.options['columns'] as unknown[]).filter((x): x is string => typeof x === 'string')
        : undefined;
      const refOpt = typeof c.options['tableRef'] === 'string' ? c.options['tableRef'].trim() : '';
      const layerOpt = typeof c.options['layerId'] === 'string' ? c.options['layerId'].trim() : '';
      let table: TablePanelData | null = null;
      try {
        if (refOpt) {
          if (opts.loadTable) {
            table = buildExportTableData(titleOpt ?? '数据表', await opts.loadTable(refOpt), columnsOpt);
          }
        } else if (layerOpt) {
          if (opts.loadLayerTable) {
            table = buildExportTableData(
              titleOpt ?? '属性表',
              await opts.loadLayerTable(layerOpt),
              columnsOpt,
            );
          }
        } else if (c.options['table'] != null) {
          table = buildExportTableData(titleOpt ?? '数据表', c.options['table'], columnsOpt);
        }
      } catch {
        table = null; // 拉取失败 → 面板缺席（无数据不伪造）
      }
      if (!table && c.enabled) {
        // Wave 9：同 chart —— 表格面板缺席记录显式降级，不再静默。
        model.degradations.push({
          code: 'table_ref_unavailable',
          componentId: c.id,
          detail: refOpt ? `ref ${refOpt} 不可用` : layerOpt ? `图层 ${layerOpt} 无属性表` : 'inline 载荷非法',
        });
      }
      if (table) {
        model.panels.push({
          kind: 'table',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          table,
          // E-2 对称：collapsed 表格导出折叠标题条（与 statistics/chart 同约定）
          text: c.collapsed ? table.title : undefined,
        });
      }
    }
  }

  return model;
}

// ── 画布绘制 ────────────────────────────────────────────────────────

interface DrawCtx {
  ctx: CanvasRenderingContext2D;
  darkMode: boolean;
  scalePx: (v: number) => number;
  targetW: number;
  targetH: number;
  style: LayoutStyle;
}

/**
 * anchor → 画布槽锚点。**y 一律是"距所属边的 margin 距离"**（top 槽距
 * 顶边、bottom 槽距底边），vAlign 标明所属边 —— 消费端按 vAlign 恰好做
 * 一次 targetH - y 换算（review P0：此前 bottom 槽返回画布坐标又被二次
 * 相减，全部底部组件被画到顶部）。x 直接是画布坐标（左/中/右对齐基线）。
 */
export function anchorOrigin(
  anchor: ChromeAnchor,
  d: { targetW: number; targetH: number; marginX: number; marginY: number },
): { x: number; y: number; align: 'left' | 'center' | 'right'; vAlign: 'top' | 'bottom' } {
  switch (anchor) {
    case 'top-left':
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'top' };
    case 'top-center':
      return { x: d.targetW / 2, y: d.marginY, align: 'center', vAlign: 'top' };
    case 'top-right':
      return { x: d.targetW - d.marginX, y: d.marginY, align: 'right', vAlign: 'top' };
    case 'bottom-left':
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'bottom' };
    case 'bottom-center':
      return { x: d.targetW / 2, y: d.marginY, align: 'center', vAlign: 'bottom' };
    case 'bottom-right':
      return { x: d.targetW - d.marginX, y: d.marginY, align: 'right', vAlign: 'bottom' };
    default:
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'top' };
  }
}

function _text(d: DrawCtx, s: string, x: number, y: number, align: CanvasTextAlign) {
  d.ctx.textAlign = align;
  d.ctx.fillText(s, x, y);
  d.ctx.textAlign = 'left';
}

/** title/subtitle（anchor 对齐；vAlign=bottom 时 y 为基线底部）。 */
export function drawChromeText(
  d: DrawCtx,
  el: ExportChromeElement,
  fontPx: number,
  color: string,
  opts: { marginX: number; marginY?: number; dy?: number },
) {
  if (!el.text) return;
  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 52 });
  d.ctx.fillStyle = color;
  d.ctx.font = `bold ${d.scalePx(fontPx)}px ${d.style.fontFamily}`;
  // margin 语义统一：origin.y 已是画布像素（调用方传入 scalePx 后的值）；
  // bottom 槽恰好一次 targetH - y 换算。
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y : origin.y + (opts.dy ?? 0);
  _text(d, el.text, origin.x, y, origin.align);
}

/** 罗盘 —— 与 live 同一旋转约定（-bearing 逆时针；此前导出符号相反）。 */
export function drawChromeNorthArrow(
  d: DrawCtx,
  el: ExportChromeElement,
  bearing: number,
  opts: { marginX: number; marginY?: number },
) {
  const r = d.scalePx(28);
  const origin = el.rect
    ? { x: el.rect.x + r, y: el.rect.y + r }
    : (() => {
        const o = anchorOrigin(el.anchor, {
          targetW: d.targetW, targetH: d.targetH,
          marginX: opts.marginX, marginY: (opts.marginY ?? 64),
        });
        return {
          x: o.align === 'right' ? o.x - r : o.align === 'center' ? o.x : o.x + r,
          y: o.vAlign === 'bottom' ? d.targetH - o.y - r : o.y + r,
        };
      })();
  const { ctx } = d;
  ctx.save();
  ctx.translate(origin.x, origin.y);
  // parity 修复：live 是 rotate(-bearing)（CSS 逆时针）；canvas y 轴向下，
  // 正角为顺时针 —— 取 -bearing 才与 live 同向。
  ctx.rotate((-bearing * Math.PI) / 180);

  ctx.shadowColor = 'rgba(0,0,0,0.4)';
  ctx.shadowBlur = d.scalePx(6);
  ctx.beginPath();
  ctx.moveTo(0, -r);
  ctx.lineTo(r * 0.35, 0);
  ctx.lineTo(0, r * 0.2);
  ctx.lineTo(-r * 0.35, 0);
  ctx.closePath();
  ctx.fillStyle = d.style.accentColor || '#e53e3e';
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(0, r);
  ctx.lineTo(r * 0.35, 0);
  ctx.lineTo(0, r * 0.2);
  ctx.lineTo(-r * 0.35, 0);
  ctx.closePath();
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#f8fafc';
  ctx.fill();

  ctx.shadowBlur = 0;
  ctx.beginPath();
  ctx.arc(0, 0, d.scalePx(4), 0, 2 * Math.PI);
  ctx.fillStyle = '#1e293b';
  ctx.fill();
  ctx.restore();

  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(13)}px ${d.style.fontFamily}`;
  _text(d, 'N', origin.x, origin.y - r - d.scalePx(6), 'center');
}

/** 比例尺（anchor 槽位；nice-number 算法与 legacy 同源）。 */
export function drawChromeScaleBar(
  d: DrawCtx,
  el: ExportChromeElement,
  metersPerPx: number,
  pxPerLogical: number,
  opts: { marginX: number; marginY?: number },
) {
  const { ctx } = d;
  const logicalW = d.targetW / pxPerLogical;
  const targetPx = Math.round(logicalW * 0.12);
  // ADR-0084（E-3）：与 live 共用同一 nice-number 算法（scale-math.ts）。
  const { meters: nice, px: barPxLogical } = computeNiceScale(metersPerPx, targetPx);
  const barPx = barPxLogical * pxPerLogical;
  const barLabel = formatScaleLabel(nice);
  const barH = d.scalePx(8);

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 52 });
  const bx = origin.align === 'right' ? origin.x - barPx : origin.align === 'center' ? origin.x - barPx / 2 : origin.x;
  const by = origin.vAlign === 'bottom' ? d.targetH - origin.y - barH : origin.y;

  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.8)';
  ctx.lineWidth = d.scalePx(1.5);
  ctx.strokeRect(bx, by, barPx, barH);
  const segCount = 4;
  const segW = barPx / segCount;
  for (let i = 0; i < segCount; i++) {
    ctx.fillStyle =
      i % 2 === 0
        ? d.darkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.8)'
        : 'rgba(0,0,0,0)';
    ctx.fillRect(bx + i * segW, by, segW, barH);
  }
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(13)}px ${d.style.fontFamily}`;
  _text(d, '0', bx, by - d.scalePx(4), 'left');
  _text(d, barLabel, bx + barPx, by - d.scalePx(4), 'right');
  // V3：dual_unit —— 同一根比例尺条的实际代表距离换算英制第二行
  // （与 live scale-bar formatImperial 同式；非独立第二根尺）。
  if (el.variant === 'dual_unit') {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(71,85,105,0.9)';
    ctx.font = `${d.scalePx(11)}px ${d.style.fontFamily}`;
    _text(d, formatImperialLabel(nice), bx + barPx, by + barH + d.scalePx(12), 'right');
  }
}

/** 英制比例标签（ft/mi；与 live scale-bar.tsx formatImperial 同式）。 */
export function formatImperialLabel(meters: number): string {
  const feet = meters * 3.28084;
  if (feet >= 5280) {
    const miles = feet / 5280;
    return `${miles.toFixed(miles >= 10 ? 0 : 1)} mi`;
  }
  return `${Math.round(feet)} ft`;
}

/** 连续色条 —— 渐变 ramp + min/mid/max + unit（与 live colorbar 同形态）。 */
export function drawChromeColorbar(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const spec = el.legendSpec as
    | { min?: number; max?: number; palette_colors?: string[]; unit?: string; field?: string; nodata?: { color?: string; label?: string } }
    | undefined;
  // E-5：与 live 同款退化语义 —— 无 palette 不绘制（不伪造默认 ramp）；
  // 缺 min/max 只画裸条不带数值标签（live colorbar.tsx 同款），不再整体丢弃。
  const rawColors = spec?.palette_colors ?? [];
  if (rawColors.length === 0 || !spec) return;
  const colors =
    rawColors.length >= 2 ? rawColors : [rawColors[0], rawColors[0]];
  const hasRange =
    typeof spec.min === 'number' &&
    typeof spec.max === 'number' &&
    spec.min !== spec.max;
  // V3（R2 parity）：导出侧与 live colorbar 同词表 —— vertical（方向）、
  // stepped（离散色阶块）、scientific（中间刻度行）。
  const variant = el.variant || '';
  const vertical = variant === 'vertical';
  const stepped = variant === 'stepped';
  const scientific = variant === 'scientific';
  const { ctx } = d;
  const padding = d.scalePx(10);
  const fmt = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : n.toFixed(1);
  const suffix = spec.unit ? ` ${spec.unit}` : '';

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 56 });

  const strokeRampBorder = (x: number, y2: number, w: number, h: number) => {
    ctx.strokeStyle = 'rgba(128,128,128,0.4)';
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(x, y2, w, h);
  };
  /** 连续渐变或离散色阶（stepped），方向随 vertical。 */
  const drawRamp = (x: number, y2: number, w: number, h: number) => {
    if (stepped) {
      // 离散色阶：等分色块（与 live stepped basis-0/grow 等分同语义）
      const n = colors.length;
      for (let i = 0; i < n; i++) {
        ctx.fillStyle = colors[i];
        if (vertical) {
          const cellH = h / n;
          ctx.fillRect(x, y2 + i * cellH, w, cellH);
        } else {
          const cellW = w / n;
          ctx.fillRect(x + i * cellW, y2, cellW, h);
        }
      }
      strokeRampBorder(x, y2, w, h);
      return;
    }
    const grad = vertical
      ? ctx.createLinearGradient(0, y2, 0, y2 + h)
      : ctx.createLinearGradient(x, 0, x + w, 0);
    colors.forEach((c, i) => grad.addColorStop(colors.length === 1 ? 1 : i / (colors.length - 1), c));
    ctx.fillStyle = grad;
    ctx.fillRect(x, y2, w, h);
    strokeRampBorder(x, y2, w, h);
  };
  // W7 nodata parity：色条尾部 nodata 色块条目（与 drawChromeLegend/live
  // legendEntries 同源 —— nodata.color 来自 withNoDataGuard 同一规则）。
  const nodata = spec.nodata?.color
    ? { color: spec.nodata.color, label: spec.nodata.label || '无数据' }
    : undefined;
  const nodataH = nodata ? d.scalePx(16) : 0;
  const drawNodataRow = (nx: number, ny: number) => {
    if (!nodata) return;
    ctx.fillStyle = nodata.color;
    ctx.fillRect(nx, ny, d.scalePx(14), d.scalePx(10));
    ctx.strokeStyle = 'rgba(128,128,128,0.4)';
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(nx, ny, d.scalePx(14), d.scalePx(10));
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.7)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    _text(d, nodata.label || '无数据', nx + d.scalePx(20), ny + d.scalePx(9), 'left');
  };

  if (vertical) {
    // 纵向：窄高条 + 右侧 min/max（live 竖排同构）；条内首色=顶部（min 在顶，
    // 与 live linear-gradient(to bottom) 同向）
    const barW = d.scalePx(12);
    const barH = d.scalePx(120);
    const titleH = spec.field ? d.scalePx(18) : 0;
    const labelsW = hasRange ? d.scalePx(56) : 0;
    const boxW = padding * 2 + barW + (labelsW ? d.scalePx(6) + labelsW : 0);
    const boxH = padding * 2 + titleH + barH + nodataH;
    const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
    const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;
    _chromePanel(d, lx, ly, boxW, boxH);
    let y = ly + padding;
    if (spec.field) {
      ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.7)' : 'rgba(100,116,139,0.9)';
      ctx.font = `${d.scalePx(10)}px monospace`;
      _text(d, spec.field.toUpperCase(), lx + padding, y + d.scalePx(10), 'left');
      y += titleH;
    }
    drawRamp(lx + padding, y, barW, barH);
    if (hasRange) {
      const tx = lx + padding + barW + d.scalePx(6);
      ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.8)';
      ctx.font = `${d.scalePx(10)}px sans-serif`;
      _text(d, `${fmt(spec.max!)}${suffix}`, tx, y + d.scalePx(8), 'left');
      _text(d, `${fmt(spec.min!)}${suffix}`, tx, y + barH, 'left');
    }
    drawNodataRow(lx + padding, y + barH + d.scalePx(6));
    return;
  }

  // 横向（缺省）：渐变/色阶条 +（scientific）中间刻度行 + 两端读数
  const barW = d.scalePx(160);
  const barH = d.scalePx(10);
  const titleH = spec.field ? d.scalePx(18) : 0;
  const ticksH = scientific && hasRange ? d.scalePx(14) : 0;
  // 终审 F6：无量化范围 = 裸条 —— 不预留数值标签带（此前空占 ~16px）
  const labelsH = hasRange ? d.scalePx(16) : 0;
  const boxW = padding * 2 + barW;
  const boxH = padding * 2 + titleH + barH + ticksH + labelsH + nodataH;
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  let y = ly + padding;
  if (spec.field) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.7)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(10)}px monospace`;
    _text(d, spec.field.toUpperCase(), lx + padding, y + d.scalePx(10), 'left');
    y += titleH;
  }
  drawRamp(lx + padding, y, barW, barH);
  y += barH + d.scalePx(4);
  if (hasRange) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.8)';
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    if (scientific) {
      // 与 live scientific 同式：25/50/75% 内插读数行
      for (const t of [0.25, 0.5, 0.75]) {
        const v = Number(spec.min) + (Number(spec.max) - Number(spec.min)) * t;
        _text(d, fmt(v), lx + padding + barW * t, y + d.scalePx(10), 'center');
      }
      y += ticksH;
    }
    // 数值标签只在有量化范围时绘制（live 同款：无范围 = 裸条）
    _text(d, `${fmt(spec.min!)}${suffix}`, lx + padding, y + d.scalePx(10), 'left');
    _text(
      d,
      `${fmt((spec.min! + spec.max!) / 2)}`,
      lx + padding + barW / 2,
      y + d.scalePx(10),
      'center',
    );
    _text(d, `${fmt(spec.max!)}${suffix}`, lx + padding + barW, y + d.scalePx(10), 'right');
  }
  drawNodataRow(lx + padding, y + labelsH);
}

/** 离散/分级图例（anchor 槽位版 _drawDiscreteLegend）。 */
export function drawChromeLegend(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const spec = el.legendSpec as
    | {
        type: string;
        field?: string;
        breaks?: number[];
        palette_colors?: string[];
        palette?: string;
        categories?: Array<{ color?: string; label?: string; key?: string }>;
        min?: number;
        max?: number;
        unit?: string;
        /** W7 parity：live 图例标题源（legends.tsx 同款）。 */
        title?: string;
        /** W7 parity：nodata 规则（与 withNoDataGuard 的 nodata.color 同源）。 */
        nodata?: { color?: string; label?: string };
      }
    | undefined;
  if (!spec) return;

  // V4：双变量色阵图例 —— legend.type === 'bivariate' 走专用绘制器
  //（与 live legends.tsx 的 BivariateMatrix 类型分派同语义）。
  if (spec.type === 'bivariate') {
    drawChromeBivariateLegend(d, el, opts);
    return;
  }

  // W5：条目推导收敛至 legend-model 单源 —— canvas 侧此前忽略
  // spec.labels 且不按 palette 截断（graduated 画超界条目），现与
  // live/vector 同口径。行为 delta 已登记于 ADR-0120 收敛表。
  const model = deriveLegendModel(spec as LegendSpec);
  if (!model) return;
  const entries = model.entries;

  const { ctx } = d;
  const itemH = d.scalePx(22);
  const itemW = d.scalePx(18);
  const padding = d.scalePx(10);
  const gapX = d.scalePx(8);
  ctx.font = `${d.scalePx(11)}px sans-serif`;
  let maxTextW = 0;
  for (const e of entries) maxTextW = Math.max(maxTextW, ctx.measureText(e.label).width);
  const legendW = padding * 2 + itemW + gapX + maxTextW + d.scalePx(10);
  const legendH = padding * 2 + d.scalePx(24) + entries.length * itemH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 56 });
  const lx = origin.align === 'right' ? origin.x - legendW : origin.align === 'center' ? origin.x - legendW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - legendH : origin.y;

  _chromePanel(d, lx, ly, legendW, legendH);
  ctx.fillStyle = d.darkMode ? '#00f2ff' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  // W5 parity：标题回退统一模型口径（原 '未知字段' 分支删除）。
  _text(d, model.title, lx + padding, ly + padding + d.scalePx(12), 'left');
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const iy = ly + padding + d.scalePx(24) + i * itemH;
    ctx.fillStyle = e.color;
    ctx.fillRect(lx + padding, iy, itemW, itemH - d.scalePx(4));
    ctx.strokeStyle = 'rgba(128,128,128,0.4)';
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(lx + padding, iy, itemW, itemH - d.scalePx(4));
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : '#334155';
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    _text(d, e.label, lx + padding + itemW + gapX, iy + itemH - d.scalePx(8), 'left');
  }
}

/**
 * V4：双变量色阵图例（n×n 色阵 + 「→label_a / ↑label_b」轴标）。
 * 颜色与 live BivariateMatrix 逐格同源（legend.colors 行主序：row=变量 B、
 * col=变量 A）；n 夹取 [2,4]（live 同式）；colors 不足以铺 n×n → 不绘制
 *（不伪造色阵 —— live return null 同门）。
 */
export function drawChromeBivariateLegend(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const spec = el.legendSpec as
    | { type?: string; colors?: string[]; n?: number; label_a?: string; label_b?: string; title?: string }
    | undefined;
  if (!spec || spec.type !== 'bivariate') return;
  const colors = Array.isArray(spec.colors) ? spec.colors : [];
  const nRaw = Number(spec.n ?? 3);
  if (!Number.isFinite(nRaw)) return;
  const n = Math.min(4, Math.max(2, nRaw));
  if (colors.length < n * n) return;

  const { ctx } = d;
  const padding = d.scalePx(10);
  const cell = d.scalePx(14);
  const gap = d.scalePx(1);
  const titleH = spec.title ? d.scalePx(16) : 0;
  const axisRowH = d.scalePx(16);
  const axisColW = d.scalePx(18);
  const gridW = n * cell + (n - 1) * gap;
  const legendW = padding * 2 + axisColW + gridW;
  const legendH = padding * 2 + titleH + gridW + axisRowH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 56 });
  const lx = origin.align === 'right' ? origin.x - legendW : origin.align === 'center' ? origin.x - legendW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - legendH : origin.y;

  _chromePanel(d, lx, ly, legendW, legendH);
  let y = ly + padding;
  if (spec.title) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
    ctx.font = `bold ${d.scalePx(11)}px sans-serif`;
    _text(d, spec.title, lx + padding, y + d.scalePx(10), 'left');
    y += titleH;
  }
  const gridX = lx + padding + axisColW;
  // 色阵（行主序：row=变量 B、col=变量 A —— 与 live grid 逐格 colors[i] 同源）
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      ctx.fillStyle = colors[r * n + c];
      ctx.fillRect(gridX + c * (cell + gap), y + r * (cell + gap), cell, cell);
    }
  }
  // 轴标：↑label_b（行，阵左侧）/ →label_a（列，阵下方）—— live 同款箭头词
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.9)';
  ctx.font = `${d.scalePx(10)}px sans-serif`;
  _text(d, `↑${(spec.label_b ?? '').slice(0, 4)}`, gridX - d.scalePx(4), y + cell / 2 + d.scalePx(3), 'right');
  _text(d, `→${(spec.label_a ?? '').slice(0, 10)}`, gridX, y + gridW + d.scalePx(12), 'left');
}

/** 署名行（anchor 槽位；此前导出完全不读 spec attribution 组件）。 */
export function drawChromeAttribution(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.text) return;
  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 22 });
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y : origin.y;
  d.ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.4)';
  d.ctx.font = `${d.scalePx(11)}px sans-serif`;
  _text(d, el.text, origin.x, y, origin.align);
}

/** 统计卡（title + label/value 行）。 */
export function drawChromeStatsPanel(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.stats) return;
  const { ctx } = d;
  const padding = d.scalePx(12);
  const rowH = d.scalePx(22);
  const titleH = el.stats.title ? d.scalePx(26) : 0;
  const collapsed = el.text !== undefined;
  const boxW = el.rect?.width ?? d.scalePx(240);
  const boxH = collapsed
    ? d.scalePx(36)
    : padding * 2 + titleH + el.stats.items.length * rowH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  let y = ly + padding;
  if (el.stats.title || collapsed) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    _text(d, collapsed ? el.text || el.stats.title || '统计' : el.stats.title!, lx + padding, y + d.scalePx(12), 'left');
    y += titleH;
  }
  if (collapsed) return;
  for (const item of el.stats.items) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    _text(d, item.label ?? '', lx + padding, y + d.scalePx(14), 'left');
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#0f172a';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    const value = `${item.value ?? ''}${item.unit ? ` ${item.unit}` : ''}`;
    _text(d, value, lx + boxW - padding, y + d.scalePx(14), 'right');
    y += rowH;
  }
}

/**
 * V3（ADR-0101 D6）：披露族导出（methodology / uncertainty / decision）。
 * 归一化「标题 + 文本行」卡片；methodology 带警示色左边条（与 live
 * border-status-warning 同语义）。collapsed 导出折叠标题条（E-2 约定）。
 */
export function drawChromeDisclosurePanel(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.disclosure || el.disclosure.rows.length === 0) return;
  const { ctx } = d;
  const padding = d.scalePx(12);
  const rowH = d.scalePx(20);
  const titleH = d.scalePx(24);
  const collapsed = el.text !== undefined;
  const boxW = el.rect?.width ?? d.scalePx(250);
  const boxH = collapsed ? d.scalePx(36) : padding * 2 + titleH + el.disclosure.rows.length * rowH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  let y = ly + padding;
  if (collapsed) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    _text(d, el.text || el.disclosure.title, lx + padding, y + d.scalePx(12), 'left');
    return;
  }
  // 标题（methodology 带警示色标记条）
  if (el.disclosure.accent) {
    ctx.fillStyle = d.darkMode ? 'rgba(250, 204, 21, 0.9)' : 'rgba(217, 119, 6, 0.95)';
    ctx.fillRect(lx, ly, d.scalePx(3), boxH);
  }
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  _text(d, el.disclosure.title, lx + padding, y + d.scalePx(12), 'left');
  y += titleH;
  const strike = new Set(el.disclosure.strikeRows ?? []);
  for (let i = 0; i < el.disclosure.rows.length; i++) {
    const row = el.disclosure.rows[i];
    const text = _clipText(ctx, row, boxW - padding * 2);
    ctx.fillStyle = strike.has(i)
      ? d.darkMode ? 'rgba(255,255,255,0.5)' : 'rgba(100,116,139,0.9)'
      : d.darkMode ? 'rgba(255,255,255,0.85)' : '#1e293b';
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    const textY = y + d.scalePx(12);
    _text(d, text, lx + padding, textY, 'left');
    if (strike.has(i)) {
      // vetoed 删除线（与 live line-through 同语义）
      const w = ctx.measureText(text).width;
      ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.5)' : 'rgba(71,85,105,0.8)';
      ctx.lineWidth = d.scalePx(1);
      ctx.beginPath();
      ctx.moveTo(lx + padding, textY - d.scalePx(3));
      ctx.lineTo(lx + padding + w, textY - d.scalePx(3));
      ctx.stroke();
    }
    y += rowH;
  }
}

/**
 * V4：表格面板导出（有界快照 —— 标题条 + 表头 + ≤8 行 + 截断尾注）。
 * 载荷由 buildExportChrome 双通道装配（tableRef artifact / layerId 图层
 * 属性 / inline table）；列宽均分，单元格单行截断（导出画布无横向滚动，
 * 与 live 虚拟化行窗口同一「有界显示面」纪律）。collapsed 导出折叠标题条
 *（与 statistics/chart 同约定）。无数据 → 面板缺席，不画空表冒充。
 */
export function drawChromeTable(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const table = el.table;
  if (!table || table.columns.length === 0 || table.rows.length === 0) return;
  const { ctx } = d;
  const padding = d.scalePx(12);
  const titleH = d.scalePx(24);
  const rowH = d.scalePx(22);
  const collapsed = el.text !== undefined;
  const hiddenRows = Math.max(table.totalCount - table.rows.length, 0);
  const hiddenCols =
    table.totalColumns !== undefined && table.totalColumns > table.columns.length
      ? table.totalColumns - table.columns.length
      : 0;
  const footerParts: string[] = [];
  if (hiddenRows > 0) footerParts.push(`…${hiddenRows} 行未显示`);
  if (hiddenCols > 0) footerParts.push(`…${hiddenCols} 列未显示`);
  const footerH = footerParts.length ? d.scalePx(16) : 0;
  const boxW = el.rect?.width ?? d.scalePx(320);
  const boxH = collapsed
    ? d.scalePx(36)
    : padding * 2 + titleH + rowH * (table.rows.length + 1) + footerH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  _text(d, collapsed ? el.text || table.title : table.title, lx + padding, ly + padding + d.scalePx(12), 'left');
  if (collapsed) return;

  const gridX = lx + padding;
  const gridW = boxW - padding * 2;
  const colW = gridW / table.columns.length;
  let y = ly + padding + titleH;
  // 表头（可截断；底部分隔线 —— live border-b 同语义）
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.75)' : 'rgba(30,41,59,0.85)';
  ctx.font = `bold ${d.scalePx(10)}px sans-serif`;
  table.columns.forEach((col, ci) => {
    _text(d, _clipText(ctx, col, colW - d.scalePx(8)), gridX + ci * colW + d.scalePx(4), y + d.scalePx(14), 'left');
  });
  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.25)' : 'rgba(30,41,59,0.3)';
  ctx.lineWidth = d.scalePx(1);
  ctx.beginPath();
  ctx.moveTo(gridX, y + rowH - d.scalePx(4));
  ctx.lineTo(gridX + gridW, y + rowH - d.scalePx(4));
  ctx.stroke();
  y += rowH;
  // 行快照（≤8 行；隔行淡底 —— live hover 底纹的静态近似）
  ctx.font = `${d.scalePx(10)}px sans-serif`;
  table.rows.forEach((row, ri) => {
    if (ri % 2 === 1) {
      ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)';
      ctx.fillRect(gridX, y, gridW, rowH);
    }
    table.columns.forEach((col, ci) => {
      const raw = row[col];
      const text = raw == null ? '' : typeof raw === 'object' ? (Array.isArray(raw) ? `[…]` : '{…}') : String(raw);
      ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : '#1e293b';
      _text(d, _clipText(ctx, text, colW - d.scalePx(8)), gridX + ci * colW + d.scalePx(4), y + d.scalePx(14), 'left');
    });
    y += rowH;
  });
  // 截断披露（诚实：快照外行/列计数）
  if (footerParts.length) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.5)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    _text(d, footerParts.join(' · '), gridX, y + d.scalePx(12), 'left');
  }
}

/** 画布单行文本截断（导出侧无自动换行；与 stats 卡同一裁剪约定）。 */
function _clipText(ctx: CanvasRenderingContext2D, text: string, maxW: number): string {
  if (ctx.measureText(text).width <= maxW) return text;
  let clipped = text;
  while (clipped.length > 1 && ctx.measureText(`${clipped}…`).width > maxW) {
    clipped = clipped.slice(0, -1);
  }
  return `${clipped}…`;
}

/**
 * V4：导出绘制已实现的 chart kind（与 lib/types ChartKind 的 18 种 native
 * 同词表）。词表外的 kind（如 planned violin）不绘制图形 —— 画标题条 +
 * 「暂不支持导出」一行说明（诚实降级，不生成空组件假成功）。
 */
const DRAWN_CHART_KINDS = new Set([
  'bar', 'horizontal_bar', 'grouped_bar', 'stacked_bar',
  'line', 'area', 'scatter', 'histogram', 'box_plot',
  'pie', 'donut', 'radar', 'rose', 'timeseries', 'cumulative',
  'heat_matrix', 'kpi_card', 'ranking_list',
]);
/** 需要底部类目轴带的 kind（直角坐标系；极坐标/卡片/矩阵族无轴带）。 */
const CHART_AXIS_KINDS = new Set([
  'bar', 'line', 'grouped_bar', 'stacked_bar', 'scatter',
  'histogram', 'box_plot', 'timeseries', 'area', 'cumulative',
]);

/** 静态图表（bar/line/pie/scatter 的确定性 canvas 绘制）。 */
export function drawChromeChartPanel(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.chart || el.chart.data.length === 0) return;
  const { ctx } = d;
  const chart = el.chart;
  // E-2 对称（§13 collapsed parity）：collapsed 面板只画标题条 —— 与
  // live FloatingChrome 折叠态同语义，不展开用户折叠的面板。
  if (el.text !== undefined) {
    const collapsedH = d.scalePx(36);
    const collapsedW = el.rect?.width ?? d.scalePx(300);
    const collapsedOrigin = el.rect
      ? { x: el.rect.x, y: el.rect.y }
      : (() => {
          const o = anchorOrigin(el.anchor, {
            targetW: d.targetW, targetH: d.targetH,
            marginX: opts.marginX, marginY: opts.marginY ?? 90,
          });
          return {
            x: o.align === 'right' ? o.x - collapsedW : o.align === 'center' ? o.x - collapsedW / 2 : o.x,
            y: o.vAlign === 'bottom' ? d.targetH - o.y - collapsedH : o.y,
          };
        })();
    _chromePanel(d, collapsedOrigin.x, collapsedOrigin.y, collapsedW, collapsedH);
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    _text(d, el.text || chart.title, collapsedOrigin.x + d.scalePx(12), collapsedOrigin.y + d.scalePx(22), 'left');
    return;
  }
  const padding = d.scalePx(12);
  const titleH = d.scalePx(24);
  // V4：轴带按 kind 判定（直角坐标系才留底部类目轴带；此前仅 pie 特判 0）
  const axisH = CHART_AXIS_KINDS.has(chart.type) ? d.scalePx(26) : 0;
  const boxW = el.rect?.width ?? d.scalePx(300);
  const boxH = el.rect?.height ?? d.scalePx(220);

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  _text(d, chart.title, lx + padding, ly + padding + d.scalePx(12), 'left');

  const plotX = lx + padding;
  const plotY = ly + padding + titleH;
  const plotW = boxW - padding * 2;
  const plotH = boxH - padding * 2 - titleH - axisH;
  if (plotW <= 0 || plotH <= 0) return;

  const accent = d.style.accentColor || '#3182bd';
  const gridColor = d.darkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';
  const labelColor = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.9)';
  const CHART_COLORS = ['#3182bd', '#e6550d', '#31a354', '#756bb1', '#e41a1c', '#ffd92f'];

  // V4：词表外 kind（如 planned violin）诚实降级 —— 标题条已画，正文只
  // 放一行说明（不绘制图形、不生成空组件假成功）。
  // 空数据披露同标准（rose/radar/donut 等数据不足时不画空绘图区）。
  const drawEmptyDisclosure = (reason: string) => {
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    _text(d, reason, plotX + plotW / 2, plotY + plotH / 2, 'center');
  };
  if (!DRAWN_CHART_KINDS.has(chart.type)) {
    drawEmptyDisclosure('该图表类型暂不支持导出');
    return;
  }

  // V4：多序列类目的序列图例（色签 + 名，绘图区右上角内联）。
  const drawSeriesLegend = (names: string[], y: number) => {
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    const items = names.map((nm) => _clipText(ctx, nm, d.scalePx(64)));
    let totalW = 0;
    for (const label of items) totalW += d.scalePx(12) + ctx.measureText(label).width + d.scalePx(8);
    let x = plotX + plotW - totalW;
    items.forEach((label, si) => {
      ctx.fillStyle = CHART_COLORS[si % CHART_COLORS.length];
      ctx.fillRect(x, y - d.scalePx(7), d.scalePx(8), d.scalePx(8));
      ctx.fillStyle = labelColor;
      _text(d, label, x + d.scalePx(10), y, 'left');
      x += d.scalePx(12) + ctx.measureText(label).width + d.scalePx(8);
    });
  };
  // 直角坐标 x 轴标签（首/中/尾，避免重叠 —— 与既有 bar/line 同式）。
  const drawXLabels = (labelAt: (i: number) => string, n: number) => {
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    if (n > 0) _text(d, labelAt(0), plotX, plotY + plotH + d.scalePx(14), 'left');
    if (n > 2) _text(d, labelAt(Math.floor(n / 2)), plotX + plotW / 2, plotY + plotH + d.scalePx(14), 'center');
    if (n > 1) _text(d, labelAt(n - 1), plotX + plotW, plotY + plotH + d.scalePx(14), 'right');
  };
  // 水平网格（3 段 —— 与既有 bar/line 同式）。
  const drawGrid = () => {
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    for (let g = 0; g <= 3; g++) {
      const gy = plotY + (g / 3) * plotH;
      ctx.beginPath();
      ctx.moveTo(plotX, gy);
      ctx.lineTo(plotX + plotW, gy);
      ctx.stroke();
    }
  };

  if (chart.type === 'pie') {
    const total = chart.data.reduce((s, p) => s + (p.value ?? 0), 0);
    if (total <= 0) return;
    const cx = plotX + plotW / 2;
    const cy = plotY + plotH / 2;
    const r = Math.min(plotW, plotH) / 2;
    let angle = -Math.PI / 2;
    chart.data.forEach((p, i) => {
      const frac = (p.value ?? 0) / total;
      if (frac <= 0) return;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, angle, angle + frac * 2 * Math.PI);
      ctx.closePath();
      ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.fill();
      angle += frac * 2 * Math.PI;
    });
    return;
  }

  if (chart.type === 'scatter') {
    const xs = chart.data.map((p) => p.x ?? 0);
    const ys = chart.data.map((p) => p.y ?? 0);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(plotX, plotY, plotW, plotH);
    ctx.fillStyle = accent;
    for (const p of chart.data) {
      const px = plotX + (((p.x ?? 0) - minX) / spanX) * plotW;
      const py = plotY + plotH - (((p.y ?? 0) - minY) / spanY) * plotH;
      ctx.beginPath();
      ctx.arc(px, py, d.scalePx(3), 0, 2 * Math.PI);
      ctx.fill();
    }
  } else if (chart.type === 'horizontal_bar') {
    // 横向条：类目 y 轴（自上而下），条长 ∝ 值（0 基线正则化）
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    const n = chart.data.length;
    const gutterW = Math.min(plotW * 0.35, d.scalePx(72));
    const barX = plotX + gutterW;
    const barWMax = Math.max(plotW - gutterW, 0);
    const rowH = plotH / Math.max(n, 1);
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(barX, plotY, barWMax, plotH);
    chart.data.forEach((p, i) => {
      const v = p.value ?? 0;
      const w = maxV > 0 ? (Math.max(v, 0) / maxV) * barWMax : 0;
      ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.fillRect(barX, plotY + i * rowH + rowH * 0.2, w, rowH * 0.6);
    });
    // 类目标签：≤12 行逐行（槽宽截断），更多行只画首/中/尾
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    const labelRow = (i: number) => {
      const label = _clipText(ctx, chart.data[i]?.name ?? '', gutterW - d.scalePx(4));
      _text(d, label, plotX, plotY + i * rowH + rowH * 0.5 + d.scalePx(3), 'left');
    };
    if (n <= 12) {
      for (let i = 0; i < n; i++) labelRow(i);
    } else if (n > 0) {
      labelRow(0);
      if (n > 2) labelRow(Math.floor(n / 2));
      labelRow(n - 1);
    }
  } else if (chart.type === 'grouped_bar' || chart.type === 'stacked_bar') {
    // 多序列 tidy 行按类目聚合 {name, [seriesName]: value}；grouped 并排、
    // stacked 同列累加 y（缺 series 时 data 即单序列）。
    const seriesList = chart.series && chart.series.length
      ? chart.series
      : [{ name: 'value', data: chart.data }];
    const cats: string[] = [];
    const catIdx = new Map<string, number>();
    for (const s of seriesList) {
      for (const p of s.data) {
        if (!catIdx.has(p.name)) {
          catIdx.set(p.name, cats.length);
          cats.push(p.name);
        }
      }
    }
    const m = seriesList.length;
    const grid = seriesList.map((s) => {
      const arr = new Array<number>(cats.length).fill(0);
      for (const p of s.data) {
        const ci = catIdx.get(p.name);
        if (ci !== undefined) arr[ci] = p.value ?? 0;
      }
      return arr;
    });
    const maxV = Math.max(0, ...grid.flat()) || 1;
    drawGrid();
    const n = cats.length;
    const slotW = plotW / Math.max(n, 1);
    if (chart.type === 'stacked_bar') {
      for (let ci = 0; ci < n; ci++) {
        let acc = 0;
        for (let si = 0; si < m; si++) {
          const v = grid[si][ci];
          if (v <= 0) continue;
          const h = (v / maxV) * plotH;
          ctx.fillStyle = CHART_COLORS[si % CHART_COLORS.length];
          ctx.fillRect(plotX + ci * slotW + slotW * 0.2, plotY + plotH - ((acc + v) / maxV) * plotH, slotW * 0.6, h);
          acc += v;
        }
      }
    } else {
      const bw = (slotW / m) * 0.7;
      for (let ci = 0; ci < n; ci++) {
        for (let si = 0; si < m; si++) {
          const h = (Math.max(grid[si][ci], 0) / maxV) * plotH;
          ctx.fillStyle = CHART_COLORS[si % CHART_COLORS.length];
          ctx.fillRect(plotX + ci * slotW + (slotW - bw * m) / 2 + si * bw, plotY + plotH - h, bw, h);
        }
      }
    }
    drawSeriesLegend(seriesList.map((s) => s.name), plotY + d.scalePx(8));
    drawXLabels((i) => cats[i] ?? '', n);
  } else if (chart.type === 'timeseries' || chart.type === 'area' || chart.type === 'cumulative') {
    // timeseries 同 line；area / cumulative 折线 + 半透明填充
    //（cumulative 数据已由上游衍生 —— live toCumulative 同约定，此处不再累加）
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    const minV = Math.min(...values, 0);
    const span = maxV - minV || 1;
    drawGrid();
    const n = chart.data.length;
    const pxAt = (i: number) => plotX + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const pyAt = (i: number) => plotY + plotH - (((chart.data[i]?.value ?? 0) - minV) / span) * plotH;
    if (chart.type !== 'timeseries') {
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        if (i === 0) ctx.moveTo(pxAt(0), pyAt(0));
        else ctx.lineTo(pxAt(i), pyAt(i));
      }
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.closePath();
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.18;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    ctx.strokeStyle = accent;
    ctx.lineWidth = d.scalePx(2);
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      if (i === 0) ctx.moveTo(pxAt(0), pyAt(0));
      else ctx.lineTo(pxAt(i), pyAt(i));
    }
    ctx.stroke();
    drawXLabels((i) => chart.data[i]?.name ?? '', n);
  } else if (chart.type === 'histogram') {
    // 同 bar，barCategoryGap 0 视觉 —— 条紧贴（slot 全宽，1px 视觉缝）。
    // 分箱计数从 0 起基线（min 基线会把最小非零 bin 画成零高度条 —— 数据失真）
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    const span = maxV || 1;
    drawGrid();
    const n = chart.data.length;
    const slotW = plotW / Math.max(n, 1);
    chart.data.forEach((p, i) => {
      const v = p.value ?? 0;
      const h = (v / span) * plotH;
      ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.fillRect(plotX + i * slotW + d.scalePx(0.5), plotY + plotH - h, Math.max(d.scalePx(1), slotW - d.scalePx(1)), h);
    });
    drawXLabels((i) => chart.data[i]?.name ?? '', n);
  } else if (chart.type === 'donut') {
    // 饼图 + 内半径白圈（0.6 倍）—— 与 live donut innerRadius 同视觉
    const total = chart.data.reduce((s, p) => s + (p.value ?? 0), 0);
    if (total <= 0) {
      drawEmptyDisclosure('暂无数据');
    }
    if (total > 0) {
      const cx = plotX + plotW / 2;
      const cy = plotY + plotH / 2;
      const r = Math.min(plotW, plotH) / 2;
      let angle = -Math.PI / 2;
      chart.data.forEach((p, i) => {
        const frac = (p.value ?? 0) / total;
        if (frac <= 0) return;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, angle, angle + frac * 2 * Math.PI);
        ctx.closePath();
        ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
        ctx.fill();
        angle += frac * 2 * Math.PI;
      });
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.6, 0, 2 * Math.PI);
      ctx.closePath();
      ctx.fillStyle = d.darkMode ? 'rgba(0,10,20,0.82)' : 'rgba(255,255,255,0.88)';
      ctx.fill();
    }
    return;
  } else if (chart.type === 'radar') {
    // 闭合多边形（每序列一圈；轴 = 类目数，0 基线正则化）
    const seriesList = chart.series && chart.series.length
      ? chart.series
      : [{ name: 'value', data: chart.data }];
    const cats = seriesList[0]?.data.map((p) => p.name) ?? [];
    const k = cats.length;
    if (k < 3) {
      drawEmptyDisclosure('暂无数据（雷达图至少需要 3 个轴）');
    }
    if (k >= 3) {
      const values = seriesList.flatMap((s) => s.data.map((p) => p.value ?? 0));
      const maxV = Math.max(...values, 0) || 1;
      const cx = plotX + plotW / 2;
      const cy = plotY + plotH / 2;
      const R = Math.min(plotW, plotH) / 2 - d.scalePx(14);
      const angleAt = (i: number) => -Math.PI / 2 + (i / k) * 2 * Math.PI;
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = d.scalePx(0.5);
      for (let i = 0; i < k; i++) {
        const a = angleAt(i);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, 2 * Math.PI);
      ctx.stroke();
      seriesList.forEach((s, si) => {
        ctx.beginPath();
        for (let i = 0; i < k; i++) {
          const a = angleAt(i);
          const v = Math.max(s.data[i]?.value ?? 0, 0) / maxV;
          const px = cx + Math.cos(a) * R * v;
          const py = cy + Math.sin(a) * R * v;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.closePath();
        const color = CHART_COLORS[si % CHART_COLORS.length];
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.25;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = d.scalePx(1.5);
        ctx.stroke();
      });
      if (seriesList.length > 1) drawSeriesLegend(seriesList.map((s) => s.name), plotY + d.scalePx(8));
      ctx.fillStyle = labelColor;
      ctx.font = `${d.scalePx(9)}px sans-serif`;
      const labelIdx = k <= 8 ? cats.map((_, i) => i) : [0, Math.floor(k / 2), k - 1];
      for (const i of labelIdx) {
        const a = angleAt(i);
        _text(
          d,
          _clipText(ctx, cats[i], d.scalePx(48)),
          cx + Math.cos(a) * (R + d.scalePx(10)),
          cy + Math.sin(a) * (R + d.scalePx(10)) + d.scalePx(3),
          'center',
        );
      }
    }
  } else if (chart.type === 'rose') {
    // 极区扇形：角度等分（2π/n），半径 ∝ 值（0 基线正则化）
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    if (maxV <= 0) {
      drawEmptyDisclosure('rose');
    } else {
      const cx = plotX + plotW / 2;
      const cy = plotY + plotH / 2;
      const R = Math.min(plotW, plotH) / 2 - d.scalePx(6);
      const n = chart.data.length;
      const slice = (2 * Math.PI) / Math.max(n, 1);
      chart.data.forEach((p, i) => {
        const rr = (Math.max(p.value ?? 0, 0) / maxV) * R;
        if (rr <= 0) return;
        const a0 = -Math.PI / 2 + i * slice;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, rr, a0, a0 + slice);
        ctx.closePath();
        ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
        ctx.fill();
      });
    }
  } else if (chart.type === 'box_plot') {
    // 每类目：竖须（min-max，带端帽）+ 矩形（q1-q3）+ 中位线（value）
    //（value 缺位回退 live RenderBoxPlot 同式：q1/q3/min/max 逐级回退 median）
    const nums = chart.data
      .flatMap((p) => [p.min, p.q1, p.value, p.q3, p.max])
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    const lo = nums.length ? Math.min(...nums) : 0;
    const hi = nums.length ? Math.max(...nums) : 1;
    const span = hi - lo || 1;
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(plotX, plotY, plotW, plotH);
    const n = chart.data.length;
    const slotW = plotW / Math.max(n, 1);
    const yAt = (v: number) => plotY + plotH - ((v - lo) / span) * plotH;
    chart.data.forEach((p, i) => {
      const cx = plotX + i * slotW + slotW / 2;
      const bw = Math.min(d.scalePx(48), slotW * 0.55);
      const med = yAt(p.value ?? lo);
      const q1 = yAt(typeof p.q1 === 'number' ? p.q1 : p.value ?? lo);
      const q3 = yAt(typeof p.q3 === 'number' ? p.q3 : p.value ?? lo);
      const mn = typeof p.min === 'number' ? yAt(p.min) : q1;
      const mx = typeof p.max === 'number' ? yAt(p.max) : q3;
      const boxY = Math.min(q1, q3);
      const boxH = Math.max(d.scalePx(2), Math.abs(q3 - q1));
      ctx.strokeStyle = accent;
      ctx.lineWidth = d.scalePx(1);
      ctx.beginPath();
      ctx.moveTo(cx, mn); ctx.lineTo(cx, mx);
      ctx.moveTo(cx - bw / 3, mn); ctx.lineTo(cx + bw / 3, mn);
      ctx.moveTo(cx - bw / 3, mx); ctx.lineTo(cx + bw / 3, mx);
      ctx.stroke();
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.35;
      ctx.fillRect(cx - bw / 2, boxY, bw, boxH);
      ctx.globalAlpha = 1;
      ctx.strokeRect(cx - bw / 2, boxY, bw, boxH);
      ctx.lineWidth = d.scalePx(2);
      ctx.beginPath();
      ctx.moveTo(cx - bw / 2, med); ctx.lineTo(cx + bw / 2, med);
      ctx.stroke();
    });
    drawXLabels((i) => chart.data[i]?.name ?? '', n);
  } else if (chart.type === 'heat_matrix') {
    // 行列色阵（series=行、行内 data=列；无 series 单行）—— Blues 5 档
    // ramp 按 (v-lo)/(hi-lo) 取档（与 live HEAT_RAMP 同源）。
    const HEAT_RAMP = ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'];
    const rowsSrc: Array<{ name: string; data: ChartPanelDataPoint[] }> = chart.series && chart.series.length
      ? chart.series
      : [{ name: '', data: chart.data }];
    const allVals = rowsSrc.flatMap((r) => r.data.map((p) => p.value ?? 0)).filter(Number.isFinite);
    const lo = allVals.length ? Math.min(...allVals) : 0;
    const hi = allVals.length ? Math.max(...allVals) : 1;
    const span = hi - lo || 1;
    const gutterW = rowsSrc.length > 1 ? Math.min(plotW * 0.25, d.scalePx(56)) : 0;
    const gridX = plotX + gutterW;
    const gridW = plotW - gutterW;
    const cols = Math.max(rowsSrc[0]?.data.length ?? 0, 1);
    const cellW = gridW / cols;
    const cellH = plotH / Math.max(rowsSrc.length, 1);
    rowsSrc.forEach((r, ri) => {
      r.data.forEach((p, ci) => {
        const t = Math.min(1, Math.max(0, ((p.value ?? 0) - lo) / span));
        ctx.fillStyle = HEAT_RAMP[Math.min(HEAT_RAMP.length - 1, Math.floor(t * HEAT_RAMP.length))];
        ctx.fillRect(
          gridX + ci * cellW,
          plotY + ri * cellH,
          Math.max(d.scalePx(1), cellW - d.scalePx(1)),
          Math.max(d.scalePx(1), cellH - d.scalePx(1)),
        );
      });
      if (gutterW > 0) {
        ctx.fillStyle = labelColor;
        ctx.font = `${d.scalePx(9)}px sans-serif`;
        _text(d, _clipText(ctx, r.name, gutterW - d.scalePx(4)), plotX, plotY + ri * cellH + cellH / 2 + d.scalePx(3), 'left');
      }
    });
    // 列标签（首/中/尾）—— 上移进绘图区底部（heat_matrix 无轴带预算，
    // 12px 正好是面板 padding：贴边画会压到面板边框）
    const colNames = rowsSrc[0]?.data.map((p) => p.name) ?? [];
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(9)}px sans-serif`;
    const nc = colNames.length;
    const colLabelY = plotY + plotH - d.scalePx(4);
    if (nc > 0) _text(d, _clipText(ctx, colNames[0], cellW), gridX, colLabelY, 'left');
    if (nc > 2) _text(d, _clipText(ctx, colNames[Math.floor(nc / 2)], cellW), gridX + gridW / 2, colLabelY, 'center');
    if (nc > 1) _text(d, _clipText(ctx, colNames[nc - 1], cellW), gridX + gridW, colLabelY, 'right');
  } else if (chart.type === 'kpi_card') {
    // 2 列卡片网格（大数字 + 小标签，最多 4 个 —— live RenderKpiCards 同式）
    const items = chart.data.slice(0, 4);
    const rowsN = Math.max(Math.ceil(items.length / 2), 1);
    const cellW = plotW / 2;
    const cellH = plotH / rowsN;
    items.forEach((p, i) => {
      const cx = plotX + (i % 2) * cellW;
      const cy = plotY + Math.floor(i / 2) * cellH;
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = d.scalePx(0.5);
      ctx.strokeRect(cx + d.scalePx(2), cy + d.scalePx(2), cellW - d.scalePx(4), cellH - d.scalePx(4));
      ctx.fillStyle = labelColor;
      ctx.font = `${d.scalePx(10)}px sans-serif`;
      _text(d, _clipText(ctx, p.name, cellW - d.scalePx(16)), cx + d.scalePx(10), cy + cellH * 0.38, 'left');
      ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#0f172a';
      ctx.font = `bold ${d.scalePx(18)}px sans-serif`;
      _text(
        d,
        typeof p.value === 'number' ? p.value.toLocaleString('en-US') : '—',
        cx + d.scalePx(10),
        cy + cellH * 0.72,
        'left',
      );
    });
  } else if (chart.type === 'ranking_list') {
    // 值降序横条列表（最多 10 行：名次 + 名 + 比例条 + 值）
    const rowsSrc = [...chart.data]
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 10);
    const maxV = Math.max(...rowsSrc.map((r) => r.value ?? 0), 1);
    const rowH = plotH / Math.max(rowsSrc.length, 1);
    const rankW = d.scalePx(16);
    const nameW = Math.min(plotW * 0.3, d.scalePx(80));
    const valueW = d.scalePx(48);
    const fmtValue = (v: number) =>
      v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toLocaleString('en-US');
    rowsSrc.forEach((r, i) => {
      const ry = plotY + i * rowH;
      const midY = ry + rowH / 2 + d.scalePx(3);
      ctx.font = `${d.scalePx(10)}px sans-serif`;
      ctx.fillStyle = labelColor;
      _text(d, String(i + 1), plotX + rankW, midY, 'right');
      _text(d, _clipText(ctx, r.name, nameW), plotX + rankW + d.scalePx(6), midY, 'left');
      const barX = plotX + rankW + d.scalePx(6) + nameW + d.scalePx(6);
      const barWMax = Math.max(plotW - (rankW + d.scalePx(6) + nameW + d.scalePx(6)) - valueW, 0);
      const bw = (Math.max(r.value ?? 0, 0) / maxV) * barWMax;
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.8;
      ctx.fillRect(barX, ry + rowH / 2 - d.scalePx(3), bw, d.scalePx(6));
      ctx.globalAlpha = 1;
      ctx.fillStyle = labelColor;
      _text(d, fmtValue(r.value ?? 0), plotX + plotW, midY, 'right');
    });
  } else {
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    const minV = Math.min(...values, 0);
    const span = maxV - minV || 1;
    // 网格
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    for (let g = 0; g <= 3; g++) {
      const gy = plotY + (g / 3) * plotH;
      ctx.beginPath();
      ctx.moveTo(plotX, gy);
      ctx.lineTo(plotX + plotW, gy);
      ctx.stroke();
    }
    const n = chart.data.length;
    if (chart.type === 'bar') {
      const slotW = plotW / Math.max(n, 1);
      const barW = slotW * 0.6;
      chart.data.forEach((p, i) => {
        const v = p.value ?? 0;
        const h = ((v - minV) / span) * plotH;
        const bx = plotX + i * slotW + (slotW - barW) / 2;
        const by = plotY + plotH - h;
        ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
        ctx.fillRect(bx, by, barW, h);
      });
    } else {
      // line
      ctx.strokeStyle = accent;
      ctx.lineWidth = d.scalePx(2);
      ctx.beginPath();
      chart.data.forEach((p, i) => {
        const px = plotX + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
        const py = plotY + plotH - (((p.value ?? 0) - minV) / span) * plotH;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
    // x 轴标签（首/中/尾，避免重叠）
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    const labelAt = (i: number) => chart.data[i]?.name ?? '';
    if (n > 0) _text(d, labelAt(0), plotX, plotY + plotH + d.scalePx(14), 'left');
    if (n > 2) _text(d, labelAt(Math.floor(n / 2)), plotX + plotW / 2, plotY + plotH + d.scalePx(14), 'center');
    if (n > 1) _text(d, labelAt(n - 1), plotX + plotW, plotY + plotH + d.scalePx(14), 'right');
  }
  // 轴标签
  if (chart.x_label || chart.y_label) {
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    if (chart.y_label) _text(d, chart.y_label, plotX, plotY - d.scalePx(4), 'left');
    if (chart.x_label) _text(d, chart.x_label, plotX + plotW, plotY + plotH + d.scalePx(26), 'right');
  }
}

/** chrome 面板底色（圆角半透明卡）。 */
function _chromePanel(d: DrawCtx, x: number, y: number, w: number, h: number) {
  const { ctx } = d;
  ctx.fillStyle = d.darkMode ? 'rgba(0,10,20,0.82)' : 'rgba(255,255,255,0.88)';
  ctx.beginPath();
  const rad = d.scalePx(8);
  ctx.moveTo(x + rad, y);
  ctx.lineTo(x + w - rad, y);
  ctx.arcTo(x + w, y, x + w, y + rad, rad);
  ctx.lineTo(x + w, y + h - rad);
  ctx.arcTo(x + w, y + h, x + w - rad, y + h, rad);
  ctx.lineTo(x + rad, y + h);
  ctx.arcTo(x, y + h, x, y + h - rad, rad);
  ctx.lineTo(x, y + rad);
  ctx.arcTo(x, y, x + rad, y, rad);
  ctx.closePath();
  ctx.fill();
}

/** Map Border —— 全画布图框（P6：与 live map-border.tsx 三变体同语义）。 */
export function drawChromeMapBorder(
  d: DrawCtx,
  el: ExportChromeElement,
): void {
  const { ctx } = d;
  const variant = el.variant || 'minimal';
  const ink = d.darkMode ? 'rgba(255,255,255,0.85)' : 'rgba(30,41,59,0.9)';
  ctx.strokeStyle = ink;

  const inset = variant === 'minimal' ? d.scalePx(8) : d.scalePx(10);
  const w = d.targetW - inset * 2;
  const h = d.targetH - inset * 2;

  if (variant === 'report') {
    ctx.lineWidth = d.scalePx(3);
    ctx.strokeRect(inset, inset, w, h);
    ctx.lineWidth = d.scalePx(1);
    const inset2 = inset + d.scalePx(5);
    ctx.strokeRect(inset2, inset2, d.targetW - inset2 * 2, d.targetH - inset2 * 2);
    return;
  }
  if (variant === 'academic') {
    // 外框
    ctx.lineWidth = d.scalePx(2);
    ctx.strokeRect(inset, inset, w, h);
    // 内框
    ctx.lineWidth = d.scalePx(1);
    const inset2 = inset + d.scalePx(4);
    ctx.strokeRect(inset2, inset2, d.targetW - inset2 * 2, d.targetH - inset2 * 2);
    // 四角刻度（与 live 四角 tick 同位：外框角向内 12px）
    const tick = d.scalePx(12);
    ctx.lineWidth = d.scalePx(3);
    ctx.beginPath();
    // 左上/右上/左下/右下：横竖两段
    ctx.moveTo(inset, inset + tick); ctx.lineTo(inset, inset);
    ctx.lineTo(inset + tick, inset);
    ctx.moveTo(d.targetW - inset - tick, inset); ctx.lineTo(d.targetW - inset, inset);
    ctx.lineTo(d.targetW - inset, inset + tick);
    ctx.moveTo(inset, d.targetH - inset - tick); ctx.lineTo(inset, d.targetH - inset);
    ctx.lineTo(inset + tick, d.targetH - inset);
    ctx.moveTo(d.targetW - inset - tick, d.targetH - inset);
    ctx.lineTo(d.targetW - inset, d.targetH - inset);
    ctx.lineTo(d.targetW - inset, d.targetH - inset - tick);
    ctx.stroke();
    return;
  }
  // V3（ADR-0101 D6）：neatline —— 外细实线 + 内虚线（与 live map-border
  // neatline 同语义：经典内图廓）。
  if (variant === 'neatline') {
    ctx.lineWidth = d.scalePx(1);
    ctx.strokeRect(inset, inset, w, h);
    ctx.save();
    ctx.setLineDash([d.scalePx(6), d.scalePx(4)]);
    ctx.globalAlpha = 0.65;
    const inset2 = inset + d.scalePx(4);
    ctx.strokeRect(inset2, inset2, d.targetW - inset2 * 2, d.targetH - inset2 * 2);
    ctx.restore();
    return;
  }
  // minimal
  ctx.lineWidth = d.scalePx(1.5);
  ctx.strokeRect(inset, inset, w, h);
}

/** Annotation —— 文本注释卡（终审 F1：与 live annotation.tsx 同语义：左边
 * 线强调 + 弱文本；多行按 \n 分行绘制）。v2：callout（anchorCoordinate）
 * 用 boundsFromCenterZoom 推导 bounds —— 与 live 同一 geo-anchor 投影函数
 * （象限避让规则一致）；group（items）逐条绘制（带 anchor 的条目为组内
 * callout，无 anchor 条目合并为一张静态卡）。 */
export function drawChromeAnnotation(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: {
    marginX: number;
    marginY?: number;
    /** callout 投影输入（mapCenter/mapZoom + 逻辑像素 → bounds）。 */
    mapCenter?: { lat: number; lng: number };
    mapZoom?: number;
    pxPerLogical?: number;
  },
): void {
  // group 形态：带 anchor 条目逐条锚定 + 无 anchor 条目静态卡
  if (el.items && el.items.length) {
    const bounds =
      opts.mapCenter && opts.mapZoom !== undefined
        ? boundsFromCenterZoom(
            opts.mapCenter,
            opts.mapZoom,
            d.targetW / (opts.pxPerLogical ?? 1),
            d.targetH / (opts.pxPerLogical ?? 1),
          )
        : null;
    const plain = el.items.filter((it) => !it.anchor);
    if (bounds) {
      for (const item of el.items) {
        if (item.anchor) drawAnchoredCalloutBox(d, item.anchor, textCanvasLines(item.text), bounds);
      }
    }
    if (plain.length) {
      const lines = plain.flatMap((it) => textCanvasLines(it.text)).slice(0, 8);
      drawStaticAnnotationBox(d, el, lines, opts);
    }
    return;
  }

  const lines = el.text ? el.text.split('\n').slice(0, 8) : [];
  if (!lines.length) return;
  // callout：anchor + 可推导 bounds → 地理锚定（否则降级静态卡 —— 与
  // live 的 bounds 缺席降级同语义，不虚构位置）
  if (el.anchorCoordinate && opts.mapCenter && opts.mapZoom !== undefined) {
    const bounds = boundsFromCenterZoom(
      opts.mapCenter,
      opts.mapZoom,
      d.targetW / (opts.pxPerLogical ?? 1),
      d.targetH / (opts.pxPerLogical ?? 1),
    );
    if (bounds) {
      drawAnchoredCalloutBox(d, el.anchorCoordinate, lines.map((l) => l.slice(0, 80)), bounds);
      return;
    }
  }
  drawStaticAnnotationBox(d, el, lines.map((l) => l.slice(0, 80)), opts);
}

function textCanvasLines(text: string): string[] {
  return text.split('\n').slice(0, 8).map((l) => l.slice(0, 80));
}

function drawStaticAnnotationBox(
  d: DrawCtx,
  el: ExportChromeElement,
  lines: string[],
  opts: { marginX: number; marginY?: number },
): void {
  const padding = d.scalePx(10);
  const lineH = d.scalePx(16);
  const boxW = Math.min(d.scalePx(360), d.targetW * 0.5);
  const boxH = padding * 2 + lineH * lines.length;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const x = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, x, y, boxW, boxH);
  // 左边线强调（live border-l-2 同语义）
  d.ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : 'rgba(30,41,59,0.85)';
  d.ctx.fillRect(x, y, d.scalePx(2), boxH);
  d.ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.65)' : 'rgba(100,116,139,0.95)';
  d.ctx.font = `${d.scalePx(12)}px ${d.style.fontFamily}`;
  lines.forEach((line, i) => {
    _text(d, line, x + padding + d.scalePx(2), y + padding + lineH * (i + 0.75), 'left');
  });
}

/**
 * callout 锚定盒（画布像素版 live AnchoredCallout）：anchor → bounds 比例
 * → 画布坐标；象限避让规则与 live 一致（右半向左偏、上半向下偏）。
 */
function drawAnchoredCalloutBox(
  d: DrawCtx,
  anchor: [number, number],
  lines: string[],
  bounds: { west: number; south: number; east: number; north: number },
): void {
  const frac = anchorFractionInBounds(anchor, bounds);
  if (!frac) return;
  const { ctx } = d;
  const fx = Math.min(1, Math.max(0, frac.fx));
  const fy = Math.min(1, Math.max(0, frac.fy));
  const ax = fx * d.targetW;
  const ay = (1 - fy) * d.targetH;
  const flipX = fx > 0.6;
  const flipY = fy < 0.4;

  const padding = d.scalePx(10);
  const lineH = d.scalePx(16);
  const boxW = Math.min(d.scalePx(180), d.targetW * 0.35);
  const boxH = padding * 2 + lineH * lines.length;
  const bx = flipX ? ax - boxW - d.scalePx(26) : ax + d.scalePx(26);
  const by = flipY ? ay - boxH - d.scalePx(10) : ay + d.scalePx(10);

  // 引线（anchor → 卡片方向；同一象限规则）+ 锚点
  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.65)' : 'rgba(30,41,59,0.65)';
  ctx.lineWidth = d.scalePx(1.25);
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(flipX ? bx + boxW : bx, by + (flipY ? boxH : 0));
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(ax, ay, d.scalePx(3), 0, 2 * Math.PI);
  ctx.fillStyle = '#e11d48';
  ctx.fill();

  _chromePanel(d, bx, by, boxW, boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : 'rgba(30,41,59,0.85)';
  ctx.fillRect(bx, by, d.scalePx(2), boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.65)' : 'rgba(100,116,139,0.95)';
  ctx.font = `${d.scalePx(12)}px ${d.style.fontFamily}`;
  lines.forEach((line, i) => {
    _text(d, line, bx + padding + d.scalePx(2), by + padding + lineH * (i + 0.75), 'left');
  });
}

/**
 * v2：区位插图（画布版 live inset-map 渲染器 —— 同一 fit/投影语义：
 * 等比适配 + 居中、边界折线、主图范围指示框、范围框 + 经纬十字示意）。
 * insetBbox 缺省不绘制（不虚构范围 —— 与 live 自弃同门）。
 */
export function drawChromeInset(
  d: DrawCtx,
  el: ExportChromeElement,
  opts?: { marginX?: number; marginY?: number },
): void {
  const bbox = el.insetBbox;
  if (!bbox) return;
  const { ctx } = d;
  const boxW = d.scalePx(176);
  const boxH = d.scalePx(156);
  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, {
        targetW: d.targetW, targetH: d.targetH,
        marginX: opts?.marginX ?? d.scalePx(12),
        marginY: opts?.marginY ?? d.scalePx(12),
      });
  const x = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  // 面板底 + 标题
  _chromePanel(d, x, y, boxW, boxH);
  const label = el.text || (el.variant === 'location' ? '区位' : '概览');
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(10)}px ${d.style.fontFamily}`;
  _text(d, label.slice(0, 24), x + d.scalePx(8), y + d.scalePx(14), 'left');

  // 地理绘制区（等比适配）
  const innerX = x + d.scalePx(8);
  const innerY = y + d.scalePx(20);
  const innerW = boxW - d.scalePx(16);
  const innerH = boxH - d.scalePx(28);
  const boundary = el.insetBoundary;
  let west = bbox.west, south = bbox.south, east = bbox.east, north = bbox.north;
  if (boundary) {
    for (const [lng, lat] of boundary) {
      west = Math.min(west, lng); east = Math.max(east, lng);
      south = Math.min(south, lat); north = Math.max(north, lat);
    }
  }
  const spanLng = east - west;
  const spanLat = north - south;
  if (!(spanLng > 0) || !(spanLat > 0)) return;
  const scale = Math.min(innerW / spanLng, innerH / spanLat);
  const drawW = spanLng * scale;
  const drawH = spanLat * scale;
  const offX = innerX + (innerW - drawW) / 2;
  const offY = innerY + (innerH - drawH) / 2;
  const project = (lng: number, lat: number): [number, number] => [
    offX + (lng - west) * scale,
    offY + (north - lat) * scale,
  ];

  ctx.save();
  ctx.beginPath();
  ctx.rect(innerX, innerY, innerW, innerH);
  ctx.clip();
  // 背景示意
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.06)' : 'rgba(30,41,59,0.05)';
  ctx.fillRect(offX, offY, drawW, drawH);
  // 边界折线
  if (boundary && boundary.length >= 3) {
    ctx.beginPath();
    boundary.forEach(([lng, lat], i) => {
      const [px, py] = project(lng, lat);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.closePath();
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.12)' : 'rgba(30,41,59,0.12)';
    ctx.fill();
    ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.5)' : 'rgba(30,41,59,0.5)';
    ctx.lineWidth = d.scalePx(1);
    ctx.stroke();
  }
  // 经纬十字（范围中点示意）
  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.25)' : 'rgba(30,41,59,0.25)';
  ctx.lineWidth = d.scalePx(0.5);
  ctx.setLineDash([d.scalePx(3), d.scalePx(3)]);
  const mid = project((west + east) / 2, (south + north) / 2);
  ctx.beginPath();
  ctx.moveTo(offX, mid[1]); ctx.lineTo(offX + drawW, mid[1]);
  ctx.moveTo(mid[0], offY); ctx.lineTo(mid[0], offY + drawH);
  ctx.stroke();
  ctx.setLineDash([]);
  // 主图范围指示框（相交裁剪）
  const main = el.insetMainBbox;
  if (main) {
    const cE = Math.min(Math.max(main.east, west), east);
    const cW = Math.min(Math.max(main.west, west), east);
    const cN = Math.min(Math.max(main.north, south), north);
    const cS = Math.min(Math.max(main.south, south), north);
    const [ix1, iy1] = project(cW, cN);
    const [ix2, iy2] = project(cE, cS);
    if (Math.abs(ix2 - ix1) > 2 && Math.abs(iy2 - iy1) > 2) {
      ctx.fillStyle = 'rgba(225,29,72,0.14)';
      ctx.fillRect(Math.min(ix1, ix2), Math.min(iy1, iy2), Math.abs(ix2 - ix1), Math.abs(iy2 - iy1));
      ctx.strokeStyle = '#e11d48';
      ctx.lineWidth = d.scalePx(1.5);
      ctx.strokeRect(Math.min(ix1, ix2), Math.min(iy1, iy2), Math.abs(ix2 - ix1), Math.abs(iy2 - iy1));
    }
  }
  ctx.restore();
  // 范围框
  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.35)' : 'rgba(30,41,59,0.35)';
  ctx.lineWidth = d.scalePx(1);
  ctx.strokeRect(offX, offY, drawW, drawH);
}
