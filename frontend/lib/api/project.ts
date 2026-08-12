/**
 * Project Workspace, Workflow, Quality & Lineage Frontend API Client
 *
 * All requests flow through the shared transport (apiFetch) for typed errors,
 * request correlation, abort propagation, and timeout. GETs go through the
 * Fast Path (in-flight dedup + 5s LRU) so parallel mounts / tab switches
 * collapse to a single roundtrip.
 *
 * List endpoints return a Page envelope (F-FE-SD). Helpers unwrap `items`
 * so callers never iterate a Page as if it were an array. Artifact.crs is
 * `string | null` — unknown CRS is null, never a fabricated EPSG:4326.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const API = '/api/v1/projects';

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface Project {
  id: string;
  org_id?: number;
  owner_id?: string;
  name: string;
  description?: string;
  status: string;
  metadata_json?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProjectDataset {
  id: string;
  project_id: string;
  name: string;
  source_type: string;
  source_ref?: string | null;
  schema_profile?: Record<string, unknown>;
  /** Dataset CRS may be missing; do not default it. */
  crs: string | null;
  quality_status: string;
  version_fingerprint?: string | null;
  created_at: string;
}

export interface WorkflowStepSpec {
  step_id: string;
  tool_name: string;
  args_template?: Record<string, unknown>;
  input_bindings?: Record<string, string>;
  dependencies?: string[];
}

/** Slim list row — graph_spec lives on the save/detail payload, not the list. */
export interface WorkflowSummary {
  id: string;
  project_id: string;
  name: string;
  description?: string;
  version: number;
  step_count: number;
  created_at: string;
  updated_at: string;
}

/** Full workflow (POST save). Kept for callers that still hold a recipe. */
export interface Workflow extends WorkflowSummary {
  graph_spec?: { steps: WorkflowStepSpec[] };
  inputs_schema?: Record<string, unknown>;
  current_revision_id?: string | null;
}

export type WorkflowRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface WorkflowRunSummary {
  id: string;
  workflow_id: string;
  workflow_version: number;
  status: WorkflowRunStatus | string;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  created_at: string;
}

export interface RunManifestStep {
  step_id?: string;
  tool_name?: string;
  tool_version?: string;
  status?: string;
  args?: Record<string, unknown>;
}

export interface RunManifestArtifact {
  id?: string;
  producing_step?: string;
  artifact_type?: string;
  format?: string | null;
  /** Truthful CRS; null/omitted means unknown — never invent EPSG:4326. */
  crs?: string | null;
  content_fingerprint?: string | null;
  storage_ref?: string | null;
}

export interface RunManifest {
  workflow_revision_id?: string | null;
  graph_fingerprint?: string | null;
  inputs?: Record<string, unknown>;
  input_dataset_fingerprints?: Record<string, string>;
  steps?: RunManifestStep[];
  tool_versions?: Record<string, string>;
  artifacts?: RunManifestArtifact[];
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  project_id?: string | null;
  workflow_revision_id?: string | null;
  input_bindings: Record<string, unknown>;
  input_dataset_fingerprints: Record<string, unknown>;
  execution_trace: Array<Record<string, unknown>>;
  outputs: Record<string, unknown>;
  cost_perf_summary: Record<string, unknown>;
  completed_steps: string[];
  run_manifest?: RunManifest | null;
  run_fingerprint?: string | null;
}

/** @deprecated Use WorkflowRunDetail. Kept so existing tests compile. */
export type WorkflowRun = WorkflowRunDetail;

export interface WorkflowRevisionSummary {
  id: string;
  workflow_id: string;
  revision_no: number;
  graph_fingerprint: string;
  created_at: string;
}

export interface WorkflowRevisionDetail extends WorkflowRevisionSummary {
  inputs_schema?: Record<string, unknown> | null;
  created_by?: string | null;
}

export interface Artifact {
  id: string;
  project_id: string;
  name: string;
  artifact_type: string;
  format?: string | null;
  /** Unknown CRS is null. Do not default to EPSG:4326. */
  crs: string | null;
  storage_ref?: string | null;
  content_fingerprint?: string | null;
  created_at: string;
}

export interface QualityReport {
  dataset_id: string;
  total_features: number;
  overall_status: 'passed' | 'warning' | 'blocking';
  issue_summary: Record<string, number>;
  issues: Array<{
    dimension: string;
    code: string;
    level: string;
    message: string;
    feature_index?: number;
    details?: Record<string, unknown>;
  }>;
}

export type ReplayMode = 'exact' | 'latest';

export interface DictDiff {
  run_a?: Record<string, unknown>;
  run_b?: Record<string, unknown>;
  diff_keys?: string[];
}

export interface RunComparison {
  run_a_id: string;
  run_b_id: string;
  revision: {
    run_a_revision?: string | null;
    run_b_revision?: string | null;
    run_a_graph_fingerprint?: string | null;
    run_b_graph_fingerprint?: string | null;
    graph_same?: boolean;
  };
  inputs_changed: DictDiff;
  dataset_versions_changed: DictDiff;
  tool_versions_changed: Record<string, [unknown, unknown] | unknown>;
  params_changed: Record<string, { tool_a?: string; tool_b?: string; args_diff?: string[] }>;
  output_artifacts_changed: {
    run_a_status?: string;
    run_b_status?: string;
    run_a_artifact_count?: number;
    run_b_artifact_count?: number;
    run_a_fingerprints?: string[];
    run_b_fingerprints?: string[];
  };
  metrics_changed: Record<string, unknown>;
  warnings_changed: Record<string, unknown>;
  run_fingerprint: {
    run_a?: string | null;
    run_b?: string | null;
    same: boolean;
  };
}

export interface LineageParent {
  lineage_id: string;
  artifact_id: string;
  parent_artifact_id: string;
  producing_tool?: string | null;
  tool_version?: string | null;
  workflow_run_id?: string | null;
  parameters?: Record<string, unknown> | null;
  source_dataset_id?: string | null;
  source_dataset_fingerprint?: string | null;
  depth?: number;
  created_at?: string | null;
}

export interface LineageConsumer {
  lineage_id: string;
  consumer_artifact_id: string;
  parent_artifact_id: string | null;
  producing_tool?: string | null;
  tool_version?: string | null;
  workflow_run_id?: string | null;
  parameters?: Record<string, unknown> | null;
  depth?: number;
  created_at?: string | null;
}

export interface LineageGraph {
  artifact_id: string;
  parents: LineageParent[];
  consumers: LineageConsumer[];
}

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

export async function fetchProjects(opts?: {
  forceRefresh?: boolean;
  signal?: AbortSignal;
  limit?: number;
  offset?: number;
}): Promise<Project[]> {
  const result = await fastGet<Page<Project> | Project[]>(API, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit, offset: opts?.offset },
    label: 'Project list error',
  });
  return asPage(result.data).items;
}

export async function createProject(name: string, description?: string): Promise<Project> {
  const project = await apiFetch<Project>(API, {
    method: 'POST',
    body: { name, description },
    label: 'Project create error',
  });
  invalidateCache(API);
  return project;
}

export async function fetchProjectDatasets(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; limit?: number; offset?: number },
): Promise<ProjectDataset[]> {
  const path = `${API}/${projectId}/datasets`;
  const result = await fastGet<Page<ProjectDataset> | ProjectDataset[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit, offset: opts?.offset },
    label: 'Project datasets error',
  });
  return asPage(result.data).items;
}

export async function fetchProjectWorkflows(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; limit?: number; offset?: number },
): Promise<WorkflowSummary[]> {
  const path = `${API}/${projectId}/workflows`;
  const result = await fastGet<Page<WorkflowSummary> | WorkflowSummary[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit, offset: opts?.offset },
    label: 'Project workflows error',
  });
  return asPage(result.data).items;
}

export async function fetchWorkflowRuns(
  projectId: string,
  opts?: {
    workflowId?: string;
    forceRefresh?: boolean;
    signal?: AbortSignal;
    limit?: number;
    offset?: number;
  },
): Promise<Page<WorkflowRunSummary>> {
  const path = `${API}/${projectId}/runs`;
  const result = await fastGet<Page<WorkflowRunSummary> | WorkflowRunSummary[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: {
      workflow_id: opts?.workflowId,
      limit: opts?.limit ?? 50,
      offset: opts?.offset ?? 0,
    },
    label: 'Workflow runs error',
  });
  return asPage(result.data);
}

export async function fetchWorkflowRun(
  projectId: string,
  runId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<WorkflowRunDetail> {
  const path = `${API}/${projectId}/runs/${runId}`;
  const result = await fastGet<WorkflowRunDetail>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    ttlMs: 0,
    label: 'Workflow run detail error',
  });
  return result.data;
}

export async function fetchWorkflowRevisions(
  projectId: string,
  workflowId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; limit?: number; offset?: number },
): Promise<Page<WorkflowRevisionSummary>> {
  const path = `${API}/${projectId}/workflows/${workflowId}/revisions`;
  const result = await fastGet<Page<WorkflowRevisionSummary> | WorkflowRevisionSummary[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    params: { limit: opts?.limit ?? 50, offset: opts?.offset ?? 0 },
    label: 'Workflow revisions error',
  });
  return asPage(result.data);
}

export async function runWorkflow(
  projectId: string,
  workflowId: string,
  inputBindings: Record<string, unknown> = {},
): Promise<WorkflowRunDetail> {
  const run = await apiFetch<WorkflowRunDetail>(
    `${API}/${projectId}/workflows/${workflowId}/run`,
    {
      method: 'POST',
      body: { input_bindings: inputBindings },
      timeoutMs: 120_000,
      label: 'Workflow run error',
    },
  );
  invalidateWorkflowCaches(projectId);
  return run;
}

export async function replayWorkflowRun(
  projectId: string,
  runId: string,
  mode: ReplayMode,
): Promise<WorkflowRunDetail> {
  const run = await apiFetch<WorkflowRunDetail>(`${API}/${projectId}/runs/${runId}/replay`, {
    method: 'POST',
    body: { mode },
    timeoutMs: 120_000,
    label: 'Workflow replay error',
  });
  invalidateWorkflowCaches(projectId);
  return run;
}

export async function resumeWorkflowRun(
  projectId: string,
  runId: string,
): Promise<WorkflowRunDetail> {
  const run = await apiFetch<WorkflowRunDetail>(`${API}/${projectId}/runs/${runId}/resume`, {
    method: 'POST',
    body: { allow_rerun: false },
    timeoutMs: 120_000,
    label: 'Workflow resume error',
  });
  invalidateWorkflowCaches(projectId);
  return run;
}

/** Compare two runs. POST with query params (backend contract). */
export async function fetchRunComparison(
  projectId: string,
  runAId: string,
  runBId: string,
  opts?: { signal?: AbortSignal },
): Promise<RunComparison> {
  const qs = new URLSearchParams({ run_a_id: runAId, run_b_id: runBId });
  return apiFetch<RunComparison>(`${API}/${projectId}/runs/compare?${qs.toString()}`, {
    method: 'POST',
    signal: opts?.signal,
    label: 'Workflow compare error',
  });
}

export async function fetchArtifactLineage(
  artifactId: string,
  opts?: { signal?: AbortSignal; forceRefresh?: boolean },
): Promise<LineageGraph> {
  const path = `${API}/artifacts/${artifactId}/lineage`;
  const result = await fastGet<LineageGraph>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    ttlMs: 5_000,
    label: 'Artifact lineage error',
  });
  return result.data;
}

export async function auditQuality(
  projectId: string,
  geojson: Record<string, unknown>,
): Promise<QualityReport> {
  return apiFetch<QualityReport>(`${API}/${projectId}/quality-audit`, {
    method: 'POST',
    body: { geojson },
    timeoutMs: 60_000,
    label: 'Quality audit error',
  });
}

export function invalidateProjectRunCaches(projectId: string): void {
  invalidateCache(`${API}/${projectId}/workflows`);
  invalidateCache(`${API}/${projectId}/runs`);
}

function invalidateWorkflowCaches(projectId: string): void {
  invalidateProjectRunCaches(projectId);
}

export { fetchRunComparison as compareRuns };
