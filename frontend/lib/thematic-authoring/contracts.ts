/**
 * G33 专题表达工作台契约（与后端 app/services/thematic_authoring/* 对齐）。
 *
 * 纪律：类型面只镜像服务端回执的**有界**子集；TS 孪生求值器
 * （assignClass / sizeForValue）与 Python 端逐点一致（tests 用共享 fixture
 * 双端断言），用于面板即时类计数与图例样本——绝不另立分类语义。
 */

// ─── 回执类型（有界子集） ──────────────────────────────────────────────────

export type ThematicMode =
  | 'graduated_auto'
  | 'graduated_manual'
  | 'categorical'
  | 'continuous'
  | 'divergent'
  | 'revert_auto';

export interface StatsBasis {
  mode: 'full_scan' | 'sampled' | 'degraded';
  total_features?: number;
  scanned_values?: number;
  sample_size?: number;
  approximate?: boolean;
  reason?: string;
  stats_version?: number;
}

export interface NumericStats {
  valid?: number;
  invalid?: Record<string, number>;
  invalid_total?: number;
  zero?: number;
  min?: number | null;
  max?: number | null;
  histogram?: { bin_edges: number[]; counts: number[] };
}

export interface TopCategory {
  key: string;
  count: number;
  key_type?: string;
}

export interface CategoricalStats {
  distinct?: number | null;
  distinct_capped?: boolean;
  top?: TopCategory[];
  other_count?: number;
  ambiguous_keys?: string[];
}

export interface Distribution {
  field?: string | null;
  basis: StatsBasis;
  numeric: NumericStats;
  categorical?: CategoricalStats;
  disclosure?: string;
}

export interface ClassRow {
  index: number;
  color?: string | null;
  label?: string | null;
  count?: number | null;
  range?: Array<number | null>;
  key?: string;
  key_type?: string;
}

export interface LegendSpecLike {
  type: string;
  field?: string;
  breaks?: number[];
  palette?: string;
  palette_colors?: string[];
  labels?: string[];
  method?: string;
  k?: number;
  categories?: Array<{ key: string; color: string; label?: string; key_type?: string }>;
  min?: number;
  max?: number;
  center?: number;
  nodata?: { color: string; label: string } | null;
  unit?: string | null;
  [k: string]: unknown;
}

export interface InspectReceipt {
  layer_id: string;
  layer_type: string;
  source: {
    source_id: string;
    ref_id: string | null;
    content_revision: number;
    data_fingerprint?: string | null;
    feature_count: number;
  };
  current: {
    legend_spec: LegendSpecLike | null;
    fingerprint: string | null;
    paint?: unknown;
    authoring?: Record<string, unknown> | null;
  };
  fields: Array<{ name: string; type?: string | null }>;
  distribution: Distribution;
  revision: number;
  shared_expression_scope?: Record<string, unknown> | null;
  notes?: string[];
}

export interface PlanBody {
  mode: ThematicMode;
  field?: string | null;
  method?: string | null;
  k?: number | null;
  breaks?: number[] | null;
  palette?: string | null;
  invert_palette?: boolean;
  divergent_center?: number | null;
  labels?: string[] | null;
  class_colors?: string[] | null;
  categories?: Array<{ key: string; color: string; label?: string }>;
  category_other?: { color: string; label?: string } | null;
  nodata?: { color: string; label: string } | null;
  unit?: string | null;
  title?: string | null;
  size_channel?: SizeChannelBody | null;
  source_anchor?: { ref_id: string; content_revision: number } | null;
}

export interface SizeChannelBody {
  channel: 'radius' | 'width';
  field: string;
  min_value: number;
  max_value: number;
  min_size: number;
  max_size: number;
  scaling: 'linear' | 'area_sqrt';
}

export interface PreviewReceipt {
  layer_id: string;
  field: string;
  mode: ThematicMode;
  committed: boolean;
  legend_spec: LegendSpecLike;
  paint?: unknown;
  class_table: ClassRow[];
  disclosures: string[];
  requested_k: number | null;
  actual_k: number | null;
  stats_basis?: StatsBasis;
  distribution?: Distribution;
  current_fingerprint: string | null;
  candidate_fingerprint: string | null;
  source_anchor: { ref_id: string | null; content_revision: number };
  size_samples?: Array<{ anchor: string; value: number; size: number }>;
  revision?: number;
  can_undo?: boolean;
}

export interface CommitReceipt extends PreviewReceipt {
  revision: number;
  can_undo: boolean;
  mapspec_fingerprint?: string | null;
}

export interface ThematicErrorPayload {
  code: string;
  message: string;
  field?: string;
  index?: number;
  correction_hint?: string;
}

// ─── TS 孪生求值器（与 Python stats.assign_class / builder.size_for_value
//     逐点一致；双端共享 fixture 断言） ────────────────────────────────────

/** step 语义：阈值 = breaks[1..n-1]（末断点是图例上界非阈值）；v≥阈值归上类。 */
export function assignClass(value: number, breaks: number[]): number {
  let cls = 0;
  for (let i = 1; i < breaks.length - 1; i++) {
    if (value >= breaks[i]) cls += 1;
    else break;
  }
  return cls;
}

/** 尺寸函数：linear = 尺寸线性；area_sqrt = 面积线性于数值（r=√面积）。
 *  值域外钳到端点；非法输入返回 null。 */
export function sizeForValue(
  value: number,
  ch: { min_value: number; max_value: number; min_size: number; max_size: number; scaling: string },
): number | null {
  if (!Number.isFinite(value)) return null;
  if (ch.scaling === 'area_sqrt') {
    const area =
      ch.min_size ** 2 +
      ((value - ch.min_value) * (ch.max_size ** 2 - ch.min_size ** 2)) /
        (ch.max_value - ch.min_value);
    const clamped = Math.min(Math.max(area, ch.min_size ** 2), ch.max_size ** 2);
    return Math.sqrt(clamped);
  }
  const t = (value - ch.min_value) / (ch.max_value - ch.min_value);
  const clamped = Math.min(Math.max(t, 0), 1);
  return ch.min_size + clamped * (ch.max_size - ch.min_size);
}

// ─── 词表（与后端 contract.py 同步） ───────────────────────────────────────

export const GRADUATED_METHODS = [
  { id: 'quantiles', labelKey: 'thematicAuthoring.methods.quantiles' },
  { id: 'equal_interval', labelKey: 'thematicAuthoring.methods.equalInterval' },
  { id: 'natural_breaks', labelKey: 'thematicAuthoring.methods.naturalBreaks' },
  { id: 'std_dev', labelKey: 'thematicAuthoring.methods.stdDev' },
  { id: 'head_tail', labelKey: 'thematicAuthoring.methods.headTail' },
] as const;

export const PALETTES = [
  'YlOrRd', 'Blues', 'Greens', 'Reds', 'Viridis', 'Magma', 'Plasma',
  'RdBu', 'RdYlGn', 'PuOr', 'Oranges', 'Purples', 'Gray',
] as const;

export const MODES = [
  { id: 'graduated_auto', labelKey: 'thematicAuthoring.modes.auto' },
  { id: 'graduated_manual', labelKey: 'thematicAuthoring.modes.manual' },
  { id: 'categorical', labelKey: 'thematicAuthoring.modes.categorical' },
  { id: 'continuous', labelKey: 'thematicAuthoring.modes.continuous' },
  { id: 'divergent', labelKey: 'thematicAuthoring.modes.divergent' },
  { id: 'revert_auto', labelKey: 'thematicAuthoring.modes.revert' },
] as const;
