/**
 * Project workspace assets API client — datasets / artifacts / snapshots /
 * quality / data-gc (ADR-0143).
 *
 * Contract source: `app/api/routes/project.py` (48 endpoints, recon doc
 * `frontend/docs/workspace-ui-recon.md`). Shapes marked ⚠ in the recon doc
 * are honored here:
 *   - datasets have NO rename/preview/detail endpoint (attach response is the
 *     only carrier of schema_profile);
 *   - artifacts have NO download and NO revisions listing endpoint; the
 *     pin/clone/lineage paths are project-independent (`/projects/artifacts/…`);
 *   - snapshot list is bounded (≤50) and does NOT use the Page envelope;
 *   - no endpoint returns a job id — everything is synchronous long-blocking
 *     HTTP, so callers pass generous timeouts and render result reports;
 *   - data-gc has no staging/rollback endpoint — grace period is display-only.
 *
 * Cross-family aggregation (allowed by the line contract): the data-fabric
 * catalog preview endpoint backs dataset table previews and quality-audit
 * GeoJSON sourcing; the aggregation itself lives in hooks, not here.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';
import type { Page, ProjectDataset } from './project';

const API = '/api/v1/projects';
const FABRIC_PREVIEW = '/api/v1/data-fabric/catalog';

/** Re-export so panels can import the whole asset surface from one module. */
export type { Page, ProjectDataset };

function asPage<T>(data: Page<T> | T[] | undefined): Page<T> {
  if (!data) return { items: [], total: 0, limit: 50, offset: 0, has_more: false };
  if (Array.isArray(data)) {
    return { items: data, total: data.length, limit: data.length || 50, offset: 0, has_more: false };
  }
  return {
    items: Array.isArray(data.items) ? data.items : [],
    total: data.total ?? 0,
    limit: data.limit ?? 50,
    offset: data.offset ?? 0,
    has_more: Boolean(data.has_more),
  };
}

// ── datasets ────────────────────────────────────────────────────────────────

export type DatasetSourceType = 'upload' | 'layer' | 'external' | 'vector' | 'raster';

export interface DatasetAttachRequest {
  name: string;
  source_type: DatasetSourceType;
  source_ref?: string;
  schema_profile?: Record<string, unknown>;
  /** Omit to let the backend default; never fabricate a CRS for display. */
  crs?: string;
}

/** Attach response — the only place schema_profile comes back. */
export interface DatasetAttached extends ProjectDataset {
  schema_profile?: Record<string, unknown>;
}

export interface DatasetDetachResponse {
  status: string;
  message: string;
}

export async function attachDataset(
  projectId: string,
  req: DatasetAttachRequest,
): Promise<DatasetAttached> {
  const attached = await apiFetch<DatasetAttached>(`${API}/${projectId}/datasets`, {
    method: 'POST',
    body: req,
    label: 'Dataset attach error',
  });
  invalidateCache(`${API}/${projectId}/datasets`);
  return attached;
}

/**
 * Soft tombstone (INV-DEL1): the backend keeps the row for lineage resolution
 * and performs NO reference check — the confirm dialog must carry the warning.
 */
export async function detachDataset(projectId: string, datasetId: string): Promise<DatasetDetachResponse> {
  const result = await apiFetch<DatasetDetachResponse>(
    `${API}/${projectId}/datasets/${datasetId}`,
    { method: 'DELETE', label: 'Dataset detach error' },
  );
  invalidateCache(`${API}/${projectId}/datasets`);
  return result;
}

/**
 * Paginated variant — `fetchProjectDatasets` in project.ts unwraps `items`,
 * which loses `total`/`has_more`; the dataset panel needs the full envelope.
 */
export async function fetchProjectDatasetPage(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; limit?: number; offset?: number },
): Promise<Page<ProjectDataset>> {
  const path = `${API}/${projectId}/datasets`;
  const result = await fastGet<Page<ProjectDataset> | ProjectDataset[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit, offset: opts?.offset },
    label: 'Project datasets error',
  });
  return asPage(result.data);
}

// ── artifacts ───────────────────────────────────────────────────────────────

/** List-row truth: no storage_ref / content_fingerprint on the list endpoint. */
export interface ArtifactSummary {
  id: string;
  project_id: string;
  name: string;
  artifact_type: string;
  format?: string | null;
  /** Unknown CRS is null — never default to EPSG:4326. */
  crs: string | null;
  created_at: string;
}

export interface ArtifactPinResponse {
  status: string;
  artifact_id: string;
  revision_no?: number | null;
  content_sha256?: string | null;
  pinned: boolean;
  pinned_at?: string | null;
}

export interface ArtifactCloneResponse {
  status: string;
  artifact_id: string;
  source_artifact_id: string;
  name?: string | null;
  content_location?: string | null;
  content_sha256?: string | null;
}

export async function fetchProjectArtifacts(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; limit?: number; offset?: number },
): Promise<Page<ArtifactSummary>> {
  const path = `${API}/${projectId}/artifacts`;
  const result = await fastGet<Page<ArtifactSummary> | ArtifactSummary[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit, offset: opts?.offset },
    label: 'Project artifacts error',
  });
  return asPage(result.data);
}

/** Pin/clone/lineage live OUTSIDE the project segment (auth inside service). */
export async function pinArtifact(artifactId: string, pinned = true): Promise<ArtifactPinResponse> {
  return apiFetch<ArtifactPinResponse>(`${API}/artifacts/${artifactId}/pin`, {
    method: 'POST',
    body: { pinned },
    label: 'Artifact pin error',
  });
}

export async function unpinArtifact(artifactId: string): Promise<ArtifactPinResponse> {
  return apiFetch<ArtifactPinResponse>(`${API}/artifacts/${artifactId}/pin`, {
    method: 'DELETE',
    label: 'Artifact unpin error',
  });
}

export async function cloneArtifact(artifactId: string): Promise<ArtifactCloneResponse> {
  return apiFetch<ArtifactCloneResponse>(`${API}/artifacts/${artifactId}/clone`, {
    method: 'POST',
    label: 'Artifact clone error',
  });
}

// ── workspace snapshots ─────────────────────────────────────────────────────

export interface WorkspaceSnapshotSummary {
  snapshot_id: string;
  label?: string | null;
  /** Epoch seconds; absent means unknown — never synthesize a date. */
  created_at?: number | null;
  artifacts: number;
  layers: number;
  project_id: string;
  home: 'project' | 'session' | string;
}

/** Bounded list (≤50): NOT the Page envelope. */
export interface WorkspaceSnapshotListResponse {
  project_id: string;
  count: number;
  bounded: number;
  items: WorkspaceSnapshotSummary[];
}

export type SnapshotIntegrityStatus = 'verified' | 'digest_mismatch' | 'pointer_missing' | 'no_pointer' | string;

/** GET …/snapshots/{sid} verify report (SnapshotVerification.to_dict()). */
export interface SnapshotVerification {
  snapshot_id: string;
  exists: boolean;
  integrity_ok: boolean;
  restorable: boolean;
  artifacts: { total: number; live: number; missing: string[] };
  layers: { total: number; live: number; missing: string[] };
  mapspec_available: boolean;
  integrity: Record<string, SnapshotIntegrityStatus>;
}

export interface SnapshotSaveRequest {
  session_id: string;
  label?: string;
  materialize?: 'none' | 'claimed' | 'all';
}

export interface WorkspaceSnapshotSaveResponse {
  status: string;
  project_id: string;
  home: string;
  snapshot_id: string;
  label?: string | null;
  durable_pointers: number;
  materialize_skipped: string[];
  snapshot: Record<string, unknown>;
}

/**
 * Restore result. `verify` returns {mode, verification}; `register` adds
 * re-registration / re-materialization disclosures (dead refs degrade to
 * `expired`/`degraded`). The backend returns a raw dict — known fields are
 * typed, the rest stay accessible via the index signature.
 */
export interface SnapshotRestoreResponse {
  mode: string;
  verification: SnapshotVerification;
  error?: string;
  [key: string]: unknown;
}

export async function listWorkspaceSnapshots(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; sessionId?: string },
): Promise<WorkspaceSnapshotListResponse> {
  const result = await fastGet<WorkspaceSnapshotListResponse>(
    `${API}/${projectId}/workspace/snapshots`,
    {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      params: { session_id: opts?.sessionId },
      label: 'Workspace snapshots error',
    },
  );
  return result.data;
}

export async function saveWorkspaceSnapshot(
  projectId: string,
  req: SnapshotSaveRequest,
): Promise<WorkspaceSnapshotSaveResponse> {
  const saved = await apiFetch<WorkspaceSnapshotSaveResponse>(
    `${API}/${projectId}/workspace/snapshots`,
    {
      method: 'POST',
      body: req,
      // materialize=all re-materializes payloads server-side — long-blocking.
      timeoutMs: 120_000,
      label: 'Snapshot save error',
    },
  );
  invalidateCache(`${API}/${projectId}/workspace/snapshots`);
  return saved;
}

export async function inspectWorkspaceSnapshot(
  projectId: string,
  snapshotId: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; forceRefresh?: boolean },
): Promise<SnapshotVerification> {
  const result = await fastGet<SnapshotVerification>(
    `${API}/${projectId}/workspace/snapshots/${snapshotId}`,
    {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      ttlMs: 0,
      params: { session_id: sessionId },
      label: 'Snapshot inspect error',
    },
  );
  return result.data;
}

export async function restoreWorkspaceSnapshot(
  projectId: string,
  snapshotId: string,
  req: { session_id: string; mode: 'verify' | 'register' },
): Promise<SnapshotRestoreResponse> {
  const restored = await apiFetch<SnapshotRestoreResponse>(
    `${API}/${projectId}/workspace/snapshots/${snapshotId}/restore`,
    {
      method: 'POST',
      body: req,
      timeoutMs: 120_000,
      label: 'Snapshot restore error',
    },
  );
  invalidateCache(`${API}/${projectId}/workspace/snapshots`);
  return restored;
}

export async function cloneWorkspaceSnapshot(
  projectId: string,
  snapshotId: string,
  req: { source_session_id: string; target_session_id: string },
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(
    `${API}/${projectId}/workspace/snapshots/${snapshotId}/clone`,
    {
      method: 'POST',
      body: req,
      timeoutMs: 120_000,
      label: 'Snapshot clone error',
    },
  );
}

export async function deleteWorkspaceSnapshot(
  projectId: string,
  snapshotId: string,
  sessionId: string,
): Promise<{ status: string; snapshot_id: string; home: string }> {
  const qs = new URLSearchParams({ session_id: sessionId });
  const deleted = await apiFetch<{ status: string; snapshot_id: string; home: string }>(
    `${API}/${projectId}/workspace/snapshots/${snapshotId}?${qs.toString()}`,
    {
      method: 'DELETE',
      label: 'Snapshot delete error',
    },
  );
  invalidateCache(`${API}/${projectId}/workspace/snapshots`);
  return deleted;
}

// ── quality (audit exists in project.ts; repair + richer report here) ──────

export interface QualityIssueSummary {
  info: number;
  warning: number;
  error: number;
  blocking: number;
  [key: string]: number | string;
}

/** Full audit report — extends project.ts QualityReport with truncation. */
export interface SpatialQualityReport {
  dataset_id: string;
  total_features: number;
  issue_summary: QualityIssueSummary | Record<string, number>;
  overall_status: 'passed' | 'warning' | 'blocking' | string;
  truncated: boolean;
  truncated_count: number;
  truncation_details?: Record<string, unknown>;
  issues: Array<{
    dimension: string;
    code: string;
    level: string;
    message: string;
    feature_index?: number;
    details?: Record<string, unknown>;
  }>;
}

export interface RepairRequest {
  geojson: Record<string, unknown>;
  operations?: string[];
  session_id?: string;
  source_ref?: string;
  dataset_id?: string;
  issue_codes?: string[];
}

export interface RepairResponse {
  project_id: string;
  operations_applied: string[];
  ops_evidence: Record<string, unknown>;
  repair_logs: Array<Record<string, unknown>>;
  logs_count: number;
  feature_count: number;
  feature_count_before: number;
  repaired_ref?: string | null;
  ref_registration_error?: string | null;
  repair_evidence: Record<string, unknown>;
  lineage_status: 'recorded' | 'skipped' | 'dataset_not_found' | 'error' | string;
  lineage_artifact_id?: string | null;
  lineage_error?: string | null;
  repaired_geojson_preview?: Record<string, unknown> | null;
}

export async function auditSpatialQuality(
  projectId: string,
  geojson: Record<string, unknown>,
  opts?: { crs?: string; signal?: AbortSignal },
): Promise<SpatialQualityReport> {
  const qs = new URLSearchParams();
  if (opts?.crs) qs.set('crs', opts.crs);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch<SpatialQualityReport>(`${API}/${projectId}/quality-audit${suffix}`, {
    method: 'POST',
    body: { geojson },
    timeoutMs: 60_000,
    signal: opts?.signal,
    label: 'Quality audit error',
  });
}

export async function repairQuality(
  projectId: string,
  req: RepairRequest,
  opts?: { signal?: AbortSignal },
): Promise<RepairResponse> {
  return apiFetch<RepairResponse>(`${API}/${projectId}/repair`, {
    method: 'POST',
    body: req,
    timeoutMs: 120_000,
    signal: opts?.signal,
    label: 'Quality repair error',
  });
}

// ── data-usage / data-gc ────────────────────────────────────────────────────

export interface DataUsageResponse {
  project_id: string;
  usage: { bytes: number; artifact_count: number; revision_bytes: number };
  limits: {
    max_bytes: number;
    max_artifact_count: number;
    max_revision_bytes_per_artifact: number;
  };
  quota: { allowed: boolean; reason?: string | null };
  retention: {
    policy: Record<string, unknown>;
    upcoming_candidates: number;
    upcoming_candidate_blobs: number;
  };
}

export interface GcPlanResponse {
  project_id: string;
  scoped_to_project: boolean;
  retention: {
    policy: Record<string, unknown>;
    disabled: boolean;
    candidate_revision_count: number;
    candidate_blob_count: number;
    candidate_blob_bytes: number;
    protected_counts: Record<string, number>;
    protection_scan_truncated: boolean;
    candidate_revisions: Array<{
      artifact_id: string;
      revision_no: number;
      age_days: number;
      byte_size: number;
    }>;
    candidate_blobs: Array<{ sha_prefix: string; byte_size: number }>;
  };
  promotion_store_gc: {
    scoped_to_project: boolean;
    grace_hours?: number | null;
    deletable_count: number;
    deletable_bytes: number;
    deletable: Array<{ sha_prefix: string; bytes: number }>;
  };
}

export interface GcExecuteResponse {
  project_id: string;
  retention: {
    deleted_revisions: Array<Record<string, unknown>>;
    deleted_revision_count: number;
    deleted_blobs: Array<Record<string, unknown>>;
    deleted_blob_count: number;
    bytes_freed: number;
    skipped_protected_count: number;
    skipped_protected: Array<Record<string, unknown>>;
  };
  orphan_revisions: { deleted_count: number; deleted_revision_ids: string[] };
  skipped_protected: Array<{ key: string; reason: string }>;
}

export async function fetchDataUsage(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<DataUsageResponse> {
  const result = await fastGet<DataUsageResponse>(`${API}/${projectId}/data-usage`, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: 'Data usage error',
  });
  return result.data;
}

export async function planDataGc(
  projectId: string,
  opts?: { signal?: AbortSignal },
): Promise<GcPlanResponse> {
  return apiFetch<GcPlanResponse>(`${API}/${projectId}/data-gc/plan`, {
    method: 'POST',
    timeoutMs: 120_000,
    signal: opts?.signal,
    label: 'Data gc plan error',
  });
}

/** Backend rejects confirm≠true with 400 — the dialog must hard-code true. */
export async function executeDataGc(
  projectId: string,
  opts?: { signal?: AbortSignal },
): Promise<GcExecuteResponse> {
  const result = await apiFetch<GcExecuteResponse>(`${API}/${projectId}/data-gc/execute`, {
    method: 'POST',
    body: { confirm: true },
    timeoutMs: 300_000,
    signal: opts?.signal,
    label: 'Data gc execute error',
  });
  invalidateCache(`${API}/${projectId}/data-usage`);
  return result;
}

// ── cross-family aggregation helpers (read-only) ───────────────────────────

/** `GET /api/v1/data-fabric/catalog/{item_id}/preview` — bounded sample. */
export interface CatalogPreviewResponse {
  dataset_id: string;
  features: Array<Record<string, unknown>>;
  total_count: number;
  schema_info: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
}

/**
 * Auth-required; the server fetches from the remote source. 413 = too large
 * (retry smaller), 502 = source unreachable. `itemId` comes from the
 * dataset's `source_ref` when it references a fabric catalog item.
 */
export async function fetchCatalogItemPreview(
  itemId: string,
  opts?: { limit?: number; signal?: AbortSignal },
): Promise<CatalogPreviewResponse> {
  const qs = new URLSearchParams({ limit: String(opts?.limit ?? 50) });
  return apiFetch<CatalogPreviewResponse>(
    `${FABRIC_PREVIEW}/${encodeURIComponent(itemId)}/preview?${qs.toString()}`,
    {
      timeoutMs: 60_000,
      signal: opts?.signal,
      label: 'Catalog preview error',
    },
  );
}
