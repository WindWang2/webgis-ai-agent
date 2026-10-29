/**
 * Feature-edit REST 客户端（/goal 03）——apiFetch 双头鉴权
 * （Bearer 登录 + X-Session-Token 匿名 owner），409/410 由卡片状态机收敛。
 */
import { apiFetch } from '@/lib/api/transport';
import type {
  FeatureEditDecisionResult,
  FeatureEditHistoryEntry,
  FeatureEditUndoResult,
} from '@/lib/types/feature-edit';

const BASE = (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/feature-edits`;

/** 用户在提案卡上的显式决策（approve = 服务端同事务执行：CAS/入账/盖章/广播）。 */
export function decideFeatureEditPlan(
  sessionId: string,
  planId: string,
  action: 'approve' | 'reject',
  ownerToken?: string | null,
): Promise<FeatureEditDecisionResult> {
  return apiFetch<FeatureEditDecisionResult>(
    `${BASE(sessionId)}/plans/${encodeURIComponent(planId)}/decision`,
    {
      method: 'POST',
      body: { action },
      ownerToken,
      label: 'FeatureEdit',
    },
  );
}

/** 撤销该数据集 journal 的 head 条目（服务端 CAS；409 带冲突 detail）。 */
export function undoFeatureEdit(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<FeatureEditUndoResult> {
  return apiFetch<FeatureEditUndoResult>(
    `${BASE(sessionId)}/${encodeURIComponent(refId)}/undo`,
    { method: 'POST', ownerToken, label: 'FeatureEdit' },
  );
}

export function redoFeatureEdit(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<FeatureEditUndoResult> {
  return apiFetch<FeatureEditUndoResult>(
    `${BASE(sessionId)}/${encodeURIComponent(refId)}/redo`,
    { method: 'POST', ownerToken, label: 'FeatureEdit' },
  );
}

export function getFeatureEditHistory(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<{ head_seq: number; dropped: number; entries: FeatureEditHistoryEntry[] }> {
  return apiFetch(`${BASE(sessionId)}/${encodeURIComponent(refId)}/history`, {
    ownerToken,
    label: 'FeatureEdit',
  });
}

export function getFeatureEditPolicy(
  sessionId: string,
  ownerToken?: string | null,
): Promise<{ mode: 'confirm_all' | 'allow_safe_attribute' }> {
  return apiFetch(`${BASE(sessionId)}/policy`, { ownerToken, label: 'FeatureEdit' });
}

export function setFeatureEditPolicy(
  sessionId: string,
  mode: 'confirm_all' | 'allow_safe_attribute',
  ownerToken?: string | null,
): Promise<{ mode: string }> {
  return apiFetch(`${BASE(sessionId)}/policy`, {
    method: 'PUT',
    body: { mode },
    ownerToken,
    label: 'FeatureEdit',
  });
}
