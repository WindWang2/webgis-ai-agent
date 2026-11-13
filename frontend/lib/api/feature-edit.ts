/**
 * Feature-edit REST 客户端（/goal 03）——apiFetch 双头鉴权
 * （Bearer 登录 + X-Session-Token 匿名 owner），409/410 由卡片状态机收敛。
 */
import { apiFetch } from '@/lib/api/transport';
import type {
  FeatureEditDecisionResult,
  FeatureEditHistoryEntry,
  FeatureEditOpPayload,
  FeatureEditPreviewSummary,
  FeatureEditProvisionResult,
  FeatureEditQualification,
  FeatureEditLineageEvent,
  FeatureEditUndoResult,
} from '@/lib/types/feature-edit';

const BASE = (sessionId: string) =>
  `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/feature-edits`;

/** G14：预览（不改数据；返回有界 diff + plan_id）。 */
export function previewFeatureEdit(
  sessionId: string,
  target: { refId: string; featureIds?: string[] },
  ops: FeatureEditOpPayload[],
  ownerToken?: string | null,
): Promise<FeatureEditPreviewSummary> {
  return apiFetch<FeatureEditPreviewSummary>(`${BASE(sessionId)}/preview`, {
    method: 'POST',
    body: {
      target: {
        mode: 'explicit',
        ref_id: target.refId,
        ...(target.featureIds ? { feature_ids: target.featureIds } : {}),
      },
      ops,
    },
    ownerToken,
    label: 'FeatureEdit',
  });
}

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

// ── G14 数据集 provisioning ────────────────────────────────────────────────

export function createEditDataset(
  sessionId: string,
  req: { name: string; geometryType: 'Point' | 'LineString' | 'Polygon'; topologyRules?: string[] },
  ownerToken?: string | null,
): Promise<FeatureEditProvisionResult> {
  return apiFetch<FeatureEditProvisionResult>(`${BASE(sessionId)}/datasets`, {
    method: 'POST',
    body: {
      name: req.name,
      geometry_type: req.geometryType,
      ...(req.topologyRules ? { topology: { rules: req.topologyRules } } : {}),
    },
    ownerToken,
    label: 'FeatureEdit',
  });
}

export function promoteSketchDataset(
  sessionId: string,
  req: { name: string; features: Array<{ type: 'Feature'; geometry: GeoJSON.Geometry; properties?: Record<string, unknown> }> },
  ownerToken?: string | null,
): Promise<FeatureEditProvisionResult> {
  return apiFetch<FeatureEditProvisionResult>(`${BASE(sessionId)}/datasets/promote-sketch`, {
    method: 'POST',
    body: { name: req.name, features: req.features },
    ownerToken,
    label: 'FeatureEdit',
  });
}

export function createEditableCopy(
  sessionId: string,
  refId: string,
  name?: string,
  ownerToken?: string | null,
): Promise<FeatureEditProvisionResult> {
  return apiFetch<FeatureEditProvisionResult>(
    `${BASE(sessionId)}/datasets/${encodeURIComponent(refId)}/editable-copy`,
    {
      method: 'POST',
      body: name ? { name } : {},
      ownerToken,
      label: 'FeatureEdit',
    },
  );
}

export function getEditQualification(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<FeatureEditQualification> {
  return apiFetch<FeatureEditQualification>(
    `${BASE(sessionId)}/datasets/${encodeURIComponent(refId)}/qualification`,
    { ownerToken, label: 'FeatureEdit' },
  );
}

export function getFeatureLineage(
  sessionId: string,
  refId: string,
  wfid?: string,
  ownerToken?: string | null,
): Promise<{ ref_id: string; events: FeatureEditLineageEvent[] }> {
  const q = wfid ? `?wfid=${encodeURIComponent(wfid)}` : '';
  return apiFetch(`${BASE(sessionId)}/${encodeURIComponent(refId)}/lineage${q}`, {
    ownerToken,
    label: 'FeatureEdit',
  });
}
