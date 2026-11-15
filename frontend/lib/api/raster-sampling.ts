/**
 * Raster Sampling API — 数值栅格点集/沿线采样工作台客户端（G39）。
 *
 * 链路：describe → createPlan → preview(有界) → execute(durable) →
 * status → results(分页 + 曲线)。所有请求走共享 transport（apiFetch）；
 * owner token 经 X-Session-Token header；POST 永不重放。
 */

import { apiFetch } from './transport';

const RS_BASE = '/api/v1/raster-sampling';
const RS_LABEL = 'Raster sampling API error';

export interface RasterSamplingRequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export interface RasterRefOption {
  ref_id: string;
  alias: string;
  kind?: string | null;
  verified: boolean;
}

export interface DescribeResponse {
  success: boolean;
  raster_refs: RasterRefOption[];
  truncated: boolean;
  budgets: Record<string, number>;
  methods: string[];
  distance_modes: string[];
  algorithm_version: string;
}

export interface SourceSpec {
  ref_id: string;
  label?: string;
  bands: number[];
  band_labels?: Record<string, string>;
  band_units?: Record<string, string>;
  band_semantics?: Record<string, string>;
}

export interface StationSummary {
  count: number;
  crs: string;
  mode: string;
  part_ids: string[];
  part_breaks: number;
  distance_mode: string;
  line_length_m: number;
  notes: string[];
  first_sample_ids: string[];
}

export interface PlanPayload {
  plan_id: string;
  name: string;
  revision: number;
  mode: string;
  station_spec: Record<string, unknown>;
  sources: Array<Record<string, unknown> & { ref_id: string; bands: number[] }>;
  method: string;
  nodata_policy: string;
  algorithm_version: string;
  params_digest: string;
}

export interface PlanResponse {
  success: boolean;
  plan: PlanPayload;
  stations: StationSummary;
  selection_materialized?: {
    ref_id: string;
    feature_count: number;
    coarse_bbox: boolean;
    revision: number;
  } | null;
}

export interface SampleRow {
  sample_id: string;
  station_index: number;
  parent_locator: string;
  source_ref: string;
  band: number;
  band_label: string;
  station_x: number;
  station_y: number;
  station_crs: string;
  part_id: string;
  chainage_m: number | null;
  source_x: number;
  source_y: number;
  row: number | null;
  col: number | null;
  raw_value: number | null;
  raw_exact?: string | null;
  physical_value: number | null;
  interpolated_value: number | null;
  value_domain: string;
  method: string;
  unit: string;
  unit_status: string;
  valid: boolean;
  invalid_reason: string | null;
  support_ratio: number | null;
  data_revision: string;
}

export interface ReadMetrics {
  opens: number;
  blocks_touched: number;
  window_reads: number;
  batches: number;
  decoded_bytes: number;
  stations_processed: number;
  block_shapes: Record<string, number[]>;
  is_tiled: Record<string, boolean>;
  overview_used: boolean;
}

export interface PreviewResponse {
  success: boolean;
  plan_id: string;
  revision: number;
  preview: boolean;
  station_selection: { requested_stations: number; previewed_stations: number; method: string };
  rows_shown: number;
  rows_truncated: boolean;
  rows: SampleRow[];
  read_metrics: ReadMetrics;
  cost_estimate: Record<string, unknown>;
  coverage_preview: {
    requested_cells: number;
    valid_cells: number;
    invalid_by_reason: Record<string, number>;
  };
}

export interface Coverage {
  requested_cells: number;
  valid_cells: number;
  invalid_by_reason: Record<string, number>;
  stations_requested: number;
  stations_with_any_valid: number;
}

export interface ReceiptSummary {
  execution_id: string;
  plan_id: string;
  plan_revision: number;
  status: string;
  method: string;
  station_count: number;
  params_digest: string;
  coverage: Coverage;
  outputs: {
    sample_table_ref: string | null;
    station_layer_ref: string | null;
  };
  degraded: string[];
  job_id: string | null;
  completed_at: number;
}

export interface ExecuteResponse {
  success?: boolean;
  completed?: boolean;
  status?: string;
  job_id?: string;
  plan_id?: string;
  idempotent_reuse?: boolean;
  background?: boolean;
  poll?: string;
  detail?: string;
  receipt?: ReceiptSummary;
}

export interface StatusResponse {
  success: boolean;
  plan_id: string;
  revision: number;
  job: { job_id: string; status: string } | null;
  receipt_count: number;
  latest_receipt: ReceiptSummary | null;
  outputs_alive?: { sample_table: boolean };
  recovery_required?: boolean;
  recovery_reason?: string;
}

export interface CurvePoint {
  sid: string;
  x: number | null;
  y: number | null;
  part: string;
  gap: boolean;
}

export interface ResultsResponse {
  success: boolean;
  execution_id: string;
  plan_id: string;
  receipt: ReceiptSummary;
  rows: SampleRow[];
  pagination: {
    page: number;
    per_page: number;
    filtered_rows: number;
    total_rows: number;
    filters: Record<string, unknown>;
  };
  curves?: {
    bands: Array<{
      source_ref: string;
      band: number;
      points: CurvePoint[];
    }>;
    thinning: Record<string, Record<string, unknown>>;
  };
  station_layer_ref?: string;
  band_columns?: Record<string, { source_ref: string; band: number }>;
}

export interface PlanCreateParams {
  mode: 'source_points' | 'line_stations';
  points_ref?: string;
  selection?: { ref_id: string; handle: string } | null;
  lines_ref?: string;
  line_feature_id?: string;
  station_crs?: string;
  distance_mode?: string;
  station_step?: number | null;
  include_vertices?: boolean;
  method?: string;
  name?: string;
  sources: SourceSpec[];
}

function headers(options: RasterSamplingRequestOptions): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (options.ownerToken) h['X-Session-Token'] = options.ownerToken;
  return h;
}

export async function describeSources(
  sessionId: string,
  options: RasterSamplingRequestOptions = {},
): Promise<DescribeResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/describe?${qs}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export async function createPlan(
  sessionId: string,
  params: PlanCreateParams,
  options: RasterSamplingRequestOptions = {},
): Promise<PlanResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan?${qs}`, {
    method: 'POST',
    headers: headers(options),
    body: JSON.stringify(params),
    signal: options.signal,
  });
}

export async function revisePlan(
  sessionId: string,
  planId: string,
  expectedRevision: number,
  updates: Record<string, unknown>,
  options: RasterSamplingRequestOptions = {},
): Promise<PlanResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}/revise?${qs}`, {
    method: 'POST',
    headers: headers(options),
    body: JSON.stringify({ expected_revision: expectedRevision, ...updates }),
    signal: options.signal,
  });
}

export async function getPlan(
  sessionId: string,
  planId: string,
  options: RasterSamplingRequestOptions = {},
): Promise<{ success: boolean; plan: PlanPayload }> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}?${qs}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export async function listPlans(
  sessionId: string,
  options: RasterSamplingRequestOptions = {},
): Promise<{ success: boolean; plans: Array<PlanPayload & { latest_receipt: ReceiptSummary | null }> }> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plans?${qs}`, { headers: headers(options), signal: options.signal });
}

export async function previewPlan(
  sessionId: string,
  planId: string,
  expectedRevision: number | null,
  limit: number,
  options: RasterSamplingRequestOptions = {},
): Promise<PreviewResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}/preview?${qs}`, {
    method: 'POST',
    headers: headers(options),
    body: JSON.stringify({
      expected_revision: expectedRevision,
      limit,
    }),
    signal: options.signal,
  });
}

export async function executePlan(
  sessionId: string,
  planId: string,
  expectedRevision: number | null,
  background: boolean,
  options: RasterSamplingRequestOptions = {},
): Promise<ExecuteResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}/execute?${qs}`, {
    method: 'POST',
    headers: headers(options),
    body: JSON.stringify({
      expected_revision: expectedRevision,
      background,
    }),
    signal: options.signal,
  });
}

export async function planStatus(
  sessionId: string,
  planId: string,
  options: RasterSamplingRequestOptions = {},
): Promise<StatusResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}/status?${qs}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export async function fetchResults(
  sessionId: string,
  params: {
    planId?: string;
    executionId?: string;
    page?: number;
    perPage?: number;
    includeCurves?: boolean;
    sourceRef?: string;
    band?: number | null;
    valid?: boolean | null;
  },
  options: RasterSamplingRequestOptions = {},
): Promise<ResultsResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  if (params.planId) qs.set('plan_id', params.planId);
  if (params.executionId) qs.set('execution_id', params.executionId);
  qs.set('page', String(params.page ?? 1));
  qs.set('per_page', String(params.perPage ?? 50));
  qs.set('include_curves', String(params.includeCurves ?? true));
  if (params.sourceRef) qs.set('source_ref', params.sourceRef);
  if (params.band != null) qs.set('band', String(params.band));
  if (params.valid != null) qs.set('valid', String(params.valid));
  return apiFetch(`${RS_BASE}/results?${qs}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export async function cancelPlan(
  sessionId: string,
  planId: string,
  options: RasterSamplingRequestOptions = {},
): Promise<{ success: boolean; cancelled: Array<{ job_id: string; requested: boolean; status: string }> }> {
  const qs = new URLSearchParams({ session_id: sessionId });
  return apiFetch(`${RS_BASE}/plan/${encodeURIComponent(planId)}/cancel?${qs}`, {
    method: 'POST',
    headers: headers(options),
    signal: options.signal,
  });
}

export { RS_LABEL };
