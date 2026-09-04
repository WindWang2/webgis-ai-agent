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
