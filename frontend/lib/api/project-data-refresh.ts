/**
 * G10 项目数据刷新 · 前端 API client（手写 typed 模块，沿用共享 transport）。
 *
 * 所有写操作都经过 apiFetch（非幂等 POST 不自动重试），GET 走 fastGet
 * （in-flight dedup + 5s LRU）；写后 invalidateCache 保证 tab 切换不读陈旧值。
 * 服务端返回的是**有界摘要**：大 FeatureCollection / 大表从不进模型上下文，
 * UI 只展示 counts、证据级别、完整/截断状态与警告。
 */
import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const API = '/api/v1/projects';

const base = (projectId: string) =>
  `${API}/${encodeURIComponent(projectId)}/data-refresh`;

export type SourceState =
  | 'pinned' | 'refreshable' | 'ephemeral' | 'missing' | 'version_unknown';
export type EvidenceLevel =
  | 'content_exact' | 'revision_token' | 'timestamp_only' | 'none';
export type ChangeEvidence = 'exact' | 'conservative' | 'unknown' | 'unverified';

export interface RefreshSourceItem {
  logical_id: string;
  display_name: string;
  kind: string;
  state: SourceState;
  evidence: EvidenceLevel;
  baseline_version: string;
  current_version: string;
  crs: string;
  bbox: number[] | null;
  verdict: 'unchanged' | 'changed' | 'missing' | 'unverifiable';
  note: string;
}

export interface RefreshInventory {
  items: RefreshSourceItem[];
  truncated: boolean;
  warnings: string[];
  fingerprint: string;
}

export interface CheckReceipt {
  project_id: string;
  checked_at: number;
  sources_total: number;
  changed: number;
  unchanged: number;
  missing: number;
  unverifiable: number;
  inventory: RefreshInventory;
  stale: boolean;
  warnings: string[];
  truncated: boolean;
}

export interface FeatureCounts {
  added: number;
  removed: number;
  attribute_changed: number;
  geometry_changed: number;
}

export interface RefreshDiff {
  logical_id: string;
  change_class: string;
  evidence: ChangeEvidence;
  alignment: string;
  exact_counts: FeatureCounts | null;
  fields_added: string[];
  fields_removed: string[];
  fields_type_changed: string[];
  fields_unit_changed: string[];
  crs_changed: boolean;
  bbox_changed: boolean;
  temporal_changed: boolean;
  denominator_changed: boolean;
  raster_tiles_changed: string[];
  expanded_dependencies: string[];
  warnings: string[];
  truncated: boolean;
  reason: string;
}

export interface RefreshPlan {
  recompute: string[];
  reuse: string[];
  decisions: Record<string, string>;
  conservative: boolean;
  style_only: boolean;
  preserved_user_edits: string[];
  conflicts: string[];
  estimated_budget: Record<string, unknown>;
  warnings: string[];
  proposal_fingerprint: string;
}

export interface RefreshProposal {
  proposal_id: string;
  project_id: string;
  session_id: string;
  status: string;
  created_at: number;
  mapspec_revision: number;
  proposal_fingerprint: string;
  source_versions: Record<string, string>;
  inventory: RefreshInventory;
  diffs: RefreshDiff[];
  plan: RefreshPlan;
  warnings: string[];
  adopted_revision?: number | null;
}

export interface RefreshCandidate {
  candidate_id: string;
  proposal_id: string;
  status: string;
  attempts: number;
  progress: Array<Record<string, unknown>>;
  merge_preserved: string[];
  merge_conflicts: Array<Record<string, unknown>>;
  hard_semantic_errors: string[];
  adoptable: boolean;
  last_error: string;
}

export interface ExecuteReceipt {
  candidate: RefreshCandidate | null;
  replayed: boolean;
  conflicts: Array<Record<string, unknown>>;
  preserved: string[];
  receipt: {
    tool_execution?: string;
    task_execution?: string;
    map_observation?: string;
    version_adoption?: string;
    note?: string;
  };
}

export interface RefreshCompare {
  proposal_id: string;
  candidate_status: string;
  candidate_available: boolean;
  diff_counts: Array<{
    logical_id: string;
    change_class: string;
    evidence: ChangeEvidence;
    counts: FeatureCounts | null;
  }>;
  recompute: string[];
  reuse: string[];
  preserved_user_edits: string[];
  conflicts: Array<Record<string, unknown>>;
  hard_semantic_errors: string[];
  adoptable: boolean;
  truncated: boolean;
  warnings: string[];
}

export interface AdoptionReceipt {
  proposal_id: string;
  action: 'adopt' | 'discard' | 'rollback';
  status: string;
  tool_execution: string;
  task_execution: string;
  map_observation: string;
  version_adoption: string;
  revision_before: number;
  revision_after: number;
  warnings: string[];
  replayed: boolean;
}

export interface RefreshStatus {
  proposal_id: string;
  proposal_status: string;
  proposal_fingerprint: string;
  mapspec_revision: number;
  candidate_status: string;
  candidate_attempts: number;
  progress: Array<Record<string, unknown>>;
  adopted_revision: number | null;
  recoverable: boolean;
}

export interface ProposalSummary {
  proposal_id: string;
  status: string;
  created_at: number;
  proposal_fingerprint: string;
  mapspec_revision: number;
  sources: number;
  recompute_nodes: number;
  adopted_revision: number | null;
}

function invalidate(projectId: string) {
  invalidateCache(`${base(projectId)}/`);
}

export function checkProjectSourceUpdates(
  projectId: string,
  sessionId: string,
  establishBaseline = true,
  signal?: AbortSignal,
): Promise<CheckReceipt> {
  return apiFetch<CheckReceipt>(`${base(projectId)}/check`, {
    method: 'POST',
    body: { session_id: sessionId, establish_baseline: establishBaseline },
    signal,
    label: 'Check project source updates',
  }).then((r) => {
    invalidate(projectId);
    return r;
  });
}

export function listRefreshSources(
  projectId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<RefreshInventory> {
  return fastGet<RefreshInventory>(`${base(projectId)}/sources`, {
    params: { session_id: sessionId },
    signal,
    label: 'List project source versions',
  }).then((r) => r.data);
}

export interface PreviewOptions {
  styleOnly?: boolean;
  dag?: { nodes: Array<{ node_id: string }>; edges: Array<{ from: string; to: string }> };
  maxFeatures?: number;
  maxBytes?: number;
  signal?: AbortSignal;
}

export function previewProjectDataRefresh(
  projectId: string,
  sessionId: string,
  opts: PreviewOptions = {},
): Promise<RefreshProposal> {
  return apiFetch<RefreshProposal>(`${base(projectId)}/preview`, {
    method: 'POST',
    body: {
      session_id: sessionId,
      style_only: opts.styleOnly ?? false,
      ...(opts.dag ? { dag: opts.dag } : {}),
      ...(opts.maxFeatures ? { max_features: opts.maxFeatures } : {}),
      ...(opts.maxBytes ? { max_bytes: opts.maxBytes } : {}),
    },
    signal: opts.signal,
    label: 'Preview project data refresh',
  }).then((r) => {
    invalidate(projectId);
    return r;
  });
}

export function listRefreshProposals(
  projectId: string,
  signal?: AbortSignal,
): Promise<{ project_id: string; count: number; items: ProposalSummary[] }> {
  return fastGet<{ project_id: string; count: number; items: ProposalSummary[] }>(
    `${base(projectId)}/proposals`,
    { signal, label: 'List refresh proposals' },
  ).then((r) => r.data);
}

export function getRefreshProposal(
  projectId: string,
  proposalId: string,
  signal?: AbortSignal,
): Promise<RefreshProposal> {
  return fastGet<RefreshProposal>(`${base(projectId)}/proposals/${encodeURIComponent(proposalId)}`, {
    signal, label: 'Get refresh proposal',
  }).then((r) => r.data);
}

export function getRefreshStatus(
  projectId: string,
  proposalId: string,
  signal?: AbortSignal,
): Promise<RefreshStatus> {
  return fastGet<RefreshStatus>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/status`,
    { signal, label: 'Get refresh status' },
  ).then((r) => r.data);
}

export interface RefreshExplanation {
  stale: boolean;
  reasons: string[];
  affected_nodes: string[];
  conservative: boolean;
  unverifiable: string[];
  warnings: string[];
  truncated: boolean;
}

export function explainRefresh(
  projectId: string,
  proposalId: string,
  signal?: AbortSignal,
): Promise<RefreshExplanation> {
  return fastGet<RefreshExplanation>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/explain`,
    { signal, label: 'Explain stale map product' },
  ).then((r) => r.data);
}

export function executeRefreshCandidate(
  projectId: string,
  proposalId: string,
  cancel = false,
): Promise<ExecuteReceipt> {
  return apiFetch<ExecuteReceipt>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/execute`,
    { method: 'POST', body: { cancel }, label: 'Execute refresh candidate' },
  ).then((r) => {
    invalidate(projectId);
    return r;
  });
}

export function compareRefreshCandidate(
  projectId: string,
  proposalId: string,
  signal?: AbortSignal,
): Promise<RefreshCompare> {
  return fastGet<RefreshCompare>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/compare`,
    { signal, label: 'Compare refresh candidate' },
  ).then((r) => r.data);
}

export function adoptRefreshCandidate(
  projectId: string,
  proposalId: string,
  anchors: { expectedRevision?: number; proposalFingerprint?: string } = {},
): Promise<AdoptionReceipt> {
  return apiFetch<AdoptionReceipt>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/adopt`,
    {
      method: 'POST',
      body: {
        ...(anchors.expectedRevision != null
          ? { expected_revision: anchors.expectedRevision } : {}),
        ...(anchors.proposalFingerprint
          ? { proposal_fingerprint: anchors.proposalFingerprint } : {}),
      },
      label: 'Adopt refresh candidate',
    },
  ).then((r) => {
    invalidate(projectId);
    return r;
  });
}

export function discardRefreshCandidate(
  projectId: string,
  proposalId: string,
): Promise<AdoptionReceipt> {
  return apiFetch<AdoptionReceipt>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/discard`,
    { method: 'POST', body: {}, label: 'Discard refresh candidate' },
  ).then((r) => {
    invalidate(projectId);
    return r;
  });
}

export function rollbackRefreshCandidate(
  projectId: string,
  proposalId: string,
): Promise<AdoptionReceipt> {
  return apiFetch<AdoptionReceipt>(
    `${base(projectId)}/proposals/${encodeURIComponent(proposalId)}/rollback`,
    { method: 'POST', body: {}, label: 'Roll back refresh adoption' },
  ).then((r) => {
    invalidate(projectId);
    return r;
  });
}
