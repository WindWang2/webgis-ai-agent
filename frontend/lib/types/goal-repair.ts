/**
 * Goal Repair 前端类型与解析（G42 / 有界目标补全执行）。
 *
 * 与后端 `app/services/gis_harness/goal_repair_execution` 的
 * `goalrepair.preview/v1` / `goalrepair.attempt/v1` 契约逐字段对应。
 * 解析纯函数：载荷缺失/形状非法 → null（键省略零漂移，绝不渲染半张卡）。
 */

/** 受控重观察事件（补全面 → map-panel 既有 reconcile 效应 → 新观察 POST）。 */
export const GOAL_REPAIR_REOBSERVE_EVENT = 'webgis:goal-repair-reobserve';

export type RepairDisposition =
  | 'executable'
  | 'needs_acknowledgment'
  | 'needs_user_input'
  | 'unsupported'
  | 'blocked';

export type AttemptActionStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped_cancelled'
  | 'needs_acknowledgment'
  | 'needs_user_input'
  | 'unsupported'
  | 'blocked'
  | 'waiting_observation'
  | 'waiting_delivery'
  | 'stale';

export interface RepairActionBinding {
  actionId: string;
  action: string;
  requirementId: string;
  rule: string;
  sideEffect: 'read_only' | 'write';
  budgetClass: string;
  expectedNewEvidence: string;
  disposition: RepairDisposition;
  reason: string;
  entry: string;
  params: Record<string, unknown>;
  /** needs_acknowledgment：预览披露的格式损失确认码。 */
  ackCodes?: string[];
  ackNote?: string;
}

export interface AttemptActionReceipt {
  action_id: string;
  action: string;
  requirement_id: string;
  status: AttemptActionStatus;
  detail: string;
  evidence_ref: string;
  download_url: string;
  job_id: string;
  error_code: string;
}

export interface GoalRepairAttemptView {
  attemptId: string;
  status: string;
  active: boolean;
  cancellable: boolean;
  sessionId: string;
  goalId: string;
  planVersion: string;
  outcome: string | null;
  finalVerdictBefore: string | null;
  finalVerdictAfter: string | null;
  finalChanged: boolean;
  actions: AttemptActionReceipt[];
  error: string | null;
  createdAt: string | null;
  completedAt: string | null;
  alreadyRunning?: boolean;
  selectedActionIds?: string[];
  budgetDenied?: string[];
}

export interface GoalRepairPreview {
  sessionId: string;
  goalId: string;
  verdict: string;
  signal: string;
  planVersion: string;
  mapspecRevision: number;
  actions: RepairActionBinding[];
  blockers: string[];
  progressRule: string;
  activeAttempt: GoalRepairAttemptView | null;
  attempts: GoalRepairAttemptView[];
}

const DISPOSITIONS = new Set<RepairDisposition>([
  'executable',
  'needs_acknowledgment',
  'needs_user_input',
  'unsupported',
  'blocked',
]);

function asBoundedString(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

function asBoundedStrings(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((v): v is string => typeof v === 'string')
    .slice(0, limit)
    .map((v) => v.slice(0, 128));
}

function parseBinding(value: unknown): RepairActionBinding | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const actionId = asBoundedString(raw.action_id, 16);
  const action = asBoundedString(raw.action, 32);
  if (!actionId || !action) return null;
  const disposition = asBoundedString(raw.disposition, 24);
  return {
    actionId,
    action,
    requirementId: asBoundedString(raw.requirement_id, 64),
    rule: asBoundedString(raw.rule, 48),
    sideEffect: raw.side_effect === 'write' ? 'write' : 'read_only',
    budgetClass: asBoundedString(raw.budget_class, 16),
    expectedNewEvidence: asBoundedString(raw.expected_new_evidence, 64),
    disposition: (DISPOSITIONS.has(disposition as RepairDisposition)
      ? disposition
      : 'unsupported') as RepairDisposition,
    reason: asBoundedString(raw.reason, 200),
    entry: asBoundedString(raw.entry, 48),
    params:
      raw.params && typeof raw.params === 'object'
        ? (raw.params as Record<string, unknown>)
        : {},
    ackCodes: asBoundedStrings(raw.ack_codes, 8),
    ackNote: asBoundedString(raw.ack_note, 120),
  };
}

function parseReceipt(value: unknown): AttemptActionReceipt | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const actionId = asBoundedString(raw.action_id, 16);
  if (!actionId) return null;
  return {
    action_id: actionId,
    action: asBoundedString(raw.action, 32),
    requirement_id: asBoundedString(raw.requirement_id, 64),
    status: asBoundedString(raw.status, 24) as AttemptActionStatus,
    detail: asBoundedString(raw.detail, 200),
    evidence_ref: asBoundedString(raw.evidence_ref, 128),
    download_url: asBoundedString(raw.download_url, 256),
    job_id: asBoundedString(raw.job_id, 48),
    error_code: asBoundedString(raw.error_code, 40),
  };
}

function parseAttempt(value: unknown): GoalRepairAttemptView | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const attemptId = asBoundedString(raw.attempt_id, 16);
  if (!attemptId) return null;
  const actions = Array.isArray(raw.actions)
    ? raw.actions.map(parseReceipt).filter((a): a is AttemptActionReceipt => !!a)
    : [];
  const outcome = asBoundedString(raw.outcome, 24);
  return {
    attemptId,
    status: asBoundedString(raw.status, 16) || 'pending',
    active: raw.active === true,
    cancellable: raw.cancellable === true,
    sessionId: asBoundedString(raw.session_id, 128),
    goalId: asBoundedString(raw.goal_id, 128),
    planVersion: asBoundedString(raw.plan_version, 16),
    outcome: outcome || null,
    finalVerdictBefore: asBoundedString(raw.final_verdict_before, 24) || null,
    finalVerdictAfter: asBoundedString(raw.final_verdict_after, 24) || null,
    finalChanged: raw.final_changed === true,
    actions: actions.slice(0, 8),
    error: asBoundedString(raw.error, 200) || null,
    createdAt: asBoundedString(raw.created_at, 40) || null,
    completedAt: asBoundedString(raw.completed_at, 40) || null,
    alreadyRunning: raw.already_running === true,
    selectedActionIds: asBoundedStrings(raw.selected_action_ids, 8),
    budgetDenied: asBoundedStrings(raw.budget_denied, 6),
  };
}

/** preview 载荷 → 有界视图（纯函数；非法形状 → null）。 */
export function parseGoalRepairPreview(data: unknown): GoalRepairPreview | null {
  if (data === null || typeof data !== 'object') return null;
  const raw = data as Record<string, unknown>;
  const goalId = asBoundedString(raw.goal_id, 128);
  const planVersion = asBoundedString(raw.plan_version, 16);
  if (!goalId || !planVersion) return null;
  const actions = Array.isArray(raw.actions)
    ? raw.actions.map(parseBinding).filter((b): b is RepairActionBinding => !!b)
    : [];
  const attempts = Array.isArray(raw.attempts)
    ? raw.attempts.map(parseAttempt).filter((a): a is GoalRepairAttemptView => !!a)
    : [];
  return {
    sessionId: asBoundedString(raw.session_id, 128),
    goalId,
    verdict: asBoundedString(raw.verdict, 24),
    signal: asBoundedString(raw.signal, 32),
    planVersion,
    mapspecRevision:
      typeof raw.mapspec_revision === 'number' ? raw.mapspec_revision : 0,
    actions: actions.slice(0, 8),
    blockers: asBoundedStrings(raw.blockers, 6),
    progressRule: asBoundedString(raw.progress_rule, 48),
    activeAttempt: parseAttempt(raw.active_attempt),
    attempts: attempts.slice(0, 3),
  };
}

/** attempt 载荷 → 有界视图（纯函数；非法形状 → null）。 */
export function parseGoalRepairAttempt(data: unknown): GoalRepairAttemptView | null {
  return parseAttempt(data);
}
