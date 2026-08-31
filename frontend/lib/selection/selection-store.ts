/**
 * SelectionContext — 统一跨视图选择契约（Workspace V2 / Goal D，落地
 * ADR-0088 组件库 deferred 的 map-chart 联动）。
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

export interface SelectionContext {
  source: SelectionSource;
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
    bbox?: [number, number, number, number];
    properties?: Record<string, unknown>;
  },
): void {
  if (kind === 'clear_selection') {
    clearSelection();
    return;
  }
  const context: SelectionContext = {
    source: payload.source,
    layer_id: String(payload.layer_id || '').slice(0, 128),
    artifact_ref: payload.artifact_ref ? String(payload.artifact_ref).slice(0, 128) : undefined,
    feature_id: payload.feature_id,
    selected_ids: (payload.selected_ids ?? [])
      .map((id) => String(id).slice(0, 64))
      .filter(Boolean)
      .slice(0, MAX_SELECTED_IDS),
    selected_categories: (payload.selected_categories ?? [])
      .map((c) => String(c).slice(0, 64))
      .filter(Boolean)
      .slice(0, MAX_SELECTED_CATEGORIES),
    filter_field: payload.filter_field ? String(payload.filter_field).slice(0, 64) : undefined,
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
  recordEvent('clear_selection', { source, layer_id: '', selected_ids: [], selected_categories: [], revision: ++revision });
  emit();
}

/** 测试/诊断用有界事件环快照（不驱动行为）。 */
export function selectionEvents(): SelectionEvent[] {
  return [...eventRing];
}

/** 会话切换：选择属于当前会话的交互态。 */
export function resetSelectionStore(): void {
  current = null;
  eventRing.length = 0;
  emit();
}
