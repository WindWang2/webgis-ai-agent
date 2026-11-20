/**
 * Goal Repair API client（G42）—— 完成卡补全面与后端共享同一契约。
 *
 * 端点：/api/v1/goal-repair/*（sessions/{sid}/preview | attempts）。
 * typed 错误（HTTP detail.code，如 SESSION_BUSY / NO_PROGRESS /
 * BUDGET_DENIED）保持原样上抛，面板按 code 解释并展示用户可行动的原因。
 */
import { apiFetch } from './transport';

export interface AttemptStartPayload {
  action_ids?: string[];
  acknowledged_losses?: string[];
}

/** 拉取补全预览（零副作用；404/409 等 typed 语义以 ApiError 上抛）。 */
export async function fetchGoalRepairPreview(sessionId: string): Promise<unknown> {
  return apiFetch(`/api/v1/goal-repair/sessions/${encodeURIComponent(sessionId)}/preview`);
}

/** 启动一次有界补全（幂等：活跃 attempt 复用；busy 以 409 SESSION_BUSY 上抛）。 */
export async function startGoalRepairAttempt(
  sessionId: string,
  payload: AttemptStartPayload = {},
): Promise<unknown> {
  // body 传对象（transport.buildRequest 统一 JSON 序列化 + Content-Type）。
  return apiFetch(`/api/v1/goal-repair/sessions/${encodeURIComponent(sessionId)}/attempts`, {
    method: 'POST',
    body: {
      ...(payload.action_ids ? { action_ids: payload.action_ids } : {}),
      ...(payload.acknowledged_losses?.length
        ? { acknowledged_losses: payload.acknowledged_losses }
        : {}),
    },
  });
}

/** 查询 attempt 状态（含 waiting_delivery / runner 丢失的惰性对账）。 */
export async function fetchGoalRepairAttempt(attemptId: string): Promise<unknown> {
  return apiFetch(`/api/v1/goal-repair/attempts/${encodeURIComponent(attemptId)}`);
}

/** 两阶段取消（幂等；已终态 = no-op 披露）。 */
export async function cancelGoalRepairAttempt(attemptId: string): Promise<unknown> {
  return apiFetch(`/api/v1/goal-repair/attempts/${encodeURIComponent(attemptId)}/cancel`, {
    method: 'POST',
  });
}
