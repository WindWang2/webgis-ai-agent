/**
 * Coordinate Operations API —— G45 坐标操作工作台 client。
 *
 * 与后端 /api/v1/coord-ops 端点族一一对应。诚实纪律：候选的 unavailable
 * 项携带缺格网原因（不静默消失）；accuracy_unknown 绝不显示为 0；预览
 * 成功 ≠ 全量成功；错误统一携带后端 typed error_code。
 */

import { apiFetch } from './transport';

const BASE = '/api/v1/coord-ops';

export interface CoordOpSourceRefInput {
  kind: 'session_ref' | 'fabric_parquet' | 'project_artifact';
  session_id?: string;
  ref?: string;
  project_id?: string;
  artifact_id?: string;
  revision_no?: number;
}

export interface AoiInput {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface CoordOpPlanInput {
  title?: string;
  source: CoordOpSourceRefInput;
  target_crs: string;
  aoi?: AoiInput | null;
  aoi_derivation?: 'none' | 'explicit' | 'source_bounds_approximate';
  z_policy?: 'preserve_unconverted' | 'reject';
  empty_geometry_policy?: 'keep';
  out_of_area_policy?: 'report' | 'fail';
  project_id?: string | null;
}

export interface CrsAxisInfo {
  name: string;
  abbrev: string;
  direction: string;
  unit_name: string;
  unit_conversion_factor: number;
}

export interface CrsLookupView {
  input: string;
  identity: string | null;
  name: string;
  type_name: string;
  axis_official: CrsAxisInfo[];
  carrier_convention: string;
  dimension_supported: boolean;
  dimension_unsupported_reason: string | null;
  area_of_use: { name: string; bounds: number[] } | null;
  units: { units: string[]; is_metric_metre: boolean; note: string };
  canonical_sha256: string;
}

export interface SourceDescribeView {
  kind: string;
  label: string;
  row_count: number;
  content_fingerprint: string;
  crs: string | null;
  crs_evidence: { origin: string; trusted: boolean };
  crs_description: {
    identity: string | null;
    name: string;
    type_name: string;
    axis_official: CrsAxisInfo[];
    carrier_convention: string;
    dimension: Record<string, unknown>;
    area_of_use: { name: string; bounds: number[] } | null;
    canonical_sha256: string;
  } | null;
  units?: { units: string[]; is_metric_metre: boolean; note: string };
  fields: Array<{ name: string; inferred_type: string; sample_values?: string[] }>;
  geometry: Record<string, unknown> | null;
  completeness?: Record<string, unknown>;
}

export interface CandidateView {
  candidate_id: string;
  availability: 'available' | 'unavailable';
  name: string;
  accuracy: number | null;
  accuracy_unknown: boolean;
  ballpark: boolean;
  area_of_use: { name: string; bounds: number[] } | null;
  grids: Array<{ short_name: string; available: boolean; url: string | null }>;
  definition_sha256: string;
  missing_reason?: string | null;
  selectable: boolean;
}

export interface OperationsListing {
  source: { kind: string; label: string; row_count: number; crs: string; crs_identity: string | null };
  target: { crs: string; identity: string | null; name: string };
  listing: {
    candidates: CandidateView[];
    available_count: number;
    unavailable_count: number;
    available_truncated: number;
    unavailable_truncated: number;
    environment: {
      pyproj_version: string;
      proj_version: string;
      network_enabled: boolean;
      trusted_grid_count: number;
    };
    axis_convention: string;
  };
}

export interface OperationSummary {
  candidate_id: string;
  name: string;
  accuracy: number | null;
  accuracy_unknown: boolean;
  ballpark: boolean;
  definition_sha256: string;
  environment: Record<string, unknown>;
  projjson_bytes: number;
}

export interface CoordOpPlanView {
  plan_id: string;
  session_id: string;
  title: string;
  status: 'draft' | 'locked' | 'archived';
  revision: number;
  plan: CoordOpPlanInput;
  normalized_digest: string;
  source_fingerprint: string | null;
  selected_candidate_id: string | null;
  operation: OperationSummary | null;
  preview: Record<string, unknown> | null;
  preview_digest: string | null;
  preview_stale: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface PreviewView {
  plan_id: string;
  plan_revision: number;
  sample_rule: string;
  sample_rows_requested: number;
  sample_rows_transformed: number;
  operation: OperationSummary | null;
  coordinate_pairs: Array<{ row: number; before: number[]; after: number[] }>;
  source_extent: number[] | null;
  target_extent: number[] | null;
  findings: Record<string, unknown>;
  area_of_use_check: string;
  accuracy_note: string;
  preview_is_not_full_execution: boolean;
}

export interface ReceiptView {
  run_id: string;
  rows_transformed: number;
  rows_total: number;
  null_geometry: number;
  empty_geometry: number;
  total_vertices: number;
  output_crs: string;
  output_ref: string;
  output_sha256: string;
  operation: {
    candidate_id: string;
    name: string;
    accuracy: number | null;
    accuracy_unknown: boolean;
    ballpark: boolean;
    definition_sha256: string;
  };
  native_refs?: { native_fabric_ref: string | null; output_session_ref: string | null };
  display: { state: string; display_ref?: string; reason?: string; operation?: { name: string } };
  budget_measured?: Record<string, number>;
  z_policy?: string;
}

export interface CoordOpRunView {
  run_id: string;
  plan_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  job_id: number | null;
  error_code: string | null;
  output_ref: string | null;
  output_rows: number | null;
  receipt: ReceiptView | null;
  plan_revision: number;
  created_at: string | null;
  finished_at: string | null;
}

export function asCoordOpError(err: unknown): { code: string; message: string; hint?: string } {
  const anyErr = err as { body?: { detail?: { error_code?: string; reason?: string; correction_hint?: string } } | { error_code?: string; reason?: string; correction_hint?: string }; message?: string };
  const body = anyErr?.body;
  const detail = (body as { detail?: unknown } | undefined)?.detail ?? body;
  if (detail && typeof detail === 'object' && 'error_code' in detail) {
    const d = detail as { error_code: string; reason?: string; correction_hint?: string };
    return { code: d.error_code, message: d.reason ?? '请求失败', hint: d.correction_hint };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}

type Options = { method?: string; body?: unknown; ownerToken?: string | null };

function client(sessionId: string, ownerToken?: string | null) {
  const base = `${BASE}/sessions/${encodeURIComponent(sessionId)}`;
  const opts = (o: Options): Options => ({ ...o, ownerToken });
  return {
    describeSource: (source: CoordOpSourceRefInput) =>
      apiFetch<SourceDescribeView>(`${base}/describe-source`,
        opts({ method: 'POST', body: { source } })),
    crsLookup: (query: string) =>
      apiFetch<CrsLookupView>(`${BASE}/crs/lookup?q=${encodeURIComponent(query)}`, opts({})),
    listOperations: (source: CoordOpSourceRefInput, targetCrs: string, aoi?: AoiInput | null) =>
      apiFetch<OperationsListing>(`${base}/operations`,
        opts({ method: 'POST', body: { source, target_crs: targetCrs, aoi: aoi ?? null } })),
    createPlan: (plan: CoordOpPlanInput) =>
      apiFetch<CoordOpPlanView>(`${base}/plans`, opts({ method: 'POST', body: { plan } })),
    updatePlan: (planId: string, plan: CoordOpPlanInput, expectedRevision: number) =>
      apiFetch<CoordOpPlanView>(`${base}/plans/${planId}`,
        opts({ method: 'PATCH', body: { plan, expected_revision: expectedRevision } })),
    getPlan: (planId: string) =>
      apiFetch<CoordOpPlanView>(`${base}/plans/${planId}`, opts({})),
    listPlans: () =>
      apiFetch<{ plans: CoordOpPlanView[] }>(`${base}/plans`, opts({})),
    archivePlan: (planId: string, expectedRevision: number) =>
      apiFetch<{ plan_id: string; status: string }>(
        `${base}/plans/${planId}?expected_revision=${expectedRevision}`,
        opts({ method: 'DELETE' })),
    lockPlan: (planId: string, candidateId: string, expectedRevision: number) =>
      apiFetch<CoordOpPlanView>(`${base}/plans/${planId}/lock`,
        opts({ method: 'POST', body: { candidate_id: candidateId, expected_revision: expectedRevision } })),
    preview: (planId: string, expectedRevision: number, sampleRows = 25) =>
      apiFetch<PreviewView>(`${base}/plans/${planId}/preview`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision, sample_rows: sampleRows } })),
    execute: (planId: string, expectedRevision: number, background = true) =>
      apiFetch<CoordOpRunView>(`${base}/plans/${planId}/execute`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision, background } })),
    inspectRun: (runId: string) =>
      apiFetch<CoordOpRunView>(`${base}/runs/${runId}`, opts({})),
    listRuns: (planId?: string | null) =>
      apiFetch<{ runs: Array<CoordOpRunView & { receipt_summary: unknown }> }>(
        `${base}/runs${planId ? `?plan_id=${encodeURIComponent(planId)}` : ''}`, opts({})),
    resultPage: (runId: string, cursor = 0, limit = 50) =>
      apiFetch<{
        run_id: string; rows: Array<{ properties: Record<string, unknown>; geometry_summary: { type: string } | null; first_coordinate: number[] | null }>;
        total_rows: number; cursor: number; next_cursor: number | null; page_size: number; crs: string | null;
      }>(`${base}/runs/${runId}/results?cursor=${cursor}&limit=${limit}`, opts({})),
    cancelRun: (runId: string) =>
      apiFetch<{ run_id: string; status: string }>(`${base}/runs/${runId}/cancel`,
        opts({ method: 'POST', body: {} })),
    displayRef: (runId: string) =>
      apiFetch<{ state: string; display_ref: string; rows: number }>(`${base}/runs/${runId}/display-ref`,
        opts({ method: 'POST', body: {} })),
    recover: () =>
      apiFetch<{ converged: Array<{ run_id: string; action: string }> }>(`${base}/recover`,
        opts({ method: 'POST', body: {} })),
  };
}

export const coordOpApi = { client };
