/**
 * Project Workspace, Workflow, Quality & Lineage Frontend API Client
 */

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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '/api/v1';

export async function fetchProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`);
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function createProject(name: string, description?: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  });
  if (!res.ok) throw new Error('Failed to create project');
  return res.json();
}

export async function fetchProjectDatasets(projectId: string): Promise<ProjectDataset[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/datasets`);
  if (!res.ok) throw new Error('Failed to fetch project datasets');
  return res.json();
}

export async function fetchProjectWorkflows(projectId: string): Promise<Workflow[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/workflows`);
  if (!res.ok) throw new Error('Failed to fetch project workflows');
  return res.json();
}

export async function runWorkflow(
  projectId: string,
  workflowId: string,
  inputBindings: Record<string, any> = {}
): Promise<WorkflowRun> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/workflows/${workflowId}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_bindings: inputBindings }),
  });
  if (!res.ok) throw new Error('Failed to run workflow');
  return res.json();
}

export async function auditQuality(projectId: string, geojson: Record<string, any>): Promise<QualityReport> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/quality-audit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ geojson }),
  });
  if (!res.ok) throw new Error('Failed to audit quality');
  return res.json();
}
