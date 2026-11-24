/**
 * Method Experiment API —— 方法实验工作台 client。
 *
 * 与后端 /api/v1/method-experiment 端点族一一对应（与
 * webgis_method_experiment_* 工具面同实现同契约）。诚实纪律：错误统一携带
 * 后端 typed `detail.reason_code + message`（MEXP_* 族）；推荐/采纳服务端
 * 说了算（abstained 弃权绝不伪造为推荐）；采纳只是记录决定，不改地图。
 */

import { apiFetch } from './transport';

const BASE = '/api/v1/method-experiment';

// ── inspect ─────────────────────────────────────────────────────────────

export interface MexpParamSpec {
  role: string;
  unit: string;
  default: number | string | null;
  grid: Array<number | string>;
  minimum: number | null;
  maximum: number | null;
  note: string;
}

export interface MexpCandidateCard {
  candidate_id: string;
  method_id: string;
  eval_tool: string;
  label_zh: string;
  sample_floor: number;
  param_specs: Record<string, MexpParamSpec>;
  metric_ids: string[];
  limitations: string[];
  default_variants: number;
}

export interface MexpFamilyCard {
  family_id: string;
  label_zh: string;
  protocol_kind: string;
  default_scheme: string;
  default_folds: number;
  default_metrics: string[];
  requires_value_field: boolean;
  extra_inputs: string[];
  comparability_notes: string[];
  candidates: MexpCandidateCard[];
}

export interface MexpBudgetDefaults {
  max_trials: number;
  max_trials_hard_cap: number;
  max_candidates: number;
  max_variants_per_candidate: number;
  cv_schemes: string[];
}

export interface MexpInspectRequest {
  query?: string;
  family_id?: string;
}

export interface MexpInspectResponse {
  query_matched_families: string[];
  resolved_family: string | null;
  family_vocab: string[];
  cards: MexpFamilyCard[];
  budget_defaults: MexpBudgetDefaults;
}

// ── plan ────────────────────────────────────────────────────────────────

export interface MexpPlanRequest {
  family_id: string;
  input_ref: string;
  value_field?: string;
  extra_inputs?: Record<string, string>;
  candidate_ids?: string[];
  scheme?: string;
  folds?: number;
  max_trials?: number;
  title?: string;
}

export interface MexpProtocolView {
  kind: string;
  family_id: string;
  scheme: string;
  folds: number;
  stability_runs: number;
  max_samples: number;
  metrics: string[];
  notes: string[];
  fingerprint: string;
}

export interface MexpBudgetView {
  max_trials: number;
  max_features: number;
  wall_hint_seconds_per_trial: number;
}

export interface MexpPlanCandidate {
  candidate_id: string;
  label_zh: string;
  method_id: string;
  eval_tool: string;
  variants: number;
  param_axes: Record<string, Array<number | string>>;
}

export interface MexpPlanTrial {
  trial_id: string;
  candidate_id: string;
  variant_index: number;
  params: Record<string, unknown>;
}

export interface MexpPlanView {
  experiment_id: string;
  title: string;
  family_id: string;
  protocol: MexpProtocolView;
  budget: MexpBudgetView;
  candidates: MexpPlanCandidate[];
  trials: MexpPlanTrial[];
  preflight_findings: string[];
  fingerprint: string;
}

export interface MexpPlanResponse {
  plan: MexpPlanView;
}

// ── run / status ────────────────────────────────────────────────────────

export interface MexpRunTrial {
  trial_id: string;
  candidate_id: string;
  state: string;
  metrics: Record<string, number>;
  reason_code: string;
}

export interface MexpRunResponse {
  experiment_id: string;
  status: string;
  counts: Record<string, number>;
  total_wall_ms: number;
  trials: MexpRunTrial[];
  run_log_tail: unknown[];
}

export interface MexpAdoptionView {
  candidate_id: string;
  trial_id: string;
  metric_snapshot: Record<string, number>;
  reason: string;
  received_at: number;
  plan_fingerprint: string;
}

export interface MexpStatusTrial {
  trial_id: string;
  candidate_id: string;
  variant_index: number;
  params: Record<string, unknown>;
  state: string;
  metrics: Record<string, number>;
  basis: Record<string, Record<string, unknown>>;
  reason_code: string;
  message: string;
  wall_ms: number;
  attempts: number;
}

export interface MexpStatusResponse {
  experiment_id: string;
  status: string;
  counts: Record<string, number>;
  total_wall_ms: number;
  adoption: MexpAdoptionView | null;
  trials: MexpStatusTrial[];
  run_log_tail: unknown[];
}

// ── compare ─────────────────────────────────────────────────────────────

export interface MexpCompareRow {
  trial_id: string;
  candidate_id: string;
  params: Record<string, unknown>;
  value: number;
  basis: Record<string, unknown>;
}

export interface MexpMetricGroup {
  metric_id: string;
  direction: string;
  unit_kind: string;
  n_evaluated: number;
  n_excluded: number;
  rows: MexpCompareRow[];
  ties: string[];
  exclusion_reasons: string[];
}

export interface MexpRecommendation {
  status: 'recommended' | 'abstained';
  abstain_reason?: string | null;
  candidate_id?: string | null;
  trial_id?: string | null;
  metric_evidence: Record<string, number>;
  basis: Record<string, unknown>;
  sensitivity: Record<string, unknown>;
  alternatives: Array<Record<string, unknown>>;
  caveats: string[];
  pareto_front: string[];
}

export interface MexpEvaluationView {
  experiment_id: string;
  status: string;
  counts: Record<string, number>;
  total_wall_ms: number;
  groups: MexpMetricGroup[];
  incomparable: string[];
  recommendation: MexpRecommendation | null;
}

export interface MexpCompareResponse {
  evaluation: MexpEvaluationView;
}

// ── adopt ───────────────────────────────────────────────────────────────

export interface MexpAdoptRequest {
  experiment_id: string;
  trial_id: string;
  reason?: string;
}

export interface MexpAdoptResponse {
  adoption: MexpAdoptionView;
}

/**
 * 把 transport 抛出的 ApiError 转成 typed MEXP 错误（`detail.reason_code +
 * message`）；网络层失败退回 network_error。UI 层直接渲染 code + message。
 */
export function asMethodExperimentError(err: unknown): { code: string; message: string } {
  const anyErr = err as {
    body?: { detail?: { reason_code?: string; message?: string } };
    message?: string;
  };
  const detail = anyErr?.body?.detail;
  if (detail && typeof detail === 'object' && detail.reason_code) {
    return { code: detail.reason_code, message: detail.message ?? '' };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}

type Options = { method?: string; body?: unknown; ownerToken?: string | null };

function client(sessionId: string, ownerToken?: string | null) {
  const base = `${BASE}/sessions/${encodeURIComponent(sessionId)}`;
  const opts = (o: Options): Options => ({ ...o, ownerToken });
  return {
    inspect: (payload: MexpInspectRequest = {}) =>
      apiFetch<MexpInspectResponse>(`${base}/inspect`, opts({
        method: 'POST',
        body: { query: payload.query ?? '', family_id: payload.family_id ?? '' },
      })),
    plan: (payload: MexpPlanRequest) =>
      apiFetch<MexpPlanResponse>(`${base}/plan`, opts({ method: 'POST', body: payload })),
    run: (experimentId: string, maxTrialsPerCall = 6) =>
      apiFetch<MexpRunResponse>(`${base}/run`, opts({
        method: 'POST',
        body: { experiment_id: experimentId, max_trials_per_call: maxTrialsPerCall },
      })),
    status: (experimentId: string) =>
      apiFetch<MexpStatusResponse>(
        `${base}/status/${encodeURIComponent(experimentId)}`, opts({})),
    compare: (experimentId: string) =>
      apiFetch<MexpCompareResponse>(
        `${base}/compare/${encodeURIComponent(experimentId)}`, opts({})),
    adopt: (payload: MexpAdoptRequest) =>
      apiFetch<MexpAdoptResponse>(`${base}/adopt`, opts({
        method: 'POST',
        body: {
          experiment_id: payload.experiment_id,
          trial_id: payload.trial_id,
          reason: payload.reason ?? '',
        },
      })),
  };
}

export const methodExperimentApi = { client };
