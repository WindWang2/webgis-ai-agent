/**
 * Task Data Supply frontend client（GOAL-20261126-02 WP4）.
 *
 * 与 agent 工具面共用后端 service（/api/v1/task-data-supply）。响应全部有界
 * （覆盖矩阵 ≤7 行/角色、候选 ≤12 条）；错误走共享 transport 的类型化通道。
 */

import { apiFetch } from './transport';

const LABEL = 'TaskDataSupply API error';

export interface CoverageGap {
  dimension: string;
  detail: string;
  code?: string;
  field?: string;
}

export interface CoverageEvaluation {
  role: string;
  candidate_id: string;
  verdict: 'full' | 'partial' | 'declared_only' | 'none' | 'unknown' | string;
  spatial_ratio: number | null;
  temporal_verdict: string;
  gaps: CoverageGap[];
  disclosures: string[];
  completeness: string;
  fit_for_methods: Record<string, boolean>;
  numeric_stats_qualified: boolean;
  license_disclosed: boolean;
}

export interface RoleCoverage {
  role: string;
  evaluations: CoverageEvaluation[];
  unknown_reasons: string[];
}

export interface CoverageMatrix {
  roles: RoleCoverage[];
  method_notes: string[];
}

export interface SupplyAlternative {
  kind: string;
  note: string;
}

export interface SupplyStep {
  role: string;
  action: string;
  candidate_id: string;
  channel: string;
  ads_request: Record<string, unknown>;
  cost_estimate: Record<string, number | null>;
  alternatives: SupplyAlternative[];
  disclosures: string[];
  numeric_stats_qualified: boolean;
  outcome: string;
  receipt_ref: string;
  rows_written: number | null;
  user_choice: string;
  dedup_key: string;
  step_fp: string;
}

export interface SupplyPlan {
  plan_id: string;
  state: string;
  budget_status: string;
  budget_max_bytes: number | null;
  estimated_total_bytes: number | null;
  budget_acknowledged: boolean;
  requirement_id: string;
  steps: SupplyStep[];
}

export interface ScopeRequest {
  session_id: string;
  place?: string;
  bbox?: number[] | null;
  time_range?: string[] | null;
  roles: string[];
  keyword?: string;
  budget_max_bytes?: number | null;
  choices?: Record<string, string>;
}

export interface CoverageResponse {
  success: boolean;
  requirement_id: string;
  missing_obligations: Array<Record<string, unknown>>;
  coverage: CoverageMatrix;
  discovery_meta: Record<string, unknown>;
}

export interface PlanResponse {
  success: boolean;
  plan: SupplyPlan;
  discovery_meta: Record<string, unknown>;
}

export interface ExecuteResponse {
  success: boolean;
  plan_id: string;
  plan_state: string;
  results: Array<{ role: string; outcome: string; receipt_ref?: string; rows?: number | null }>;
  plan: SupplyPlan;
}

/** 任务覆盖评估（需求 × 候选 → 覆盖矩阵）。 */
export async function compareTaskCoverage(
  req: ScopeRequest,
  opts: { ownerToken?: string | null } = {},
): Promise<CoverageResponse> {
  return apiFetch<CoverageResponse>('/api/v1/task-data-supply/coverage', {
    method: 'POST',
    body: JSON.stringify(req),
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(opts.ownerToken ? { 'X-Session-Token': opts.ownerToken } : {}),
    },
    label: LABEL,
  });
}

/** 构建受预算供给计划（存会话 store）。 */
export async function createSupplyPlan(
  req: ScopeRequest,
  opts: { ownerToken?: string | null } = {},
): Promise<PlanResponse> {
  return apiFetch<PlanResponse>('/api/v1/task-data-supply/plans', {
    method: 'POST',
    body: JSON.stringify(req),
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(opts.ownerToken ? { 'X-Session-Token': opts.ownerToken } : {}),
    },
    label: LABEL,
  });
}

/** 执行已批准计划步骤（显式 approved）。 */
export async function executeSupplyPlan(
  planId: string,
  sessionId: string,
  approved: boolean,
  roles: string[] = [],
  opts: { ownerToken?: string | null } = {},
): Promise<ExecuteResponse> {
  return apiFetch<ExecuteResponse>(
    `/api/v1/task-data-supply/plans/${encodeURIComponent(planId)}/execute?session_id=${encodeURIComponent(sessionId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ approved, roles }),
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(opts.ownerToken ? { 'X-Session-Token': opts.ownerToken } : {}),
      },
      label: LABEL,
    },
  );
}

/** 计划状态查询。 */
export async function getSupplyPlan(
  planId: string,
  sessionId: string,
  opts: { ownerToken?: string | null } = {},
): Promise<{ success: boolean; plan: SupplyPlan }> {
  return apiFetch<{ success: boolean; plan: SupplyPlan }>(
    `/api/v1/task-data-supply/plans/${encodeURIComponent(planId)}?session_id=${encodeURIComponent(sessionId)}`,
    {
      credentials: 'include',
      headers: opts.ownerToken ? { 'X-Session-Token': opts.ownerToken } : undefined,
      label: LABEL,
    },
  );
}
