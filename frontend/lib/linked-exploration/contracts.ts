/**
 * G46 联合探索前端合同 —— 与后端 app/services/linked_exploration/contract.py
 * 同构（typed identity / 闭开规则 / null 语义三处同一合同）。
 *
 * 编译器禁止 to-string 归一：1 / "1" / true 互不混同（MapLibre 严格相等，
 * 与服务端 evaluate_where._num_eq 的 bool 纪律一致）。
 */

export const MAX_CHARTS = 8;
export const MAX_CONDITIONS = 16;
export const MAX_BINS = 32;

export type ChartKind = 'category' | 'range';
export type Aggregation = 'count' | 'sum' | 'mean' | 'min' | 'max';

export interface ChartSpec {
  chartId: string;
  kind: ChartKind;
  field: string;
  aggregation: Aggregation;
  valueField?: string;
  title: string;
  bins: number;
  layerId?: string;
}

export interface CategoryCondition {
  kind: 'category';
  conditionId: string;
  chartId: string;
  field: string;
  /** typed values（原始类型；1/"1"/true 分立） */
  values: Array<string | number | boolean | null>;
  includeNull: boolean;
  /** 显示名（chips/图例；永不参与求值） */
  labels?: Record<string, string>;
}

export interface RangeCondition {
  kind: 'range';
  conditionId: string;
  chartId: string;
  field: string;
  gte?: number;
  gt?: number;
  lte?: number;
  lt?: number;
}

export type ExplorationCondition = CategoryCondition | RangeCondition;

export interface SourceAnchor {
  ref_id: string;
  content_revision: number;
  content_fingerprint?: string | null;
  feature_count?: number;
}

/** typed where AST（与后端 feature_pages 闭合算子集一致）。 */
export type FilterAst =
  | { op: 'eq' | 'ne'; field: string; value: string | number | boolean | null }
  | { op: 'in'; field: string; values: Array<string | number | boolean | null> }
  | { op: 'between'; field: string; gte: number | string; lte: number | string }
  | { op: 'gte' | 'gt' | 'lte' | 'lt'; field: string; value: number | string }
  | { op: 'and' | 'or'; terms: FilterAst[] }
  | { op: 'not'; term: FilterAst };

function mlScalar(v: string | number | boolean | null): unknown {
  if (typeof v === 'number' && Number.isInteger(v) && Math.abs(v) < 2 ** 53) return v;
  return v;
}

/** 单条件 → typed where 叶（同控件内 OR；与后端 condition_to_where 同构）。 */
export function conditionToWhere(cond: ExplorationCondition): FilterAst {
  if (cond.kind === 'category') {
    const leaves: FilterAst[] = [];
    if (cond.values.length > 0) {
      leaves.push({ op: 'in', field: cond.field, values: cond.values });
    }
    if (cond.includeNull) {
      leaves.push({ op: 'eq', field: cond.field, value: null });
    }
    if (leaves.length === 1) return leaves[0];
    return { op: 'or', terms: leaves };
  }
  const terms: FilterAst[] = [];
  if (cond.gte !== undefined) terms.push({ op: 'gte', field: cond.field, value: cond.gte });
  if (cond.gt !== undefined) terms.push({ op: 'gt', field: cond.field, value: cond.gt });
  if (cond.lte !== undefined) terms.push({ op: 'lte', field: cond.field, value: cond.lte });
  if (cond.lt !== undefined) terms.push({ op: 'lt', field: cond.field, value: cond.lt });
  return terms.length === 1 ? terms[0] : { op: 'and', terms };
}

/** 条件集合 → 组合 where：**条件之间一律 AND**（排除 excludeChartId 自身）。 */
export function compileCombinedWhere(
  conditions: ExplorationCondition[],
  excludeChartId?: string,
): FilterAst | null {
  const terms = conditions
    .filter((c) => !excludeChartId || c.chartId !== excludeChartId)
    .map(conditionToWhere);
  if (terms.length === 0) return null;
  if (terms.length === 1) return terms[0];
  return { op: 'and', terms };
}

/** typed where → MapLibre 表达式（typed identity 精确；无 to-string 归一）。 */
export function whereToMaplibre(where: FilterAst | null): unknown[] | null {
  if (!where) return null;
  switch (where.op) {
    case 'and':
    case 'or': {
      const head = where.op === 'and' ? 'all' : 'any';
      const parts = where.terms.map(whereToMaplibre).filter((p): p is unknown[] => p !== null);
      return [head, ...parts];
    }
    case 'not': {
      const inner = whereToMaplibre(where.term);
      return inner ? ['!', inner] : null;
    }
    case 'in':
      return ['in', ['get', where.field], ['literal', where.values.map(mlScalar)]];
    case 'eq':
      return ['==', ['get', where.field], mlScalar(where.value)];
    case 'ne':
      return ['!=', ['get', where.field], mlScalar(where.value)];
    case 'between':
      return [
        'all',
        ['>=', ['get', where.field], mlScalar(where.gte)],
        ['<=', ['get', where.field], mlScalar(where.lte)],
      ];
    case 'gte':
    case 'gt':
    case 'lte':
    case 'lt': {
      const sym = where.op === 'gte' ? '>=' : where.op === 'gt' ? '>' : where.op === 'lte' ? '<=' : '<';
      return [sym, ['get', where.field], mlScalar(where.value)];
    }
    default:
      return null;
  }
}

/* ─── 服务端回执类型（preview/open 等） ──────────────────────────────── */

export interface CategoryRow {
  label: string;
  count: number;
  machine_value: string | number | boolean | null;
  is_null: boolean;
  is_other: boolean;
  other_values?: Array<string | number | boolean | null>;
  other_value_count?: number;
  other_overflow?: boolean;
  other_filterable?: boolean;
  sum?: number | null;
  mean?: number | null;
  min?: number | null;
  max?: number | null;
}

export interface RangeBin {
  index: number;
  gte: number;
  lte: number;
  upper_exclusive: boolean;
  label: string;
  count: number;
}

export interface ChartStatistics {
  kind: 'category' | 'range';
  field: string;
  aggregation: string;
  rows?: CategoryRow[];
  bins?: RangeBin[];
  matched: number;
  scanned: number;
  truncated?: boolean;
  null_count?: number;
  non_numeric_count?: number;
  invalid_count?: number;
  valid_count?: number;
  denominator_note?: string;
}

export interface ChartPreview {
  chart_id: string;
  kind: ChartKind;
  field: string;
  aggregation: Aggregation;
  title: string;
  facet: { scope: string; note: string; statistics: ChartStatistics };
  full: { scope: string; note: string; statistics: ChartStatistics };
}

export interface PreviewResponse {
  anchor: SourceAnchor;
  conditions_digest: string;
  combined_where: FilterAst | null;
  match_count: number;
  match_count_exact: boolean;
  total: number;
  map_filter: unknown[] | null;
  charts: ChartPreview[];
  completeness: string;
  selection_handle?: { handle: string; count: number | null; count_exact: boolean; ttl_seconds: number };
}

export interface InspectField {
  name: string;
  type: string;
  null_count?: number | null;
  category_sample?: Array<{ value: string | number | boolean; count: number }>;
  is_sample?: boolean;
  min?: number;
  max?: number;
  min_max_is_sample?: boolean;
}

export interface InspectResponse {
  anchor: SourceAnchor;
  feature_count: number;
  fields: InspectField[];
  schema_complete: boolean;
  sample_note: string;
}

export interface ExplorationView {
  view_id: string;
  title: string;
  status: 'draft' | 'archived';
  revision: number;
  source_ref: string;
  source_revision: number;
  source_fingerprint?: string | null;
  normalized_digest: string;
  plan?: ExplorationPlanJson;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExplorationPlanJson {
  schema_version: number;
  anchor: { ref_id: string; content_revision: number; content_fingerprint?: string | null };
  charts: Array<Record<string, unknown>>;
  conditions: Array<Record<string, unknown>>;
  base_scope_note?: string;
}

export interface OpenViewResponse {
  view: ExplorationView;
  plan: ExplorationPlanJson;
  state: 'ready' | 'stale';
  preview?: Record<string, unknown>;
  stale_reason?: { code: string; message: string; current_revision?: number };
}

// ─── 多源（v3）：计划绑定 / 关系 / 指标 / 计划预览回执 ─────────────────

export interface SourceBindingJson {
  source_id: string;
  anchor: { ref_id: string; content_revision: number; content_fingerprint?: string | null };
  role: 'primary' | 'context';
  granularity_note?: string;
  hierarchy?: { parent_field: string; parent_source_id?: string | null; level_note?: string } | null;
}

export interface LinkSpecJson {
  link_id: string;
  kind: 'attribute' | 'spatial_within';
  left_source_id: string;
  right_source_id: string;
  keys?: Array<{ left_field: string; right_field: string; transform?: 'trim' | 'casefold' | 'zfill' | null; zfill_width?: number }>;
  predicate?: 'within' | 'within_or_boundary' | null;
  cardinality: 'one_to_one' | 'many_to_one' | 'one_to_many';
  propagate: 'none' | 'left_to_right' | 'right_to_left';
  note?: string;
}

export interface MetricSpecJson {
  metric_id: string;
  op: 'count' | 'distinct_count' | 'sum' | 'mean' | 'weighted_mean' | 'rate' | 'median';
  source_id?: string | null;
  value_field?: string | null;
  weight_field?: string | null;
  weight_by_area?: boolean;
  denominator_field?: string | null;
  denominator_source_id?: string | null;
  link_id?: string | null;
  group_field?: string | null;
  scale?: number | null;
  unit?: string;
  title?: string;
}

export interface MultiSourcePlanJson {
  schema_version: 2 | 3;
  anchor?: { ref_id: string; content_revision: number; content_fingerprint?: string | null };
  sources?: SourceBindingJson[];
  links?: LinkSpecJson[];
  metrics?: MetricSpecJson[];
  charts: Array<Record<string, unknown>>;
  conditions: Array<Record<string, unknown>>;
  base_scope_note?: string;
}

export interface PropagationDisclosure {
  link_id: string;
  from_source: string;
  target_fields: string[];
  key_count: number;
  from_unfiltered: boolean;
  skipped_missing?: number;
  skipped_invalid?: number;
  map_filter?: unknown[] | null;
}

export interface SourceScopeEntry {
  source_id: string;
  role: 'primary' | 'context';
  granularity_note?: string;
  anchor: { ref_id: string; content_revision: number; content_fingerprint?: string | null };
  total: number;
  match_count: number;
  match_count_exact: boolean;
  own_where: FilterAst | null;
  propagations: PropagationDisclosure[];
  map_filter: unknown[] | null;
  map_filter_projected: boolean;
  map_filter_note?: string;
  selection_handle?: { handle: string; count: number | null; count_exact: boolean; ttl_seconds: number; scope?: string };
  selection_handle_skipped?: { reason: string; note: string };
}

export interface MetricGroupResult {
  key: string | number | boolean | null;
  key_label: string;
  value: number | null;
  available: boolean;
  unavailable_reason?: string | null;
  numerator?: number | null;
  denominator?: number | null;
  count: number;
  numeric_count?: number;
  null_count?: number;
  non_numeric_count?: number;
  invalid_weight_count?: number;
}

export interface MetricResult {
  metric_id: string;
  op: MetricSpecJson['op'];
  source_id?: string | null;
  grain_source_id?: string | null;
  unit: string;
  scale: number | null;
  title: string;
  groups: MetricGroupResult[];
  truncated: boolean;
  disclosure: Record<string, unknown>;
}

export interface SelectionRefDisclosure {
  selection_ref: string;
  source_id: string;
  id_field: string;
  claimed_epoch_seq?: number | null;
  id_count: number;
  matched_count?: number | null;
  ids_truncated: boolean;
  layer_id?: string | null;
  note: string;
}

export interface PlanPreviewResponse {
  schema_version: number;
  primary_source_id: string;
  anchors: Record<string, SourceAnchor>;
  conditions_digest: string;
  plan_digest: string;
  selection_ref_resolved?: SelectionRefDisclosure;
  per_source: Record<string, SourceScopeEntry>;
  charts: Array<ChartPreview & { source_id?: string | null }>;
  metrics: MetricResult[];
  completeness: string;
}

export interface DrillResponse {
  plan: MultiSourcePlanJson;
  drill: {
    source_id: string;
    direction: 'down' | 'up';
    parent_field: string;
    parent_value?: string | number | boolean | null;
    hierarchy_note?: string;
    groups: Array<{ key: unknown; label: string; count: number | null }>;
    group_count: number;
    truncated: boolean;
    matched_in_scope?: number | null;
    denominator_note?: string | null;
  };
  preview_brief: {
    conditions_digest: string;
    per_source: Record<string, { match_count: number; total: number }>;
  };
}
