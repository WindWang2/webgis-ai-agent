/**
 * Data Join Authoring API —— G38 数据关联工作台 client。
 *
 * 与后端 /api/v1/data-join-authoring 端点族一一对应。诚实纪律：
 * total_rows 是全量行数（非当前页）；诊断计数来自全量扫描（basis 披露）；
 * 错误统一携带后端 typed error_code。
 */

import { apiFetch } from './transport';

const BASE = '/data-join-authoring';

export type JoinMode = 'attribute' | 'spatial_point_in_polygon';
export type KeepSide = 'left' | 'inner';
export type OutputCardinality = 'one_to_one' | 'many_to_one' | 'one_to_many';
export type MultiMatchPolicy =
  | 'require_unique'
  | 'expand_all'
  | 'pick_by_field'
  | 'aggregate';
export type SpatialPredicate = 'within' | 'within_or_boundary';

export interface JoinSourceRefInput {
  kind: 'session_ref' | 'fabric_parquet' | 'project_artifact' | 'table_rows';
  session_id?: string;
  ref?: string;
  project_id?: string;
  artifact_id?: string;
  revision_no?: number;
  table_ref?: string;
}

export interface KeyFieldInput {
  left_field: string;
  right_field: string;
  transform?: 'trim' | 'casefold' | 'zfill' | null;
  zfill_width?: number;
}

export interface FieldMapEntry {
  side: 'left' | 'right';
  field: string;
  alias?: string | null;
}

export interface AggregateInput {
  function: 'count' | 'sum' | 'mean' | 'min' | 'max';
  right_field?: string | null;
  alias?: string | null;
}

export interface SpatialOptionsInput {
  predicate: SpatialPredicate;
  multi_match: MultiMatchPolicy;
  pick_field?: string | null;
  pick_direction?: 'min' | 'max' | null;
  invalid_geometry_policy?: 'exclude_count' | 'block';
}

export interface JoinPlanInput {
  title?: string;
  mode: JoinMode;
  left: JoinSourceRefInput;
  right: JoinSourceRefInput;
  keys?: KeyFieldInput[];
  keep_side?: KeepSide;
  output_cardinality?: OutputCardinality;
  null_policy?: 'never_match';
  spatial?: SpatialOptionsInput | null;
  aggregates?: AggregateInput[];
  field_map?: FieldMapEntry[];
  budgets?: Record<string, number>;
  output_name?: string;
}

export interface SourceDescribeView {
  session_id: string;
  source: {
    kind: string;
    label: string;
    is_table: boolean;
    row_count: number;
    content_fingerprint: string;
    crs: string | null;
    fields: Array<{ name: string; inferred_type: string; sample_values?: string[] }>;
    geometry?: Record<string, unknown> | null;
  };
  row_count: number;
  has_geometry: boolean;
  sample_fields: Array<{ name: string; inferred_type: string }>;
}

export interface JoinPlanView {
  plan_id: string;
  session_id: string;
  title: string;
  status: 'draft' | 'archived';
  revision: number;
  normalized_digest: string;
  left_fingerprint: string | null;
  right_fingerprint: string | null;
  preview: Record<string, unknown> | null;
  preview_digest: string | null;
  plan: JoinPlanInput;
  created_at: string | null;
}

export interface JoinDiagnostics {
  basis: 'full' | 'sampled';
  mode: JoinMode;
  left_rows: number;
  right_rows: number;
  left_null_keys: number;
  right_null_keys: number;
  left_distinct_keys: number;
  right_distinct_keys: number;
  left_duplicate_keys: number;
  right_duplicate_keys: number;
  candidate_pairs: number;
  matched_pairs: number;
  matched_left_rows: number;
  unmatched_left_rows: number;
  unmatched_right_rows: number;
  left_null_geometry: number;
  right_invalid_geometry: number;
  output_rows: number;
  working_crs: string | null;
  transform_note: string | null;
  stage_counts: Record<string, number>;
  example_counts: Record<string, number>;
}

export interface PreviewResult {
  status: 'ready' | 'blocked';
  blocked_code?: string;
  blocked?: { error_code: string; message: string; correction_hint?: string };
  diagnostics: JoinDiagnostics;
  sample_rows?: Array<Record<string, unknown>>;
  sample_row_count?: number;
  duration_ms: number;
}

export interface JoinRunView {
  run_id: string;
  plan_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  job_id: number | null;
  error_code: string | null;
  output_ref: string | null;
  output_rows: number | null;
  receipt?: {
    output_kind: 'enriched_layer' | 'table';
    output_rows: number;
    counts: JoinDiagnostics;
    working_crs: string | null;
    duration_ms: number;
  } | null;
  content_invalidated?: boolean;
  created_at: string | null;
}

export interface ResultPageView {
  run_id: string;
  rows: Array<{ properties: Record<string, unknown>; geometry_summary: unknown }>;
  total_rows: number;
  cursor: number;
  next_cursor: number | null;
  page_size: number;
  crs: string | null;
  output_kind: 'enriched_layer' | 'table';
  output_ref?: string;
}

export function asDataJoinError(err: unknown): { code: string; message: string; hint?: string } {
  // ApiError.body = FastAPI detail（typed error_code/message/correction_hint）
  const anyErr = err as { body?: { detail?: { error_code?: string; message?: string; correction_hint?: string } } | { error_code?: string; message?: string; correction_hint?: string }; message?: string };
  const body = anyErr?.body;
  const detail = (body as { detail?: unknown } | undefined)?.detail ?? body;
  if (detail && typeof detail === 'object' && 'error_code' in detail) {
    const d = detail as { error_code: string; message?: string; correction_hint?: string };
    return { code: d.error_code, message: d.message ?? '请求失败', hint: d.correction_hint };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}

type Options = { method?: string; body?: unknown; ownerToken?: string | null };

function client(sessionId: string, ownerToken?: string | null) {
  const base = `${BASE}/sessions/${encodeURIComponent(sessionId)}`;
  const opts = (o: Options): Options => ({ ...o, ownerToken });
  return {
    describe: (source: JoinSourceRefInput) =>
      apiFetch<SourceDescribeView>(`${base}/sources/describe`,
        opts({ method: 'POST', body: { source } })),
    createPlan: (plan: JoinPlanInput) =>
      apiFetch<JoinPlanView>(`${base}/plans`, opts({ method: 'POST', body: { plan } })),
    updatePlan: (planId: string, plan: JoinPlanInput, expectedRevision: number) =>
      apiFetch<JoinPlanView>(`${base}/plans/${planId}`,
        opts({ method: 'PATCH', body: { plan, expected_revision: expectedRevision } })),
    getPlan: (planId: string) =>
      apiFetch<JoinPlanView>(`${base}/plans/${planId}`, opts({})),
    listPlans: () =>
      apiFetch<{ plans: JoinPlanView[]; count: number }>(`${base}/plans`, opts({})),
    preview: (planId: string, expectedRevision: number) =>
      apiFetch<PreviewResult>(`${base}/plans/${planId}/preview`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision } })),
    execute: (planId: string, expectedRevision: number, background = true) =>
      apiFetch<JoinRunView>(`${base}/plans/${planId}/execute`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision, background } })),
    inspectRun: (runId: string) =>
      apiFetch<JoinRunView>(`${base}/runs/${runId}`, opts({})),
    resultPage: (runId: string, cursor = 0, limit = 50) =>
      apiFetch<ResultPageView>(`${base}/runs/${runId}/results?cursor=${cursor}&limit=${limit}`, opts({})),
    cancelRun: (runId: string) =>
      apiFetch<{ run_id: string; cancel_requested?: boolean }>(`${base}/runs/${runId}/cancel`,
        opts({ method: 'POST', body: {} })),
    renderRef: (runId: string) =>
      apiFetch<{ render_ref: string; rows: number }>(`${base}/runs/${runId}/render-ref`,
        opts({ method: 'POST', body: {} })),
  };
}

export const dataJoinApi = { client };
