/**
 * Typed frontend API client for G48 Control-Point Georeferencing Workbench.
 *
 * Strictly aligned with `app/schemas/georeferencing_schema.py` and
 * `app/api/routes/georeferencing.py`. All binary/image requests go through
 * unified transport (`apiFetchBlob` / `downloadWithAuth`) so Bearer and
 * `X-Session-Token` (SEC-08) headers are attached consistently.
 */

import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { ApiError, apiFetch, apiFetchBlob } from '@/lib/api/transport';

export interface GeorefCtx {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

export interface ExistingGeorefMetadata {
  has_existing_crs: boolean;
  existing_crs: string | null;
  has_existing_transform: boolean;
  has_embedded_gcps: boolean;
  embedded_gcp_count: number;
  warnings: string[];
}

export interface GeorefSourceRecord {
  source_id: string;
  session_id: string;
  filename: string;
  detected_format: string;
  width: number;
  height: number;
  bands: number;
  dtype: string;
  color_mode: string;
  has_alpha: boolean;
  exif_orientation: number;
  exif_applied_transform: string;
  exif_overridden: boolean;
  original_blob_key: string;
  normalized_blob_key: string;
  normalized_path: string;
  original_sha256: string;
  normalized_sha256: string;
  byte_size: number;
  observations: string[];
  existing_georef: ExistingGeorefMetadata;
  created_at: string;
}

export interface GeorefCoordProvenance {
  source_crs: string;
  raw_x: number;
  raw_y: number;
  target_crs: string;
  map_x: number;
  map_y: number;
  axis_order: string;
  transform_note: string;
  warnings: string[];
}

export interface GcpPoint {
  gcp_id: string;
  label: string;
  px: number;
  py: number;
  map_x: number;
  map_y: number;
  role: 'fit' | 'check';
  enabled: boolean;
  note: string;
  provenance: GeorefCoordProvenance | null;
}

/** UI working copy while editing/picking coordinates before Save & Solve. */
export interface EditableGcpRow {
  gcp_id: string;
  label: string;
  px: number | null;
  py: number | null;
  wgs84_lon: number | null;
  wgs84_lat: number | null;
  map_x: number | null;
  map_y: number | null;
  role: 'fit' | 'check';
  enabled: boolean;
  note: string;
}

export interface GcpInputItem {
  gcp_id?: string | null;
  label: string;
  px: number;
  py: number;
  map_x?: number | null;
  map_y?: number | null;
  wgs84_lon?: number | null;
  wgs84_lat?: number | null;
  role: 'fit' | 'check';
  enabled: boolean;
  note: string;
}

export interface AffineCoefficients {
  a0: number;
  a1: number;
  a2: number;
  b0: number;
  b1: number;
  b2: number;
  px_scale_x: number;
  px_scale_y: number;
  rotation_deg: number;
  shear_deg: number;
  determinant: number;
  is_reflection: boolean;
}

export interface PointResidual {
  gcp_id: string;
  label: string;
  role: 'fit' | 'check';
  enabled: boolean;
  px: number;
  py: number;
  map_x: number;
  map_y: number;
  pred_x: number | null;
  pred_y: number | null;
  dx: number | null;
  dy: number | null;
  radial_error: number | null;
}

export interface AffineFitMetrics {
  status: 'valid' | 'insufficient' | 'degenerate' | 'invalid';
  fit_count: number;
  check_count: number;
  disabled_count: number;
  fit_rmse: number | null;
  fit_max_error: number | null;
  check_rmse: number | null;
  check_max_error: number | null;
  zero_residual_unverified: boolean;
  condition_number: number | null;
  rank: number;
  hull_coverage_ratio: number;
  low_coverage_warning: boolean;
  extrapolation_warning: boolean;
  outlier_gcp_ids: string[];
  warnings: string[];
  blockers: string[];
  coefficients: AffineCoefficients | null;
  residuals: PointResidual[];
}

export interface GeorefArtifactRecord {
  artifact_id: string;
  plan_id: string;
  execution_id: string;
  job_id: string | null;
  session_id: string;
  format: 'GeoTIFF';
  cog_requested: boolean;
  cog_verified: boolean;
  cog_checks: Record<string, unknown>;
  target_crs: string;
  width: number;
  height: number;
  bands: number;
  dtype: string;
  has_alpha: boolean;
  nodata_mode: string;
  sha256: string;
  byte_size: number;
  blob_key: string;
  file_path: string;
  raster_ref: string;
  bounds_target_crs: number[];
  bounds_wgs84: number[];
  footprint_wgs84: Record<string, unknown>;
  created_at: string;
  mapspec_layer_id: string | null;
  mapspec_revision: number | null;
}

export interface GeorefDraftPlan {
  plan_id: string;
  session_id: string;
  source_id: string;
  name: string;
  revision: number;
  transform_model: 'affine_2d';
  target_crs: string;
  resampling: 'bilinear' | 'nearest';
  target_resolution_m: number | null;
  generate_cog: boolean;
  ignore_exif: boolean;
  gcps: GcpPoint[];
  fit_metrics: AffineFitMetrics | null;
  preview_digest: string | null;
  preview_stale: boolean;
  preview_footprint_wgs84: Record<string, unknown> | null;
  preview_bounds_wgs84: number[] | null;
  preview_path: string | null;
  active_job_id: string | null;
  active_execution_id: string | null;
  latest_artifact: GeorefArtifactRecord | null;
  mapspec_bind_status: string | null;
  created_at: string;
  updated_at: string;
}

export interface GeorefSourceResponse {
  source: GeorefSourceRecord;
  image_url: string;
}

export interface GeorefPreviewResponse {
  plan: GeorefDraftPlan;
  preview_available: boolean;
  preview_image_url: string | null;
  out_width: number | null;
  out_height: number | null;
  estimated_output_pixels: number | null;
  estimated_output_bytes: number | null;
  warnings: string[];
}

export interface GeorefExecuteResponse {
  plan_id: string;
  job_id: string;
  execution_id: string;
  status: string;
  background: boolean;
  idempotent_reuse: boolean;
  artifact: GeorefArtifactRecord | null;
  poll_url: string;
}

export interface GeorefJobStatusResponse {
  plan_id: string;
  job_id: string | null;
  execution_id: string | null;
  status: string;
  stage: string;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  artifact: GeorefArtifactRecord | null;
  plan: GeorefDraftPlan;
}

export interface GeorefBindLayerResponse {
  bound: boolean;
  partial_success: boolean;
  layer_id: string | null;
  mapspec_revision: number | null;
  current_mapspec_revision: number | null;
  error_code: string | null;
  message: string;
  artifact: GeorefArtifactRecord | null;
  mapspec?: Record<string, unknown> | null;
}

export interface GeorefErrorInfo {
  code: string;
  message: string;
  status: number;
  details: Record<string, unknown>;
}

const BASE = '/api/v1/georeferencing';

function q(
  ctx: GeorefCtx,
  extra?: Record<string, string | boolean | number | undefined>,
): string {
  const params = new URLSearchParams({ session_id: ctx.sessionId });
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v !== undefined) params.set(k, String(v));
    }
  }
  return `?${params.toString()}`;
}

function opts(ctx: GeorefCtx) {
  return {
    ownerToken: ctx.ownerToken,
    signal: ctx.signal,
    label: 'Georeferencing API',
  };
}

export function toEditableRows(gcps: GcpPoint[]): EditableGcpRow[] {
  return gcps.map((g) => ({
    gcp_id: g.gcp_id,
    label: g.label,
    px: g.px,
    py: g.py,
    wgs84_lon: g.provenance?.source_crs === 'EPSG:4326' ? g.provenance.raw_x : null,
    wgs84_lat: g.provenance?.source_crs === 'EPSG:4326' ? g.provenance.raw_y : null,
    map_x: g.map_x,
    map_y: g.map_y,
    role: g.role,
    enabled: g.enabled,
    note: g.note,
  }));
}

export function asGeorefError(err: unknown): GeorefErrorInfo {
  if (err instanceof ApiError) {
    const raw = err.body;
    const rawRec = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null;
    const detail = rawRec
      ? (rawRec.data && typeof rawRec.data === 'object'
          ? rawRec.data
          : rawRec.detail && typeof rawRec.detail === 'object'
            ? rawRec.detail
            : rawRec)
      : null;
    if (detail && typeof detail === 'object') {
      const rec = detail as Record<string, unknown>;
      return {
        code: typeof rec.code === 'string' ? rec.code : `HTTP_${err.status}`,
        message: typeof rec.message === 'string' ? rec.message : err.message,
        status: err.status,
        details: (
          typeof rec.details === 'object' && rec.details ? rec.details : {}
        ) as Record<string, unknown>,
      };
    }
    return {
      code: `HTTP_${err.status}`,
      message: err.message,
      status: err.status,
      details: {},
    };
  }
  return {
    code: 'UNKNOWN_ERROR',
    message: err instanceof Error ? err.message : String(err),
    status: 0,
    details: {},
  };
}

export function sourceImagePath(sessionId: string, sourceId: string): string {
  return `${BASE}/sources/${encodeURIComponent(sourceId)}/image?session_id=${encodeURIComponent(sessionId)}`;
}

export function previewImagePath(
  sessionId: string,
  planId: string,
  digest?: string | null,
): string {
  const v = digest ? `&v=${encodeURIComponent(digest)}` : '';
  return `${BASE}/plans/${encodeURIComponent(planId)}/preview/image?session_id=${encodeURIComponent(sessionId)}${v}`;
}

export function artifactDownloadPath(sessionId: string, artifactId: string): string {
  return `${BASE}/artifacts/${encodeURIComponent(artifactId)}/download?session_id=${encodeURIComponent(sessionId)}`;
}

export async function fetchProtectedImageUrl(
  path: string,
  ownerToken: string | null,
  signal?: AbortSignal,
): Promise<string> {
  const { blob } = await apiFetchBlob(path, {
    ownerToken,
    signal,
    label: 'Georeferencing image',
  });
  return URL.createObjectURL(blob);
}

export async function uploadGeorefSource(
  ctx: GeorefCtx,
  file: File,
  ignoreExif = false,
): Promise<GeorefSourceResponse> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<GeorefSourceResponse>(
    `${BASE}/sources/upload${q(ctx, { ignore_exif: ignoreExif })}`,
    {
      ...opts(ctx),
      method: 'POST',
      rawBody: form,
      timeoutMs: 120_000,
    },
  );
}

export function listGeorefSources(ctx: GeorefCtx) {
  return apiFetch<{ session_id: string; sources: GeorefSourceRecord[] }>(
    `${BASE}/sources${q(ctx)}`,
    opts(ctx),
  );
}

export function deleteGeorefSource(ctx: GeorefCtx, sourceId: string) {
  return apiFetch<{ deleted: boolean; source_id: string; released_blobs: number }>(
    `${BASE}/sources/${encodeURIComponent(sourceId)}${q(ctx)}`,
    { ...opts(ctx), method: 'DELETE' },
  );
}

export function createGeorefPlan(
  ctx: GeorefCtx,
  payload: {
    session_id: string;
    source_id: string;
    name?: string;
    target_crs: string;
    resampling: 'bilinear' | 'nearest';
    target_resolution_m?: number | null;
    generate_cog: boolean;
  },
) {
  return apiFetch<{ plan: GeorefDraftPlan; source?: GeorefSourceRecord | null }>(
    `${BASE}/plans${q(ctx)}`,
    { ...opts(ctx), method: 'POST', body: payload },
  );
}

export function listGeorefPlans(ctx: GeorefCtx) {
  return apiFetch<{ session_id: string; plans: GeorefDraftPlan[] }>(
    `${BASE}/plans${q(ctx)}`,
    opts(ctx),
  );
}

export function getGeorefPlan(ctx: GeorefCtx, planId: string) {
  return apiFetch<{ plan: GeorefDraftPlan; source?: GeorefSourceRecord | null }>(
    `${BASE}/plans/${encodeURIComponent(planId)}${q(ctx)}`,
    opts(ctx),
  );
}

export function saveGeorefDraft(
  ctx: GeorefCtx,
  planId: string,
  payload: {
    expected_revision: number;
    name?: string;
    target_crs?: string;
    resampling?: 'bilinear' | 'nearest';
    target_resolution_m?: number | null;
    clear_target_resolution?: boolean;
    generate_cog?: boolean;
    gcps?: GcpInputItem[];
  },
) {
  return apiFetch<{ plan: GeorefDraftPlan; source?: GeorefSourceRecord | null }>(
    `${BASE}/plans/${encodeURIComponent(planId)}/draft${q(ctx)}`,
    { ...opts(ctx), method: 'PUT', body: payload },
  );
}

export function importGeorefGcps(
  ctx: GeorefCtx,
  planId: string,
  payload: {
    expected_revision: number;
    format: 'json' | 'csv';
    content: string;
    replace_existing?: boolean;
    coords_are_wgs84?: boolean;
  },
) {
  return apiFetch<{ plan: GeorefDraftPlan; source?: GeorefSourceRecord | null }>(
    `${BASE}/plans/${encodeURIComponent(planId)}/gcps/import${q(ctx)}`,
    { ...opts(ctx), method: 'POST', body: payload },
  );
}

export async function downloadGeorefGcps(
  ctx: GeorefCtx,
  planId: string,
  fmt: 'json' | 'csv',
): Promise<void> {
  await downloadWithAuth(
    `${BASE}/plans/${encodeURIComponent(planId)}/gcps/export${q(ctx, { fmt })}`,
    {
      ownerToken: ctx.ownerToken,
      filename: `gcps_${planId}.${fmt}`,
      label: 'Export GCPs',
    },
  );
}

export function previewGeorefPlan(
  ctx: GeorefCtx,
  planId: string,
  expectedRevision?: number,
) {
  return apiFetch<GeorefPreviewResponse>(
    `${BASE}/plans/${encodeURIComponent(planId)}/preview${q(ctx)}`,
    {
      ...opts(ctx),
      method: 'POST',
      body: expectedRevision != null ? { expected_revision: expectedRevision } : undefined,
      timeoutMs: 60_000,
    },
  );
}

export function executeGeorefPlan(
  ctx: GeorefCtx,
  planId: string,
  payload: {
    expected_revision: number;
    background: boolean;
    idempotency_key?: string;
  },
) {
  return apiFetch<GeorefExecuteResponse>(
    `${BASE}/plans/${encodeURIComponent(planId)}/execute${q(ctx)}`,
    { ...opts(ctx), method: 'POST', body: payload, timeoutMs: 120_000 },
  );
}

export function cancelGeorefPlan(ctx: GeorefCtx, planId: string) {
  return apiFetch<GeorefJobStatusResponse>(
    `${BASE}/plans/${encodeURIComponent(planId)}/cancel${q(ctx)}`,
    { ...opts(ctx), method: 'POST' },
  );
}

export function pollGeorefStatus(ctx: GeorefCtx, planId: string) {
  return apiFetch<GeorefJobStatusResponse>(
    `${BASE}/plans/${encodeURIComponent(planId)}/status${q(ctx)}`,
    opts(ctx),
  );
}

export function bindGeorefLayer(
  ctx: GeorefCtx,
  planId: string,
  payload: {
    expected_mapspec_revision: number;
    layer_id?: string;
    layer_name?: string;
    opacity?: number;
  },
) {
  return apiFetch<GeorefBindLayerResponse>(
    `${BASE}/plans/${encodeURIComponent(planId)}/bind-layer${q(ctx)}`,
    { ...opts(ctx), method: 'POST', body: payload },
  );
}

export async function downloadGeorefArtifact(
  ctx: GeorefCtx,
  artifactId: string,
): Promise<void> {
  await downloadWithAuth(artifactDownloadPath(ctx.sessionId, artifactId), {
    ownerToken: ctx.ownerToken,
    filename: `georef_${artifactId}.tif`,
    label: 'Download GeoTIFF',
  });
}
