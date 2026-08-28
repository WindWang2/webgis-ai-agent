/**
 * #1048：SessionPlan 的 hydrate-then-delta 纯状态层。
 *
 * 把三条 session_plan_* SSE 事件叠加到 #1047 的 GET 投影上（信封关联）：
 * - progress 只应用在信封匹配的当前投影上；匹配行更新（pending → complete /
 *   voided / unavailable），未见的 capability 追加新行；陈旧信封的迟到事件
 *   一律丢弃（user story 9 / 17）。
 * - updated 交换目标/配方上下文：replaced=true 重置全部行；否则只保留已
 *   完成行（待完成/已作废/不可用行属于旧章节）。信封不匹配的 updated 丢弃
 *   —— 信封迁移只由 superseded 驱动。载荷没有 goal 字段，最新已知目标文本
 *   即 query（后端 _updated_event 亦是 `query || user_goal` 兜底）。
 * - superseded 以 old_envelope_id 关联当前信封，迁移到新信封并给出
 *   上一目标 → 新目标交接横幅；GET 投影刻意不含 previous_goal，交接信息
 *   只由增量携带（superseded 后端紧接着发 updated，新信封上下文由其补全，
 *   见 session_plan.py:442-443 —— 因此这里不做二次水合）。
 * - 投影缺失（204 / 水合失败）时 updated / superseded 可以开户信封；
 *   progress 没有关联锚点，丢弃。
 *
 * 会话级隔离（session_id ≠ 活动会话的事件永不上屏）由流 hook 顶部的 INV-2
 * 守卫统一保证（三条载荷都带 session_id），本模块不做会话判断。
 * 丢弃/无变化时返回同一引用，对 React 状态判同友好。
 */

import type {
  SessionPlanCapabilityStatus,
  SessionPlanProgressRow,
  SessionPlanProjection,
} from '@/lib/types/session-plan';

/** 交接横幅：previous_goal → goal（载荷字段是 previous_query / query）。 */
export interface SessionPlanSupersedeBanner {
  previous_goal: string;
  goal: string;
}

/** 面板渲染的视图状态：plan 为 null 时整卡隐藏（无信封 / 水合失败）。 */
export interface SessionPlanViewState {
  plan: SessionPlanProjection | null;
  supersede: SessionPlanSupersedeBanner | null;
}

export const EMPTY_SESSION_PLAN_STATE: SessionPlanViewState = Object.freeze({
  plan: null,
  supersede: null,
});

// 后端 ProgressStatus Literal 原样 —— 不造 UI 同义词（ADR-0076 契约）。
const STATUSES: ReadonlySet<string> = new Set(['pending', 'complete', 'voided', 'unavailable']);

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

function freshPlan(
  sessionId: string,
  envelopeId: string,
  query: string,
  replaced: boolean,
): SessionPlanProjection {
  return {
    session_id: sessionId,
    envelope_id: envelopeId,
    user_goal: query,
    query,
    plan_id: null,
    recipe_id: null,
    progress: [],
    replaced,
    superseded: false,
    updated_at: Date.now() / 1000,
  };
}

function applyUpdated(state: SessionPlanViewState, d: Record<string, unknown>): SessionPlanViewState {
  const envelopeId = str(d.envelope_id);
  if (!envelopeId) return state;
  const plan = state.plan;
  const replaced = d.replaced === true;
  const query = str(d.query);
  const planId = str(d.plan_id) || null;
  const recipeId = str(d.recipe_id) || null;

  let next: SessionPlanProjection;
  if (!plan) {
    // 无水合投影：updated 开户信封，行从零积累（后续 progress 追加）。
    next = { ...freshPlan(str(d.session_id), envelopeId, query, replaced), plan_id: planId, recipe_id: recipeId };
  } else {
    if (plan.envelope_id !== envelopeId) return state;
    next = {
      ...plan,
      envelope_id: envelopeId,
      plan_id: planId,
      recipe_id: recipeId,
      query,
      user_goal: query,
      replaced,
      progress: replaced ? [] : plan.progress,
      updated_at: Date.now() / 1000,
    };
  }
  return { ...state, plan: next };
}

function applyProgress(state: SessionPlanViewState, d: Record<string, unknown>): SessionPlanViewState {
  const plan = state.plan;
  if (!plan || plan.envelope_id !== str(d.envelope_id)) return state;
  const capability = str(d.capability);
  const status = d.status;
  if (!capability || typeof status !== 'string' || !STATUSES.has(status)) return state;
  const row: SessionPlanProgressRow = {
    capability,
    status: status as SessionPlanCapabilityStatus,
    bound_ref: str(d.bound_ref),
  };
  const progress = plan.progress.some((r) => r.capability === capability)
    ? plan.progress.map((r) => (r.capability === capability ? row : r))
    : [...plan.progress, row];
  return { ...state, plan: { ...plan, progress, updated_at: Date.now() / 1000 } };
}

function applySuperseded(state: SessionPlanViewState, d: Record<string, unknown>): SessionPlanViewState {
  const envelopeId = str(d.envelope_id);
  if (!envelopeId) return state;
  const plan = state.plan;
  // old_envelope_id 必须与当前信封相符（无投影时无从核对，允许接管）。
  if (plan && plan.envelope_id !== str(d.old_envelope_id)) return state;
  const query = str(d.query);
  const next = plan
    ? {
        ...plan,
        envelope_id: envelopeId,
        query,
        user_goal: query,
        // 新信封的配方/行由紧随的 updated / progress 补全，旧行绝不过账。
        plan_id: null,
        recipe_id: null,
        progress: [],
        updated_at: Date.now() / 1000,
      }
    : freshPlan(str(d.session_id), envelopeId, query, true);
  return {
    plan: next,
    supersede: { previous_goal: str(d.previous_query), goal: query },
  };
}

/**
 * 应用一条 session_plan_* 事件；其余事件名原样返回（流 hook 的分发链保证
 * 只有三个名字会到达这里，此分支是纯函数可测性的兜底）。
 */
export function applySessionPlanEvent(
  state: SessionPlanViewState,
  eventName: string,
  data: unknown,
): SessionPlanViewState {
  if (typeof data !== 'object' || data === null) return state;
  const d = data as Record<string, unknown>;
  if (eventName === 'session_plan_updated') return applyUpdated(state, d);
  if (eventName === 'session_plan_progress') return applyProgress(state, d);
  if (eventName === 'session_plan_superseded') return applySuperseded(state, d);
  return state;
}
