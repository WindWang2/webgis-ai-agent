/**
 * SessionPlan envelope projection — Pi 路径的宿主计划（ADR-0076）。
 *
 * Backend source of truth: app/services/session_plan.py (SessionPlan /
 * CapabilityProgress)，由只读端点 GET /api/v1/chat/sessions/{id}/plan
 * 提供水合投影（#1047）。
 *
 * 注意：与 `agent-plan.ts` 的 AgentPlanState（CanonicalPlan 步进 HUD）是
 * 两个不同的计划概念，类型永不合并、字段永不互赋（ADR-0076）。本类型是
 * 水合事实；session_plan_* SSE 事件（#1048）只在其上叠加增量。
 */

/** 后端 Literal 原样照搬：pending / complete / voided / unavailable / failed
 * （v3 Phase E：failed = 执行过但未产出 artifact，可重试）—— 不造 UI 同义词。 */
export type SessionPlanCapabilityStatus = 'pending' | 'complete' | 'voided' | 'unavailable' | 'failed';

export interface SessionPlanProgressRow {
  capability: string;
  status: SessionPlanCapabilityStatus;
  /** 完成时绑定的数据 ref（如 ref:geojson-*）；未绑定为 ""。 */
  bound_ref: string;
}

export interface SessionPlanProjection {
  session_id: string;
  envelope_id: string;
  user_goal: string;
  /** GIS 章节三字段 —— 槽位已开但 intent 未跑时为 null（显式空投影）。 */
  query: string | null;
  plan_id: string | null;
  recipe_id: string | null;
  progress: SessionPlanProgressRow[];
  replaced: boolean;
  superseded: boolean;
  updated_at: number;
}

/* ── #1048：三条 session_plan_* SSE 事件的载荷 ──────────────────────────
 * 冻结契约（hardening spec #1029 冻结的 stream-only 面），由
 * app/services/session_plan.py 的 _updated_event / _progress_event /
 * _superseded_event 逐字构造 —— 任何字段都不得增删或为 UI 富化。
 * 全量能力列表永不走 SSE：水合靠 GET 投影，SSE 只是增量。 */

export type SessionPlanEventName =
  | 'session_plan_updated'
  | 'session_plan_progress'
  | 'session_plan_superseded';

export interface SessionPlanUpdatedPayload {
  session_id: string;
  envelope_id: string;
  plan_id: string;
  recipe_id: string;
  query: string;
  replaced: boolean;
}

export interface SessionPlanProgressPayload {
  session_id: string;
  envelope_id: string;
  capability: string;
  status: SessionPlanCapabilityStatus;
  bound_ref: string;
}

export interface SessionPlanSupersededPayload {
  session_id: string;
  old_envelope_id: string;
  envelope_id: string;
  previous_query: string;
  query: string;
}
