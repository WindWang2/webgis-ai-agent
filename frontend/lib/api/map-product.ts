/**
 * Map Product version ledger frontend API client (ADR-0092 A6 + version
 * workspace).
 *
 * The version ledger is the single product truth — this client only READS
 * it (list/detail/diff) plus the shared rerun entry point. All requests
 * flow through the shared transport; GETs use the Fast Path (dedup + LRU)
 * so tab switches collapse to one roundtrip.
 */

import { apiFetch } from './transport';
import { fastGet } from './get-fast-path';
import type { Page } from './project';

const API = '/api/v1/projects';

/** Slim ledger row (list endpoint; compute_plan/diff live on detail). */
export interface MapProductVersionSummary {
  id: number;
  project_id: string;
  version_no: number;
  product_fingerprint: string;
  recipe_id?: string | null;
  workflow_run_id?: string | null;
  mapspec_revision?: number | null;
  created_at: string;
  /** ADR-0099 lifecycle（旧行缺省 null/undefined）。 */
  label?: string | null;
  lineage_kind?: MapProductLineageKind;
  parent_version_no?: number | null;
  snapshot_available?: boolean;
}

/** Full version row (detail endpoint). */
export interface MapProductVersionDetail extends MapProductVersionSummary {
  input_dataset_fingerprints: Record<string, string>;
  compute_plan: Array<{
    step_id: string;
    capability?: string;
    algorithm?: string;
    tool_name?: string;
    args?: Record<string, unknown>;
  }>;
  output_fingerprints: string[];
  artifact_ids: string[];
  mapspec_fingerprint?: string | null;
  diff_summary?: {
    vs_version_no?: number | null;
    data_changed: boolean;
    algorithm_changed: boolean;
    parameter_changed: boolean;
    style_changed: boolean;
    output_changed: boolean;
    analysis_recomputation_expected: boolean;
  } | null;
}

/** Pairwise diff (GET /{from}/diff/{to}). */
export interface MapProductVersionDiff {
  from_version_no: number;
  to_version_no: number;
  vs_version_no: number | null;
  data_changed: boolean;
  algorithm_changed: boolean;
  parameter_changed: boolean;
  style_changed: boolean;
  output_changed: boolean;
  /** True ⇒ data/algorithm/parameter changed ⇒ analysis tools must re-run. */
  analysis_recomputation_expected: boolean;
  details: {
    input_dataset_fingerprints: {
      from: Record<string, string>;
      to: Record<string, string>;
      changed_keys: string[];
    };
    algorithm_steps: Array<{ step_id: string; from: string | null; to: string | null }>;
    parameter_steps: Array<{
      step_id: string;
      from: Record<string, unknown> | string | null;
      to: Record<string, unknown> | string | null;
    }>;
    mapspec_fingerprint: { from: string | null; to: string | null };
    artifacts: { added: string[]; removed: string[]; unchanged_count: number };
    workflow_runs: { from: string | null; to: string | null };
  };
}

function asPage<T>(data: Page<T> | T[] | undefined): Page<T> {
  if (Array.isArray(data)) {
    return { items: data, total: data.length, limit: data.length, offset: 0, has_more: false };
  }
  return (
    data ?? { items: [], total: 0, limit: 0, offset: 0, has_more: false }
  );
}

export async function listMapProductVersions(
  projectId: string,
  opts: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<Page<MapProductVersionSummary>> {
  const result = await fastGet<Page<MapProductVersionSummary> | MapProductVersionSummary[]>(
    `${API}/${projectId}/map-products`,
    {
      params: { limit: opts.limit ?? 50, offset: opts.offset ?? 0 },
      signal: opts.signal,
      label: 'Map product versions error',
    },
  );
  return asPage(result.data);
}

export async function getMapProductVersion(
  projectId: string,
  versionNo: number,
  opts: { signal?: AbortSignal } = {},
): Promise<MapProductVersionDetail> {
  const result = await fastGet<MapProductVersionDetail>(
    `${API}/${projectId}/map-products/${versionNo}`,
    { signal: opts.signal, label: 'Map product version error' },
  );
  return result.data;
}

export async function diffMapProductVersions(
  projectId: string,
  fromVersionNo: number,
  toVersionNo: number,
  opts: { signal?: AbortSignal } = {},
): Promise<MapProductVersionDiff> {
  const result = await fastGet<MapProductVersionDiff>(
    `${API}/${projectId}/map-products/${fromVersionNo}/diff/${toVersionNo}`,
    { signal: opts.signal, label: 'Map product diff error' },
  );
  return result.data;
}

/** Shared rerun entry (existing workflow rerun_from_step API). */
export async function rerunWorkflowRunFromStep(
  projectId: string,
  runId: string,
  fromStep: string,
  opts: { timeoutMs?: number } = {},
): Promise<unknown> {
  return apiFetch(`${API}/${projectId}/runs/${runId}/rerun`, {
    method: 'POST',
    body: { from_step: fromStep },
    timeoutMs: opts.timeoutMs ?? 120_000,
    label: 'Map product rerun error',
  });
}

// ── Lifecycle V2（ADR-0099）──────────────────────────────────────────────────

/** Lineage badge vocabulary (fork/restore/merge/rerun/auto; null = linear). */
export type MapProductLineageKind =
  | 'linear'
  | 'fork'
  | 'restore'
  | 'merge'
  | 'rerun'
  | 'auto'
  | null;

export interface MapProductRestoreMode {
  mode: 'style_only' | 'full';
  available: boolean;
  note: string;
}

/** GET /{v}/open — read-only version inspection w/ honest restore modes. */
export interface MapProductVersionOpen {
  version_no: number;
  product_fingerprint: string;
  recipe_id: string | null;
  workflow_run_id: string | null;
  mapspec_fingerprint: string | null;
  mapspec_revision: number | null;
  lineage_kind: MapProductLineageKind;
  parent_version_no: number | null;
  label: string | null;
  created_at: string | null;
  diff_summary: MapProductVersionDetail['diff_summary'];
  snapshot_available: boolean;
  restore_modes: MapProductRestoreMode[];
  provenance: {
    input_dataset_fingerprints: Record<string, string>;
    plan_steps: number;
    artifact_count: number;
    output_fingerprints: number;
  };
}

export async function openMapProductVersion(
  projectId: string,
  versionNo: number,
): Promise<MapProductVersionOpen> {
  return apiFetch<MapProductVersionOpen>(
    `${API}/${projectId}/map-products/${versionNo}/open`,
    { label: 'Map product open error' },
  );
}

export interface MapProductRestoreResult {
  restored_version_no: number;
  source_version_no: number;
  mode: 'style_only' | 'full';
  mutation_revision?: number | null;
  warnings?: string[];
  style_only_proof?: {
    compute_identity_preserved: boolean;
    analysis_executed: boolean;
    note: string;
  };
  run_id?: string;
}

/** POST /{v}/restore — style_only applies presentation to a live session. */
export async function restoreMapProductVersion(
  projectId: string,
  versionNo: number,
  sessionId: string,
  mode: 'style_only' | 'full' = 'style_only',
): Promise<MapProductRestoreResult> {
  return apiFetch<MapProductRestoreResult>(
    `${API}/${projectId}/map-products/${versionNo}/restore`,
    {
      method: 'POST',
      body: { mode, session_id: sessionId },
      timeoutMs: 60_000,
      label: 'Map product restore error',
    },
  );
}

/** POST /{v}/fork — new lineage branch from a historical version. */
export async function forkMapProductVersion(
  projectId: string,
  versionNo: number,
  label?: string,
): Promise<MapProductVersionDetail> {
  return apiFetch<MapProductVersionDetail>(
    `${API}/${projectId}/map-products/${versionNo}/fork`,
    { method: 'POST', body: label ? { label } : {}, label: 'Map product fork error' },
  );
}

/** POST /merge — constrained dimension merge (style-only × analysis-only). */
export async function mergeMapProductVersions(
  projectId: string,
  fromVersionNo: number,
  toVersionNo: number,
  label?: string,
): Promise<MapProductVersionDetail> {
  return apiFetch<MapProductVersionDetail>(
    `${API}/${projectId}/map-products/merge`,
    {
      method: 'POST',
      body: { from_version_no: fromVersionNo, to_version_no: toVersionNo, ...(label ? { label } : {}) },
      label: 'Map product merge error',
    },
  );
}

/** POST /{v}/rerun — version-bound incremental rerun. */
export async function rerunMapProductVersion(
  projectId: string,
  versionNo: number,
): Promise<{ run_id: string; recorded_version_no: number; from_step: string | null }> {
  return apiFetch(
    `${API}/${projectId}/map-products/${versionNo}/rerun`,
    { method: 'POST', body: {}, timeoutMs: 120_000, label: 'Map product version rerun error' },
  );
}
