/**
 * Topology Workbench API —— G31 跨图层拓扑工作台 client。
 *
 * 与后端 /api/v1/topology-workbench 端点族一一对应。计数诚实纪律：
 * findings total 是全量计数（非当前页）；status_counts 独立披露。
 */

import { apiFetch } from './transport';

const BASE = '/topology-workbench';

export type TopologySeverity = 'info' | 'warn' | 'error';
export type FindingStatus = 'open' | 'accepted_exception' | 'resolved' | 'dismissed';

export interface TopologyRule {
  rule_id: string;
  rule_type: string;
  severity: TopologySeverity;
  enabled: boolean;
  description?: string;
  params?: Record<string, number | string | boolean>;
}

export interface TopologyRuleset {
  id: string;
  name: string;
  project_id: string | null;
  session_id: string | null;
  current_version: number;
  digest: string;
  rules: TopologyRule[];
  created_by: string | null;
  created_at: string | null;
}

export type RoleBindingInput = {
  kind: 'session_ref' | 'project_artifact' | 'bbox';
  ref?: string;
  session_id?: string;
  project_id?: string;
  artifact_id?: string;
  revision_no?: number;
  bbox?: [number, number, number, number];
  scope?: { kind: 'bbox' | 'where' | 'ids'; bbox?: number[]; where?: unknown; ids?: string[] };
  selection_handle?: string;
};

export interface TopologyRunSummary {
  id: string;
  ruleset_id: string;
  ruleset_digest: string;
  ruleset_version: number;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  findings_total: number;
  findings_open: number;
  computation_crs: string | null;
  job_id: string | null;
  recheck_of_run_id: string | null;
  duration_ms: number;
  created_at: string | null;
  has_witness: boolean;
}

export interface RuleResultView {
  rule_id: string;
  rule_type: string;
  applicability: 'applicable' | 'blocked';
  blocked_code?: string;
  blocked_message?: string;
  input_completeness: 'full' | 'partial';
  computation: 'full' | 'partial';
  outcome: 'violations_found' | 'no_violations' | 'unknown' | '';
  counts?: Record<string, number>;
  notes?: string[];
  duration_ms: number;
}

export interface TopologyRunDetail extends TopologyRunSummary {
  rule_results: RuleResultView[];
  completeness: {
    all_skipped_or_unknown?: boolean;
    budget_truncated?: boolean;
    failure_code?: string;
    failure_message?: string;
    excluded_summary?: Record<string, { total: number; examined: number; excluded: Record<string, number> }>;
    recheck?: Record<string, unknown>;
  };
  bindings: Record<string, Record<string, unknown>>;
}

export interface TopologyFinding {
  id: string;
  run_id: string;
  rule_id: string;
  rule_type: string;
  finding_key: string;
  severity: TopologySeverity;
  status: FindingStatus;
  metric: { value?: number | null; threshold?: number; unit?: string; method?: string } | null;
  message: string;
  bbox: [number, number, number, number];
  geometry_type: string;
  resolved_by_run_id?: string | null;
  exception_id?: string | null;
}

export interface FindingLocator {
  role: string;
  kind: string;
  ref: string;
  content_revision?: number | null;
  identity: { kind: string; value: string; snapshot_fingerprint?: string };
  feature_index: number;
}

export interface FindingDetail extends TopologyFinding {
  locators: FindingLocator[];
  witness_digest: string;
  witness_index: number;
}

export interface FindingWitness {
  finding_id: string;
  witness: { type: string; properties?: Record<string, unknown>; geometry: Record<string, unknown> };
  witness_digest: string;
  stale: boolean;
  stale_reason?: string | null;
  locators: FindingLocator[];
}

export interface RepairCandidate {
  kind: string;
  description: string;
  target_locators: FindingLocator[];
  preview_available: boolean;
  ownership_choice_required: boolean;
  estimated_change?: { unit?: string; expected?: string };
  blocked_reason?: string | null;
}

export async function fetchRulesCatalog(): Promise<Array<Record<string, unknown>>> {
  const data = await apiFetch<{ rules: Array<Record<string, unknown>> }>(
    `${BASE}/rules/catalog`, { method: 'GET' },
  );
  return data.rules ?? [];
}

export async function fetchRulesets(params: { project_id?: string; limit?: number } = {}): Promise<{ items: TopologyRuleset[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.project_id) qs.set('project_id', params.project_id);
  if (params.limit) qs.set('limit', String(params.limit));
  const data = await apiFetch<{ items: TopologyRuleset[]; total: number }>(
    `${BASE}/rulesets${qs.size ? `?${qs}` : ''}`, { method: 'GET' },
  );
  return { items: data.items ?? [], total: data.total ?? 0 };
}

export async function createRuleset(body: {
  name: string; rules: TopologyRule[]; project_id?: string; session_id?: string;
}): Promise<TopologyRuleset> {
  const data = await apiFetch<{ ruleset: TopologyRuleset }>(
    `${BASE}/rulesets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  return data.ruleset;
}

export async function updateRuleset(rulesetId: string, body: {
  name?: string; rules: TopologyRule[];
}): Promise<TopologyRuleset> {
  const data = await apiFetch<{ ruleset: TopologyRuleset }>(
    `${BASE}/rulesets/${encodeURIComponent(rulesetId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  return data.ruleset;
}

export async function submitTopologyRun(body: {
  ruleset_id: string;
  bindings: Record<string, RoleBindingInput>;
  session_id?: string;
  project_id?: string;
}): Promise<{ run_id: string; job?: { job_id?: string; status?: string } }> {
  return apiFetch(
    `${BASE}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

export async function fetchRuns(params: {
  ruleset_id?: string; project_id?: string; session_id?: string; limit?: number;
} = {}): Promise<{ items: TopologyRunSummary[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.ruleset_id) qs.set('ruleset_id', params.ruleset_id);
  if (params.project_id) qs.set('project_id', params.project_id);
  if (params.session_id) qs.set('session_id', params.session_id);
  if (params.limit) qs.set('limit', String(params.limit));
  const data = await apiFetch<{ items: TopologyRunSummary[]; total: number }>(
    `${BASE}/runs${qs.size ? `?${qs}` : ''}`, { method: 'GET' },
  );
  return { items: data.items ?? [], total: data.total ?? 0 };
}

export async function fetchRun(runId: string): Promise<TopologyRunDetail> {
  const data = await apiFetch<{ run: TopologyRunDetail }>(
    `${BASE}/runs/${encodeURIComponent(runId)}`, { method: 'GET' },
  );
  return data.run;
}

export interface FindingsPage {
  items: TopologyFinding[];
  total: number;
  status_counts: Record<string, number>;
  has_more: boolean;
}

export async function fetchFindings(
  runId: string,
  filters: { rule_id?: string; severity?: string; status?: string; limit?: number; offset?: number } = {},
): Promise<FindingsPage> {
  const qs = new URLSearchParams();
  if (filters.rule_id) qs.set('rule_id', filters.rule_id);
  if (filters.severity) qs.set('severity', filters.severity);
  if (filters.status) qs.set('status', filters.status);
  qs.set('limit', String(filters.limit ?? 20));
  qs.set('offset', String(filters.offset ?? 0));
  const data = await apiFetch<FindingsPage>(
    `${BASE}/runs/${encodeURIComponent(runId)}/findings?${qs}`,
    { method: 'GET' },
  );
  return data;
}

export async function fetchFinding(findingId: string): Promise<FindingDetail> {
  const data = await apiFetch<{ finding: FindingDetail }>(
    `${BASE}/findings/${encodeURIComponent(findingId)}`, { method: 'GET' },
  );
  return data.finding;
}

export async function fetchFindingWitness(findingId: string): Promise<FindingWitness> {
  return apiFetch(
    `${BASE}/findings/${encodeURIComponent(findingId)}/witness`,
    { method: 'GET' },
  );
}

export async function acceptException(findingId: string, reason: string): Promise<{ id: string; status: string }> {
  const data = await apiFetch<{ exception: { id: string; status: string } }>(
    `${BASE}/findings/${encodeURIComponent(findingId)}/exception`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
  );
  return data.exception;
}

export async function revokeException(exceptionId: string): Promise<{ id: string; status: string }> {
  const data = await apiFetch<{ exception: { id: string; status: string } }>(
    `${BASE}/exceptions/${encodeURIComponent(exceptionId)}`, {
      method: 'DELETE',
    },
  );
  return data.exception;
}

export async function fetchRepairCandidates(findingId: string): Promise<{ candidates: RepairCandidate[]; note?: string }> {
  return apiFetch(
    `${BASE}/findings/${encodeURIComponent(findingId)}/repair`,
    { method: 'POST' },
  );
}

export async function recheckRun(runId: string): Promise<{
  recheck_run_id: string; status: string; resolved_count: number;
  persisted_count: number; new_run_findings_total: number; new_run_findings_open: number;
  digest_changed?: boolean;
}> {
  return apiFetch(
    `${BASE}/runs/${encodeURIComponent(runId)}/recheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
  );
}
