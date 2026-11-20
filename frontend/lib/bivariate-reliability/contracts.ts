/**
 * G41 双变量与可靠性作者工作台契约（镜像后端有界回执子集）。
 *
 * 纪律：UI 不重算分类/矩阵/误差映射，只呈现服务端 canonical 图例、统计口径
 * 与逐字段错误。任何设置改变都会使旧 preview 的 candidate_digest 失效。
 */

export type G41Mode = 'bivariate_matrix' | 'reliability_dual_channel' | 'revert';
export type AxisMethod = 'quantiles' | 'equal_interval' | 'manual';
export type BoundaryRule = 'upper_inclusive' | 'legacy_lower_inclusive';
export type MeasureKind = 'standard_error' | 'variance' | 'confidence_interval' | 'prediction_interval';
export type VisualChannel = 'opacity' | 'stroke_width';

export const G41_MODES: Array<{ id: G41Mode; labelKey: string }> = [
  { id: 'bivariate_matrix', labelKey: 'bivariateReliability.modes.bivariate' },
  { id: 'reliability_dual_channel', labelKey: 'bivariateReliability.modes.reliability' },
  { id: 'revert', labelKey: 'bivariateReliability.modes.revert' },
];

export const AXIS_METHODS = ['quantiles', 'equal_interval', 'manual'] as const;
export const BOUNDARY_RULES = ['upper_inclusive', 'legacy_lower_inclusive'] as const;
export const MEASURE_KINDS = [
  'standard_error', 'variance', 'confidence_interval', 'prediction_interval',
] as const;
export const VISUAL_CHANNELS = ['opacity', 'stroke_width'] as const;
export const MATRIX_IDS = ['BiPurpleOrange', 'BiTealRose'] as const;

export interface FieldProfile {
  name: string;
  numeric_count: number;
  nonnumeric_count: number;
  type: string;
  kind_counts: Record<string, number>;
}

export interface SourceAnchor {
  ref_id: string | null;
  content_revision: number;
  content_fingerprint: string | null;
  feature_count?: number;
  algorithm_version?: string;
}

export interface InspectReceipt {
  layer_id: string;
  layer_type: string;
  source: {
    source_id: string;
    base_ref: string;
    content_revision: number;
    content_fingerprint: string;
    feature_count: number;
    snapshot_guarantee: string;
  };
  source_anchor: SourceAnchor;
  current: {
    legend_type: string | null;
    fingerprint: string | null;
    authoring: Record<string, unknown> | null;
    source_has_biv_class: boolean;
  };
  fields: FieldProfile[];
  fields_truncated: boolean;
  supported: {
    layer_type: string;
    bivariate_matrix: boolean;
    reliability_opacity: boolean;
    reliability_stroke_width: boolean;
    note: string;
  };
  measure_evidence_contract: {
    accepted: string[];
    requires: string;
    not_accepted: string[];
    disclosure: string;
  };
  revision: number;
  shared_expression_scope: Record<string, unknown> | null;
  notes?: string[];
  algorithm_version: string;
}

export interface AxisSpecBody {
  field: string;
  label?: string;
  unit?: string;
  measurement_note?: string;
  method: AxisMethod;
  breaks?: number[];
}

export interface BivariateBody {
  n: 2 | 3;
  axis_a: AxisSpecBody;
  axis_b: AxisSpecBody;
  matrix_id?: string;
  colors?: string[];
  boundary_rule?: BoundaryRule;
  swap_axes?: boolean;
  flip_rows?: boolean;
  missing_label?: string;
  missing_color?: string;
  scatter_sample_size?: number;
  histogram_bins?: number;
}

export interface MeasureBody {
  kind: MeasureKind;
  value_field?: string;
  low_field?: string;
  high_field?: string;
  level?: number;
  unit?: string;
  source?: 'author_declared' | 'analysis_evidence';
  evidence_ref?: string;
  method_note?: string;
  allow_noncentered?: boolean;
  require_interval_contains_estimate?: boolean;
}

export interface VisualBody {
  channel: VisualChannel;
  direction?: 'higher_less_reliable' | 'higher_more_reliable';
  min_value: number;
  max_value: number;
  min_output: number;
  max_output: number;
  unknown_output: number;
  buckets: number;
  clip: 'clamp' | 'reject';
}

export interface ReliabilityBody {
  estimate_field: string;
  estimate_label?: string;
  estimate_unit?: string;
  estimate_method?: 'quantiles' | 'equal_interval';
  estimate_k?: number;
  estimate_palette?: string;
  measure: MeasureBody;
  visual: VisualBody;
  boundary_rule?: BoundaryRule;
  missing_label?: string;
}

export interface PlanBody {
  schema_version?: 1;
  layer_id?: string;
  mode: G41Mode;
  bivariate?: BivariateBody;
  reliability?: ReliabilityBody;
  source_anchor?: {
    ref_id: string;
    content_revision: number;
    content_fingerprint?: string;
  };
  title?: string;
}

export interface LegendCell {
  screen_row: number;
  screen_col: number;
  row: number;
  col: number;
  class_index: number;
  color: string;
  count: number;
  empty: boolean;
  row_interval: { label: string; unit?: string | null } | null;
  col_interval: { label: string; unit?: string | null } | null;
}

export interface LegendBucket {
  key: string;
  index?: number;
  label: string;
  output: number;
  count: number;
}

export interface PreviewLegend {
  type: string;
  schema_version?: string;
  title?: string;
  n?: number;
  boundary_rule?: BoundaryRule;
  row_major?: boolean;
  class_field?: string;
  colors?: string[];
  cells?: LegendCell[];
  joint?: number[][];
  axes?: {
    row: { field: string; label: string; unit: string | null; method: string; arrow: string; actual_classes: number; intervals: Array<{ label: string }> };
    col: { field: string; label: string; unit: string | null; method: string; arrow: string; actual_classes: number; intervals: Array<{ label: string }> };
  };
  measure?: Record<string, unknown>;
  channel?: Record<string, unknown>;
  buckets?: LegendBucket[];
  estimate?: Record<string, unknown>;
  counts?: Record<string, unknown>;
  missing?: { count?: number; label?: string; policy?: string };
  source?: Record<string, unknown>;
  approximate?: boolean;
}

export interface PreviewReceipt {
  success: boolean;
  layer_id: string;
  mode: G41Mode;
  committed: boolean;
  preview_only?: boolean;
  algorithm_version: string;
  source_anchor: SourceAnchor;
  plan_digest: string;
  candidate_digest: string;
  current_fingerprint: string | null;
  candidate_fingerprint: string | null;
  legend: PreviewLegend;
  stats_basis: Record<string, unknown>;
  summary: Record<string, unknown>;
  disclosures: string[];
  derived: {
    fields: string[];
    ref: string | null;
    reversible: boolean;
    source_written_back: boolean;
    source_has_biv_class: boolean;
  };
  revision: number | null;
  restores?: { legend_type?: string; source_ref?: string };
}

export interface CommitReceipt extends PreviewReceipt {
  can_undo: boolean;
  mutations: number;
  mapspec_fingerprint: string | null;
  deduplicated?: boolean;
}

export interface G41ErrorPayload {
  code: string;
  message: string;
  field?: string;
  index?: number;
  correction_hint?: string;
  detail?: Record<string, unknown>;
}
