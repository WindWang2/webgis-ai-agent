/**
 * Scenario Harness API —— G05 情境化多方案候选工作台 client。
 *
 * 与后端 /api/v1/chat/sessions/{sid}/scenario 端点族一一对应；与 Pi 工具
 * （webgis_scenario_*）完全同链。诚实纪律：
 * - 状态来自服务端 receipts（candidate.state / AdoptionReceipt），无本地平行态；
 * - 不可比候选携带 incomparable_reason；影响分级原样透传
 *   （current / recompute_required / adopt_blocked / unresolvable）；
 * - 错误统一携带后端 typed error（RegistryError code）。
 */

import { apiFetch } from './transport';

export interface ScenarioLockView {
  lock_id: string;
  kind: 'layer_field' | 'component_field' | 'analysis_param';
  target_id: string;
  field_paths: string[];
  reason: string;
  created_at: string;
  value_digest: string;
}

export interface ScenarioCandidateView {
  candidate_id: string;
  title: string;
  hypothesis: string;
  branch_session_id: string;
  baseline_id: string;
  fork_revision: number;
  state:
    | 'draft'
    | 'running'
    | 'comparable'
    | 'adopted'
    | 'stale'
    | 'failed'
    | 'cancelled'
    | 'archived';
  delta: Array<{
    seq: number;
    kind: string;
    group: string;
    target_id: string;
    field_paths: string[];
    summary: string;
  }>;
  applied_seqs: number[];
  created_by: 'agent' | 'user';
  adoption_id: string;
  stale_reasons: string[];
  compare_digest: string;
}

export interface ScenarioOverviewView {
  schema_version: string;
  overview: {
    total: number;
    active: number;
    adopted: number;
    active_lock_count: number;
    baseline?: {
      baseline_id: string;
      mapspec_revision: number;
      mapspec_fingerprint: string;
      dataset_refs: string[];
      captured_at: string;
    };
    candidates: Array<Record<string, unknown>>;
  } | null;
  candidates: ScenarioCandidateView[];
  active_locks: ScenarioLockView[];
  corrupt_entries?: string[];
}

export interface ScenarioCompareView {
  schema_version: string;
  result: {
    session_id: string;
    baseline_id: string;
    baseline_revision: number;
    move: {
      moved: boolean;
      revision_from: number;
      revision_to: number;
      fingerprint_changed: boolean;
      dataset_changes: Array<{ ref_id: string; from: string; to: string }>;
      goal_changed: boolean;
    } | null;
    rows: Array<{
      candidate_id: string;
      layer_adds: string[];
      layer_removes: string[];
      style_fields: string[];
      component_changes: string[];
      metric_deltas: Record<string, number>;
      comparable: boolean;
      incomparable_reason: string;
    }>;
    impacts: Array<{
      candidate_id: string;
      level: 'current' | 'recompute_required' | 'adopt_blocked' | 'unresolvable';
      reasons: string[];
    }>;
    compare_digest: string;
  } | null;
  reason?: string;
}

export interface AdoptionReceiptView {
  adoption_id: string;
  candidate_id: string;
  base_revision: number;
  final_revision: number;
  selected_groups: string[];
  items: Array<{
    seq: number;
    kind: string;
    group: string;
    target_id: string;
    verdict:
      | 'applied'
      | 'user_wins_skip'
      | 'agent_conflict'
      | 'target_missing'
      | 'lock_blocked'
      | 'not_selected';
    detail: string;
  }>;
  applied_count: number;
  skipped_count: number;
  idempotency_key: string;
  pre_adopt_checkpoint_id: string;
}

const BASE = '/api/v1/chat/sessions';

export async function fetchScenarioOverview(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ScenarioOverviewView> {
  return apiFetch<ScenarioOverviewView>(`${BASE}/${sessionId}/scenario/overview`, { signal });
}

export async function fetchScenarioCompare(
  sessionId: string,
  candidateIds: string[],
  signal?: AbortSignal,
): Promise<ScenarioCompareView> {
  const qs = candidateIds.length ? `?candidate_ids=${encodeURIComponent(candidateIds.join(','))}` : '';
  return apiFetch<ScenarioCompareView>(`${BASE}/${sessionId}/scenario/compare${qs}`, { signal });
}

export async function proposeScenarioCandidate(
  sessionId: string,
  body: { title?: string; hypothesis?: string; intents?: Array<Record<string, unknown>> },
): Promise<{ candidate: ScenarioCandidateView }> {
  return apiFetch(`${BASE}/${sessionId}/scenario/candidates`, { method: 'POST', body });
}

export async function executeScenarioCandidate(
  sessionId: string,
  candidateId: string,
  intents: Array<Record<string, unknown>>,
): Promise<{ results: Array<Record<string, unknown>> }> {
  return apiFetch(`${BASE}/${sessionId}/scenario/candidates/${encodeURIComponent(candidateId)}/execute`, {
    method: 'POST',
    body: { intents },
  });
}

export async function adoptScenarioCandidate(
  sessionId: string,
  candidateId: string,
  selection: {
    mode: 'all' | 'groups' | 'seqs';
    groups?: string[];
    seqs?: number[];
    expected_revision?: number | null;
  },
): Promise<{ receipt: AdoptionReceiptView }> {
  return apiFetch(`${BASE}/${sessionId}/scenario/candidates/${encodeURIComponent(candidateId)}/adopt`, {
    method: 'POST',
    body: selection,
  });
}

export async function abandonScenarioCandidate(
  sessionId: string,
  candidateId: string,
  opts?: { hardDelete?: boolean; reason?: string },
): Promise<{ removed: boolean }> {
  const qs = new URLSearchParams();
  if (opts?.hardDelete !== undefined) qs.set('hard_delete', String(opts.hardDelete));
  if (opts?.reason) qs.set('reason', opts.reason);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch(
    `${BASE}/${sessionId}/scenario/candidates/${encodeURIComponent(candidateId)}${suffix}`,
    { method: 'DELETE' },
  );
}

export async function releaseScenarioLock(
  sessionId: string,
  lockId: string,
  reason?: string,
): Promise<{ lock: ScenarioLockView }> {
  const qs = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  return apiFetch(`${BASE}/${sessionId}/scenario/locks/${encodeURIComponent(lockId)}${qs}`, {
    method: 'DELETE',
  });
}
