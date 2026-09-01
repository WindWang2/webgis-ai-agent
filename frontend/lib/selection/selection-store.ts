/**
 * SelectionContext — 统一跨视图选择契约（Workspace V2 / Goal D，落地
 * ADR-0088 组件库 deferred 的 map-chart 联动；V4 扩展 brush/filter/谓词，
 * 见 ADR-0091）。
 *
 * 单一选择上下文（不是 mapSelection/chartSelection/tableSelection 三套
 * 互不兼容的状态）：map / chart / table / legend 都发布到同一模块 store，
 * 任何视图订阅同一份派生高亮。
 *
 * 边界（与既有真相的关系）：
 * - Selection 是 **transient UI 状态**：绝不写入 MapSpec（desired product
 *   state）、绝不产生 user mutation / component patch、绝不进 LLM context
 *   （会话感知仍走既有 buildSelectedFeatureSnapshot 有界快照通道）；
 * - 有界契约：selected_ids ≤ 50、selected_categories ≤ 20、properties
 *   仅保留 ≤ 8 个标量（大要素集的坐标永不入店 —— Scenario H 约束）；
 * - 事件词表（封闭）：select | hover | brush | filter | clear_selection |
 *   extent_change；有界事件环（≤16）仅供测试/诊断，不驱动行为。
 *   extent_change 的载荷在 sibling viewport-context（空间上下文不是选择，
 *   不抢占 current；词表保留以便事件面统一观测）。
 *
 * V4 增量（Runtime V4 / §8-§11）：
 * - 谓词描述符（SelectionPredicate）：大范围框选超过 id 上限时携带**有界**
 *   谓词（bbox / field∈values）而非数万 ids —— 封闭词表，不是 SQL 引擎；
 * - id_field：发布侧声明的稳定要素 id 字段（FEATURE_ID_KEYS 解析结果），
 *   使 map 框选/表格点选能编译 per-feature id 过滤（map↔table 双向）；
 * - epoch guard：会话切换 bump epoch；异步回填发布前后核对，迟到发布不再
 *   穿透（ADR-0090 deferred「selection session safety」）。
 */

export type SelectionSource = 'map' | 'chart' | 'table' | 'legend';

export type SelectionEventKind =
  | 'select'
  | 'hover'
  | 'brush'
  | 'filter'
  | 'clear_selection'
  | 'extent_change';

export const MAX_SELECTED_IDS = 50;
export const MAX_SELECTED_CATEGORIES = 20;
export const MAX_SELECTION_PROPERTIES = 8;
export const MAX_SELECTION_EVENTS = 16;
/** 谓词 values 上限（与 selected_ids 同界 —— 谓词不引入第二套无界通道）。 */
export const MAX_PREDICATE_VALUES = 50;

/**
 * 有界选择谓词（封闭词表）：
 * - `in`  : field ∈ values（类别/行政区等离散字段）；
 * - `bbox`: 命中矩形（w,s,e,n，WGS84）。
 * 只做描述与转发，绝不在 store 内求值 —— 求值属于消费侧（且必须 cheap）。
 */
export type SelectionPredicate =
  | { kind: 'in'; field: string; values: string[] }
  | { kind: 'bbox'; bbox: [number, number, number, number] };

export interface SelectionContext {
  source: SelectionSource;
  /** 本次发布的语义（select/brush/filter…）—— 消费侧区分高亮 vs 过滤。 */
  kind: SelectionEventKind;
  /** 目标图层（地图侧高亮/过滤的锚点）。 */
  layer_id: string;
  /** 产物锚（chartRef / refId —— 图表与地图共指同一数据面的证据）。 */
  artifact_ref?: string;
  feature_id?: string | number;
  selected_ids: string[];
  selected_categories: string[];
  /**
   * 类别 ↔ 要素属性的映射字段（chart 组件的 options.selectionField 协议；
   * 在场时地图侧才能把类别选择编译为要素过滤 —— 缺席则仅状态高亮）。
   */
  filter_field?: string;
  /**
   * 稳定要素 id 字段（map 框选/表格点选发布时解析）。在场且 selected_ids
   * 非空时，地图侧可编译 id 过滤（map↔table 的要素级联动）。
   */
  id_field?: string;
  /** ids 截断时携带的有界谓词（bbox 框选超上限等场景）。 */
  predicate?: SelectionPredicate;
  /** 谓词命中总数（发布侧已知的廉价计数；仅诊断/披露，不作渲染依据）。 */
  matched_count?: number;
  bbox?: [number, number, number, number];
  /** 有界标量属性（map→chart 方向的类别推导输入；无几何）。 */
  properties?: Record<string, string | number | boolean | null>;
  revision: number;
}

export interface SelectionEvent {
  kind: SelectionEventKind;
  source: SelectionSource;
  at: number;
  context: SelectionContext | null;
}

let current: SelectionContext | null = null;
let revision = 0;
const eventRing: SelectionEvent[] = [];
const listeners = new Set<() => void>();
let generation = 0;
/** 会话世代：resetSelectionStore bump；异步发布者跨 await 核对。 */
let epoch = 0;

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeSelection(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getSelectionGeneration(): number {
  return generation;
}

export function getSelection(): SelectionContext | null {
  return current;
}

/** 当前会话世代（异步发布者在 await 前捕获、await 后核对）。 */
export function getSelectionEpoch(): number {
  return epoch;
}

/** 选中类别集合（派生高亮输入；无选择 → null）。 */
export function getSelectedCategories(layerId?: string): string[] | null {
  if (!current || !current.selected_categories.length) return null;
  if (layerId !== undefined && current.layer_id !== layerId) return null;
  return current.selected_categories;
}

/** 地图过滤投影：类别选择 + filter_field 在场 → 字段/类别（否则 null）。 */
export function getSelectionFilter(
  layerId: string,
): { field: string; categories: string[] } | null {
  if (
    !current
    || current.layer_id !== layerId
    || !current.filter_field
    || !current.selected_categories.length
  ) {
    return null;
  }
  return { field: current.filter_field, categories: current.selected_categories };
}

/**
 * 编译为 MapLibre 过滤表达式（chart→map 高亮的渲染投影）。
 *
 * 形式合法性（GIS review F16）：`in` 的 haystack 必须是**单个** literal
 * 数组 —— spread 多参（['in', ['get', f], ...cats]）会被 style-spec 拒绝，
 * setFilter 静默丢弃整个过滤（特征：地图纹丝不动、无报错）。
 */
export function getSelectionFilterExpression(layerId: string): unknown[] | null {
  const projection = getSelectionFilter(layerId);
  if (!projection) return null;
  return ['in', ['get', projection.field], ['literal', projection.categories]];
}

/**
 * id 过滤投影（map 框选/表格点选 → 地图要素级高亮）。要求发布侧携带
 * id_field + selected_ids；与类别过滤同一编译纪律（单 literal haystack）。
 * 类别过滤（filter_field）优先 —— chart 联动是既有主路径。
 */
export function getSelectionIdFilter(
  layerId: string,
): { field: string; ids: string[] } | null {
  if (
    !current
    || current.layer_id !== layerId
    || !current.id_field
    || !current.selected_ids.length
    || (current.filter_field && current.selected_categories.length)
  ) {
    return null;
  }
  return { field: current.id_field, ids: current.selected_ids };
}

export function getSelectionIdFilterExpression(layerId: string): unknown[] | null {
  const projection = getSelectionIdFilter(layerId);
  if (!projection) return null;
  // '$id' = MapLibre 顶层 feature.id（不在 properties 里，必须用 ['id'] 表达式）。
  if (projection.field === '$id') {
    return ['in', ['id'], ['literal', projection.ids]];
  }
  return ['in', ['get', projection.field], ['literal', projection.ids]];
}

function boundedProperties(
  properties: Record<string, unknown> | undefined,
): SelectionContext['properties'] {
  if (!properties) return undefined;
  const out: SelectionContext['properties'] = {};
  let count = 0;
  for (const [key, value] of Object.entries(properties)) {
    if (count >= MAX_SELECTION_PROPERTIES) break;
    if (value === null || typeof value === 'number' || typeof value === 'boolean') {
      out[String(key).slice(0, 48)] = value;
      count += 1;
    } else if (typeof value === 'string') {
      // 长文本属性截断（大字段不进 transient store / 事件环）
      out[String(key).slice(0, 48)] = value.slice(0, 64);
      count += 1;
    }
  }
  return out;
}

function boundedPredicate(
  predicate: SelectionPredicate | undefined,
): SelectionPredicate | undefined {
  if (!predicate) return undefined;
  if (predicate.kind === 'bbox') {
    const [w, s, e, n] = predicate.bbox;
    if (![w, s, e, n].every((v) => Number.isFinite(v))) return undefined;
    return { kind: 'bbox', bbox: [w, s, e, n] };
  }
  if (predicate.kind !== 'in' || typeof predicate.field !== 'string' || !predicate.field) {
    return undefined;
  }
  const values = (Array.isArray(predicate.values) ? predicate.values : [])
    .map((v) => String(v).slice(0, 64))
    .filter(Boolean)
    .slice(0, MAX_PREDICATE_VALUES);
  if (!values.length) return undefined;
  return { kind: 'in', field: predicate.field.slice(0, 64), values };
}

function recordEvent(kind: SelectionEventKind, context: SelectionContext | null): void {
  eventRing.push({ kind, source: context?.source ?? current?.source ?? 'map', at: Date.now(), context });
  if (eventRing.length > MAX_SELECTION_EVENTS) eventRing.shift();
}

export function publishSelection(
  kind: SelectionEventKind,
  payload: {
    source: SelectionSource;
    layer_id: string;
    artifact_ref?: string;
    feature_id?: string | number;
    selected_ids?: Array<string | number>;
    selected_categories?: string[];
    filter_field?: string;
    id_field?: string;
    predicate?: SelectionPredicate;
    matched_count?: number;
    bbox?: [number, number, number, number];
    properties?: Record<string, unknown>;
  },
): void {
  if (kind === 'clear_selection') {
    clearSelection();
    return;
  }
  if (kind === 'extent_change') {
    // 空间上下文不抢占选择（见 viewport-context.ts）；词面保留观测通道。
    recordEvent('extent_change', null);
    return;
  }
  const rawIds = (payload.selected_ids ?? [])
    .map((id) => String(id).slice(0, 64))
    .filter(Boolean);
  const context: SelectionContext = {
    source: payload.source,
    kind,
    layer_id: String(payload.layer_id || '').slice(0, 128),
    artifact_ref: payload.artifact_ref ? String(payload.artifact_ref).slice(0, 128) : undefined,
    feature_id: payload.feature_id,
    selected_ids: rawIds.slice(0, MAX_SELECTED_IDS),
    selected_categories: (payload.selected_categories ?? [])
      .map((c) => String(c).slice(0, 64))
      .filter(Boolean)
      .slice(0, MAX_SELECTED_CATEGORIES),
    filter_field: payload.filter_field ? String(payload.filter_field).slice(0, 64) : undefined,
    id_field: payload.id_field ? String(payload.id_field).slice(0, 64) : undefined,
    predicate: boundedPredicate(payload.predicate),
    matched_count: Number.isFinite(payload.matched_count)
      ? Math.max(0, Math.floor(payload.matched_count as number))
      : undefined,
    bbox: payload.bbox,
    properties: boundedProperties(payload.properties),
    revision: ++revision,
  };
  current = context;
  recordEvent(kind, context);
  emit();
}

export function clearSelection(): void {
  if (current === null) return;
  const source = current.source;
  current = null;
  recordEvent('clear_selection', { source, kind: 'clear_selection', layer_id: '', selected_ids: [], selected_categories: [], revision: ++revision });
  emit();
}

/** 测试/诊断用有界事件环快照（不驱动行为）。 */
export function selectionEvents(): SelectionEvent[] {
  return [...eventRing];
}

/** 会话切换：选择属于当前会话的交互态（epoch bump 使迟到异步发布失效）。 */
export function resetSelectionStore(): void {
  current = null;
  eventRing.length = 0;
  epoch += 1;
  emit();
}
