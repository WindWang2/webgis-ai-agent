/**
 * Data Delivery API 客户端（G18，`/api/v1/data-delivery*`）。
 *
 * 契约源：`app/api/routes/data_delivery.py`。能力查询 → 预览（损失/阻断）→
 * 提交（durable job，幂等）→ 轮询状态/回执 → 认证下载。取消复用统一任务
 * 中心 `DELETE /tasks/jobs/{id}`（lib/api/jobs），不另建通道。
 */
import { apiFetch } from './transport';

const API = '/api/v1/data-delivery';

export type DeliveryFormat =
  | 'geojson'
  | 'gpkg'
  | 'geoparquet'
  | 'csv'
  | 'shapefile_zip'
  | 'flatgeobuf';

export type SourceKind = 'session_ref' | 'fabric_parquet' | 'project_artifact' | 'table_rows';

export interface DeliverySourceRef {
  kind: SourceKind;
  session_id?: string;
  ref?: string;
  project_id?: string;
  artifact_id?: string;
  revision_no?: number;
  table_ref?: string;
}

export interface DeliveryScope {
  type: 'full' | 'ids' | 'filter';
  ids?: string[];
  id_field?: string;
  predicate?: Record<string, unknown>;
  bbox?: [number, number, number, number];
  selection_matched_count?: number;
  ids_truncated?: boolean;
  subset_ack?: boolean;
}

export interface DeliveryFieldSelection {
  fields?: string[];
  field_map?: Record<string, string>;
  include_geometry?: boolean;
}

export interface DeliveryCrsSpec {
  target_crs?: string;
  allow_unknown_source_crs?: boolean;
  coordinate_precision?: number;
}

export interface DeliverySpec {
  version?: number;
  source: DeliverySourceRef;
  scope?: DeliveryScope;
  fields?: DeliveryFieldSelection;
  crs?: DeliveryCrsSpec;
  format: DeliveryFormat;
  filename?: string;
  spreadsheet_safe?: boolean;
  geometry_grouping?: 'single' | 'by_type';
}

export interface FormatCapabilityView {
  format: DeliveryFormat;
  available: boolean;
  constrained: boolean;
  reason?: string | null;
  notes?: string[];
  mime?: string;
}

export interface DeliveryCapabilities {
  formats: FormatCapabilityView[];
  limits: {
    max_rows_per_delivery: number;
    max_output_bytes: number;
    max_fields: number;
    max_ids: number;
    [k: string]: number;
  };
  writer_versions: Record<string, string>;
}

export interface SourceIdentityView {
  kind: SourceKind;
  label: string;
  content_fingerprint?: string | null;
  row_count: number;
  is_table: boolean;
  crs?: string | null;
  crs_known: boolean;
  fields: { name: string; inferred_type: string }[];
  geometry?: {
    types: string[];
    has_z: boolean;
    null_or_empty_count: number;
  } | null;
  completeness?: Record<string, unknown>;
}

export interface LossItemView {
  code: string;
  message: string;
  fields?: string[] | null;
  requires_ack: boolean;
}

export interface DeliveryPreview {
  spec_fingerprint: string;
  source: SourceIdentityView;
  requested_format: FormatCapabilityView;
  formats: FormatCapabilityView[];
  estimated_rows?: number | null;
  estimated_bytes?: number | null;
  estimate_note?: string | null;
  losses: LossItemView[];
  blocking_issues: LossItemView[];
  required_acknowledgments: string[];
  field_name_map_applied?: Record<string, string> | null;
  scope_note?: string | null;
}

export interface DeliveryMemberView {
  path: string;
  role: string;
  bytes: number;
  sha256?: string | null;
  rows?: number | null;
}

export interface DeliveryReceiptView {
  delivery_id: string;
  job_id: string;
  format: DeliveryFormat;
  status: string;
  filename: string;
  members: DeliveryMemberView[];
  row_count: number;
  bytes_total: number;
  content_sha256?: string | null;
  crs: { source?: string | null; target?: string | null; transformed: boolean };
  losses_applied: string[];
  field_map_applied?: Record<string, string> | null;
  scope: Record<string, unknown>;
  source: Record<string, unknown>;
  writer_versions: Record<string, string>;
  data_object_id?: string | null;
  durable?: string;
  timings?: Record<string, unknown>;
}

export interface DeliveryJobView {
  job_id: string;
  status: 'pending' | 'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'stale';
  progress: number;
  progress_message?: string | null;
  stage?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  spec_fingerprint?: string | null;
  format?: DeliveryFormat | null;
  filename?: string | null;
  receipt?: DeliveryReceiptView | null;
  download_url?: string | null;
  error_code?: string | null;
  error?: string | null;
}

export async function fetchDeliveryCapabilities(): Promise<DeliveryCapabilities> {
  return apiFetch<DeliveryCapabilities>(`${API}/capabilities`);
}

export async function previewDataDelivery(spec: DeliverySpec): Promise<DeliveryPreview> {
  return apiFetch<DeliveryPreview>(`${API}/preview`, {
    method: 'POST',
    body: spec,
  });
}

export async function submitDataDelivery(
  spec: DeliverySpec,
  acknowledgedLosses: string[],
  projectId?: string,
): Promise<{ job_id: string; status: string; idempotent_reuse: boolean; message: string }> {
  return apiFetch(`${API}/jobs`, {
    method: 'POST',
    body: { spec, acknowledged_losses: acknowledgedLosses, project_id: projectId },
  });
}

export async function getDeliveryJob(jobId: string): Promise<DeliveryJobView> {
  return apiFetch<DeliveryJobView>(`${API}/jobs/${jobId}`);
}

export async function listDeliveryJobs(projectId?: string): Promise<DeliveryJobView[]> {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return apiFetch<DeliveryJobView[]>(`${API}/jobs${q}`);
}
