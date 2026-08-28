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

/** 后端 Literal 原样照搬：pending / complete / voided / unavailable —— 不造 UI 同义词。 */
export type SessionPlanCapabilityStatus = 'pending' | 'complete' | 'voided' | 'unavailable';

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
