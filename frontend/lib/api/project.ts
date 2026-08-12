/**
 * Project Workspace, Workflow, Quality & Lineage Frontend API Client
 *
 * All requests flow through the shared transport (apiFetch) for typed errors,
 * request correlation, abort propagation, and timeout. GETs go through the
 * Fast Path (in-flight dedup + 5s LRU) so parallel mounts / tab switches
 * collapse to a single roundtrip.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

export interface Project {
  id: string;
  org_id?: number;
  owner_id?: string;
  name: string;
  description?: string;
  status: string;
  metadata_json: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface ProjectDataset {
  id: string;
  project_id: string;
  name: string;
  source_type: string;
  source_ref: string;
  schema_profile: Record<string, any>;
  crs: string;
  quality_status: string;
  version_fingerprint: string;
  created_at: string;
}

export interface WorkflowStepSpec {
  step_id: string;
  tool_name: string;
  args_template?: Record<string, any>;
  input_bindings?: Record<string, string>;
  dependencies?: string[];
}

export interface Workflow {
  id: string;
  project_id: string;
  name: string;
  description?: string;
  version: number;
  graph_spec: { steps: WorkflowStepSpec[] };
  inputs_schema: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version: number;
  input_bindings: Record<string, any>;
  status: string;
  started_at?: string;
  completed_at?: string;
  execution_trace: Array<Record<string, any>>;
  outputs: Record<string, any>;
  error_message?: string;
  cost_perf_summary: Record<string, any>;
  created_at: string;
}

export interface Artifact {
  id: string;
  project_id: string;
  name: string;
  artifact_type: string;
  format: string;
  crs: string;
  storage_ref?: string;
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
    details?: Record<string, any>;
  }>;
}

/** GET /projects — short-lived cached, dedupe across parallel mounts. */
export async function fetchProjects(opts?: {
  forceRefresh?: boolean;
  signal?: AbortSignal;
}): Promise<Project[]> {
  const result = await fastGet<Project[]>('/projects', {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: 'Project list error',
  });
  return result.data;
}

export async function createProject(name: string, description?: string): Promise<Project> {
  const project = await apiFetch<Project>('/projects', {
    method: 'POST',
    body: { name, description },
    label: 'Project create error',
  });
  // New project → bust the list cache so the next fetchProjects is fresh.
  invalidateCache('/projects');
  return project;
}

export async function fetchProjectDatasets(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<ProjectDataset[]> {
  const path = `/projects/${projectId}/datasets`;
  const result = await fastGet<ProjectDataset[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: 'Project datasets error',
  });
  return result.data;
}

export async function fetchProjectWorkflows(
  projectId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<Workflow[]> {
  const path = `/projects/${projectId}/workflows`;
  const result = await fastGet<Workflow[]>(path, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: 'Project workflows error',
  });
  return result.data;
}

export async function runWorkflow(
  projectId: string,
  workflowId: string,
  inputBindings: Record<string, any> = {}
): Promise<WorkflowRun> {
  const run = await apiFetch<WorkflowRun>(
    `/projects/${projectId}/workflows/${workflowId}/run`,
    {
      method: 'POST',
      body: { input_bindings: inputBindings },
      label: 'Workflow run error',
    }
  );
  // A new run invalidates the runs list cache and the workflow list cache.
  invalidateCache(`/projects/${projectId}/workflows`);
  invalidateCache(`/projects/${projectId}/runs`);
  return run;
}

export async function auditQuality(
  projectId: string,
  geojson: Record<string, any>
): Promise<QualityReport> {
  return apiFetch<QualityReport>(`/projects/${projectId}/quality-audit`, {
    method: 'POST',
    body: { geojson },
    timeoutMs: 60_000, // quality audit can take longer than the default 30s
    label: 'Quality audit error',
  });
}
