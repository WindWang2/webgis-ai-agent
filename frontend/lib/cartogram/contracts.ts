/**
 * G47 Dorling 圆面积 cartogram — 前端合同（与后端 `cartogram_authoring` 同源）。
 *
 * 关键语义（不在前端重复裁决，只做类型与展示）：
 *  - 计划必须声明 quantity 的 field/unit/meaning/aggregation；率/密度被拒；
 *  - 布局坐标是 `diagram`（示意），不是经纬度；
 *  - `publication_ready=false` 的布局只能以诊断草稿身份应用/导出。
 */

export type QuantityMeaning = 'count' | 'amount';
export type ZeroPolicy = 'list_only' | 'reject';

export interface SourceAnchor {
  ref_id: string;
  content_revision: number;
  content_fingerprint: string;
  feature_count?: number;
  algorithm_version?: string;
  snapshot_guarantee?: string;
}

export interface DorlingPlan {
  schema_version?: 1;
  mode?: 'dorling_circle';
  quantity: {
    field: string;
    unit: string;
    meaning: QuantityMeaning;
    aggregation: 'sum';
  };
  scope?: {
    include_ids?: string[] | null;
    exclude_ids?: string[];
    exclude_reasons?: Record<string, string>;
  };
  missing_policy?: ZeroPolicy;
  zero_policy?: ZeroPolicy;
  source_crs?: string;
  projection?: { crs: string };
  area_scale?: { max_radius?: number; extent?: number };
  solver?: {
    max_iterations?: number;
    repulsion?: number;
    attraction?: number;
    damping?: number;
    tolerance?: number;
  };
  quality_gate?: {
    max_area_rel_error?: number;
    max_pair_overlap_ratio?: number;
    max_overlapping_pair_ratio?: number;
  };
  budgets?: { max_features?: number; max_seconds?: number };
  title?: string | null;
  source_anchor?: SourceAnchor | null;
}

export interface FieldProfile {
  name: string;
  numeric_count: number;
  positive_count?: number;
  zero_count?: number;
  missing_count?: number;
  nonnumeric_count?: number;
  kinds?: Record<string, number>;
  usable_as_quantity?: boolean;
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
    declared_crs?: string | null;
    snapshot_guarantee?: string;
  };
  source_anchor: SourceAnchor;
  geometry_types?: Record<string, number>;
  fields: FieldProfile[];
  fields_truncated?: boolean;
  current?: Record<string, unknown> | null;
  supported: Record<string, unknown>;
  contract?: Record<string, unknown>;
  revision: number;
  algorithm_version?: string;
}

export interface PlanReceipt {
  success: boolean;
  layer_id: string;
  mode: string;
  source_anchor: SourceAnchor;
  plan_digest: string;
  candidate_digest: string;
  quantity: {
    field: string;
    unit: string;
    counts: Record<string, number>;
    total: number;
    max_value: number;
    min_positive: number;
    zero_ids?: string[];
    missing_ids?: string[];
  };
  units_for_layout: {
    total: number;
    with_positive_value: number;
    zero: number;
    missing: number;
    excluded: number;
  };
  geometry: {
    source_crs: string;
    preparation_crs: string;
    adjacency_edges: number;
    multi_part_units?: number;
    vertex_count?: number;
    adjacency_contract?: Record<string, unknown>;
  };
  area_scale: {
    coefficient: number;
    max_radius: number;
    min_radius: number;
    reference_circles?: Array<{ value: number; radius: number }>;
    shared_coefficient?: boolean;
    per_circle_clamp?: boolean;
  };
  examples?: Array<{ feature_id: string; label?: string; value: number; radius: number }>;
  disclosures?: string[];
  revision?: number | null;
  preview_only?: boolean;
}

export interface RunReceipt {
  success: boolean;
  job_id: number;
  status: string;
  idempotent_reuse?: boolean;
  source_anchor: SourceAnchor;
  plan_digest: string;
  candidate_digest: string;
  poll?: string;
  note?: string;
}

export interface JobStatusReceipt {
  success?: boolean;
  job_id: number;
  status: string;
  progress?: number | null;
  message?: string | null;
  result_ref?: string | null;
  result_summary?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LayoutCircle {
  feature_id: string;
  label?: string;
  x: number;
  y: number;
  radius: number;
  value: number;
  anchor?: [number, number];
}

export interface QualityMetrics {
  status?: string;
  failed?: string[];
  area?: Record<string, unknown>;
  overlap?: Record<string, unknown>;
  displacement?: Record<string, unknown>;
  adjacency?: Record<string, unknown>;
  gates?: Record<string, unknown>;
  solver?: Record<string, unknown>;
}

export interface CartogramLayout {
  kind: 'cartogram_layout';
  schema_version: string;
  coordinate_space: 'diagram';
  units: string;
  is_geographic: false;
  extent: { width: number; height: number; margin?: number; uniform_scale?: boolean };
  source: SourceAnchor;
  plan_digest: string;
  candidate_digest: string;
  status: 'converged' | 'not_converged';
  publication_ready: boolean;
  circles: LayoutCircle[];
  zero_ids: string[];
  missing_ids: string[];
  excluded?: { ids: string[]; reasons: Record<string, string> };
  legend: {
    quantity: string;
    unit: string;
    title: string;
    reference_circles?: Array<{ value: number; radius: number }>;
    zero_note?: string;
    missing_note?: string;
  };
  reference?: {
    kind: string;
    coordinate_space: string;
    outlines: Array<{ feature_id: string; rings: number[][][] }>;
    truncated?: boolean;
  };
  metrics?: QualityMetrics;
  disclosures?: string[];
  layout_ref?: string;
}

export interface ApplyReceipt {
  success: boolean;
  layer_id: string;
  layout_ref: string;
  committed: boolean;
  publication_ready: boolean;
  diagnostic: boolean;
  stale_source: boolean;
  revision: number;
  mapspec_fingerprint?: string;
  can_undo?: boolean;
  mutations?: number;
  disclosures?: string[];
}

export const DEFAULT_PLAN: DorlingPlan = {
  schema_version: 1,
  mode: 'dorling_circle',
  quantity: { field: '', unit: '', meaning: 'count', aggregation: 'sum' },
  missing_policy: 'list_only',
  zero_policy: 'list_only',
  source_crs: 'EPSG:4326',
  projection: { crs: 'EPSG:3857' },
  solver: { max_iterations: 400 },
};
