/**
 * Agent Ops Cockpit typed client（cockpit.v1）。
 *
 * 只读投影面：全部状态来自后端 projection（/cockpit/*），前端绝不自推导
 * Mission 状态。operator 动作只调用**既有** /mission-runtime 生命周期路由
 * （worker_id 固定 "ops-console"，fencing 409 由服务端裁决，前端不做乐观
 * 写入）。session 端点经 X-Session-Token 走 jobs.ts 同款归属证明链。
 *
 * 轮询节奏由服务端 poll_after_ms 建议（jobs.ts 协议先例），客户端钳制
 * 到 [MIN_COCKPIT_POLL_MS, MAX_COCKPIT_POLL_MS]。
 */
import { apiFetch } from './transport';

export const COCKPIT_SCHEMA = 'cockpit.v1';

/** cockpit 常量 worker 身份：服务端 fencing 以此判权。 */
export const COCKPIT_WORKER_ID = 'ops-console';

export const MIN_COCKPIT_POLL_MS = 3000;
export const MAX_COCKPIT_POLL_MS = 30000;

export type MissionState =
  | 'created' | 'planning' | 'running' | 'waiting_dependency'
  | 'partially_complete' | 'suspended' | 'recovering'
  | 'complete' | 'failed' | 'cancelled';

/** 服务端 TRANSITIONS 白名单的镜像（仅用于禁用按钮；裁决权在服务端 409）。 */
export const MISSION_ALLOWED_TRANSITIONS: Record<MissionState, readonly string[]> = {
  created: ['planning', 'running', 'cancelled', 'recovering'],
  planning: ['running', 'suspended', 'failed', 'cancelled', 'recovering'],
  running: ['waiting_dependency', 'partially_complete', 'suspended', 'recovering', 'complete', 'failed', 'cancelled'],
  waiting_dependency: ['running', 'suspended', 'recovering', 'failed', 'cancelled'],
  partially_complete: ['running', 'suspended', 'complete', 'failed', 'cancelled', 'recovering'],
  suspended: ['running', 'recovering', 'cancelled', 'failed'],
  recovering: ['running', 'partially_complete', 'waiting_dependency', 'suspended', 'complete', 'failed', 'cancelled'],
  complete: [],
  failed: [],
  cancelled: [],
};

const TERMINAL_STATES: readonly MissionState[] = ['complete', 'failed', 'cancelled'];

export function missionIsTerminal(state: string): boolean {
  return TERMINAL_STATES.includes(state as MissionState);
}

/** 状态机镜像：该操作在当前状态下是否可能合法（服务端仍是唯一裁决者）。 */
export function missionAllows(state: string, op: 'start' | 'suspend' | 'resume' | 'cancel'): boolean {
  if (missionIsTerminal(state)) return false;
  const targets: Record<string, readonly string[]> = {
    start: ['planning', 'running'],
    suspend: ['suspended'],
    resume: ['running', 'recovering', 'partially_complete', 'waiting_dependency'],
    cancel: ['cancelled'],
  };
  return targets[op].some((to) => MISSION_ALLOWED_TRANSITIONS[state as MissionState]?.includes(to));
}

export interface CockpitMissionSummary {
  mission_id: string;
  org_id: string;
  state: MissionState | string;
  revision: number;
  goal_revision: number;
  root_goal: string;
  project_id: string | null;
  created_at: number;
  updated_at: number;
  lease_owner: string;
  blocked_reason: string;
  frontier_counts: Record<string, number>;
  resource: {
    quota: Record<string, number>;
    consumed: Record<string, number>;
    reserved: Record<string, number>;
  };
}

export interface CockpitMissionList {
  schema: string;
  generated_at: number;
  missions: CockpitMissionSummary[];
  has_active: boolean;
  poll_after_ms: number | null;
}

export interface CockpitMissionRecord {
  schema_version: string;
  mission_id: string;
  org_id: string;
  user_id: string;
  project_id: string | null;
  root_goal: string;
  goal_revision: number;
  state: MissionState | string;
  revision: number;
  created_at: number;
  updated_at: number;
  refs: Record<string, string[]>;
  frontier: Record<string, string[]>;
  resource_budget: {
    quota: Record<string, number>;
    consumed: Record<string, number>;
    reserved: Record<string, number>;
    retry_cost: Record<string, number>;
  };
  failure: { error_code: string; detail: string; recovery_class: string; at: number };
  recovery: { attempt: number; last_checkpoint_id: string; last_checkpoint_at: number; last_recovery_at: number; blocked_reason: string; unresolved_ops: string[] };
  lease_owner: string;
  lease_epoch: number;
  lease_expires_at: number;
  [key: string]: unknown;
}

export interface CockpitDiagnostics {
  mission_id: string;
  goal: string;
  state: string;
  revision: number;
  goal_revision: number;
  current_frontier: Record<string, string[]>;
  blocked_reason: string;
  artifact_count: number;
  swarm_status: string;
  resource_use: Record<string, number>;
  last_checkpoint: string;
  lease_owner: string;
  lease_epoch: number;
  recovery_count: number;
}

export interface CockpitMissionDetail {
  schema: string;
  generated_at: number;
  mission: CockpitMissionRecord;
  diagnostics: CockpitDiagnostics;
}

export interface CockpitCheckpoint {
  checkpoint_id: string;
  mission_revision: number;
  goal_revision: number;
  state: string;
  created_at: number;
  snapshot?: Record<string, unknown>;
}

export interface CockpitTimeline {
  schema: string;
  generated_at: number;
  mission_id: string;
  state: string;
  revision: number;
  goal_revision: number;
  root_goal: string;
  updated_at: number;
  blocked_reason: string;
  refs: Record<string, string[]>;
  checkpoints: CockpitCheckpoint[];
}

export interface CockpitSwarmTask {
  task_id: string;
  assignment_id: string;
  state: string;
  operation_class: string;
  produced_refs: string[];
  summary: string;
  error_code: string;
  attempt: number;
  idempotency_key: string;
  settled_at: number;
}

export interface CockpitSwarmRun {
  swarm_run_id: string;
  mission_id: string;
  goal_slice: string;
  state: string;
  tasks: Record<string, CockpitSwarmTask>;
  created_at: number;
  updated_at: number;
}

export interface CockpitSwarm {
  schema: string;
  generated_at: number;
  mission_id: string;
  runs: CockpitSwarmRun[];
}

export interface CockpitSkillView {
  schema: string;
  generated_at: number;
  session_id: string;
  present: boolean;
  guidance: {
    decision?: Record<string, unknown>;
    projection?: Record<string, unknown>;
    shadow?: Record<string, unknown> | null;
    guides_planning?: boolean;
    pi_context?: Record<string, unknown>;
  } | null;
  mission_id: string;
}

export interface CockpitClaim {
  claim_id: string;
  claim_type: string;
  subject: string;
  predicate: string;
  value: number | null;
  value_text: string;
  unit: string;
  comparator: string;
  method: string;
  supporting_evidence_refs: string[];
  contradicting_evidence_refs: string[];
  confidence: number | null;
  status: string;
  narrative: string;
  [key: string]: unknown;
}

export interface CockpitEdge {
  edge_id: string;
  relation: string;
  src: string;
  dst: string;
}

export interface CockpitEvidenceView {
  schema: string;
  generated_at: number;
  session_id: string;
  present: boolean;
  claims: CockpitClaim[];
  edges: CockpitEdge[];
  stats: Record<string, number>;
  truncated: boolean;
}

export interface CockpitTraceEvent {
  ts: number;
  stage: string;
  detail: Record<string, string | number | boolean>;
}

export interface CockpitTraceView {
  schema: string;
  generated_at: number;
  session_id: string;
  events: CockpitTraceEvent[];
  summary: { by_stage?: Record<string, number>; [key: string]: unknown };
  counters: Record<string, number>;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** 服务端建议轮询间隔 → 客户端有界化（null/异常值回退默认）。 */
export function clampPollAfterMs(ms: number | null | undefined, fallback = 5000): number {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return fallback;
  return clamp(ms, MIN_COCKPIT_POLL_MS, MAX_COCKPIT_POLL_MS);
}

export function getCockpitHealth(signal?: AbortSignal): Promise<{ enabled: boolean; schema: string }> {
  return apiFetch('/api/v1/cockpit/health', { signal, label: 'Cockpit health' });
}

export function listCockpitMissions(
  options: { limit?: number; signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitMissionList> {
  const limit = clamp(options.limit ?? 50, 1, 200);
  return apiFetch(`/api/v1/cockpit/missions?limit=${limit}`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit missions',
  });
}

export function getCockpitMission(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitMissionDetail> {
  return apiFetch(`/api/v1/cockpit/missions/${encodeURIComponent(missionId)}`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit mission',
  });
}

export function getCockpitTimeline(
  missionId: string,
  options: { limit?: number; signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitTimeline> {
  const limit = clamp(options.limit ?? 32, 1, 32);
  return apiFetch(`/api/v1/cockpit/missions/${encodeURIComponent(missionId)}/timeline?limit=${limit}`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit timeline',
  });
}

export function getCockpitSwarm(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitSwarm> {
  return apiFetch(`/api/v1/cockpit/missions/${encodeURIComponent(missionId)}/swarm`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit swarm',
  });
}

export function getCockpitSessionSkill(
  sessionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitSkillView> {
  return apiFetch(`/api/v1/cockpit/sessions/${encodeURIComponent(sessionId)}/skill`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit skill',
  });
}

export function getCockpitSessionEvidence(
  sessionId: string,
  options: { limit?: number; signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitEvidenceView> {
  const limit = clamp(options.limit ?? 200, 1, 500);
  return apiFetch(`/api/v1/cockpit/sessions/${encodeURIComponent(sessionId)}/evidence?limit=${limit}`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit evidence',
  });
}

export function getCockpitSessionTrace(
  sessionId: string,
  options: { limit?: number; signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitTraceView> {
  const limit = clamp(options.limit ?? 64, 1, 64);
  return apiFetch(`/api/v1/cockpit/sessions/${encodeURIComponent(sessionId)}/trace?limit=${limit}`, {
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: 'Cockpit trace',
  });
}

/** Operator 动作（复用既有 mission-runtime 生命周期路由；无重试——非幂等）。 */
function missionLifecycleOp<T>(
  op: 'start' | 'suspend' | 'resume' | 'cancel',
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<T> {
  return apiFetch(`/api/v1/mission-runtime/missions/${encodeURIComponent(missionId)}/${op}`, {
    method: 'POST',
    body: { worker_id: COCKPIT_WORKER_ID },
    signal: options.signal,
    ownerToken: options.ownerToken ?? undefined,
    label: `Cockpit mission ${op}`,
  });
}

export function startCockpitMission(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitMissionRecord> {
  return missionLifecycleOp<CockpitMissionRecord>('start', missionId, options);
}

export function suspendCockpitMission(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitMissionRecord> {
  return missionLifecycleOp<CockpitMissionRecord>('suspend', missionId, options);
}

/** resume 返回恢复编排结果（recovery.recover 的 dict，含 ok/frontier 等）。 */
export function resumeCockpitMission(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<Record<string, unknown>> {
  return missionLifecycleOp<Record<string, unknown>>('resume', missionId, options);
}

export function cancelCockpitMission(
  missionId: string,
  options: { signal?: AbortSignal; ownerToken?: string | null } = {},
): Promise<CockpitMissionRecord> {
  return missionLifecycleOp<CockpitMissionRecord>('cancel', missionId, options);
}
