/**
 * layer-edit-bridge — 真实图层的编辑事务桥（G14 / WP08）。
 *
 * 纪律：
 * - 手动手势 = owner 通道：preview → approve（REST decision）两段一次完成；
 *   服务端走完整事务（CAS/journal/盖章），前端绝不本地伪造数据版本；
 * - 手势期间（mousemove 逐帧）只更新本地 draft 渲染，零 REST 调用 ——
 *   一次手势 = 一个语义提交 = 一个 content_revision（V04/V05）；
 * - 提交回执驱动：增量刷新（refreshDatasetLayer）+ 撤销栈注册
 *   （featureEditCommand）+ 退役身份的 selection 清理（V17）+ opLog；
 * - 失败诚实返回（findings / stale / expired），调用方 UI 收敛，不重试风暴。
 */
import { decideFeatureEditPlan, previewFeatureEdit, redoFeatureEdit, undoFeatureEdit } from '@/lib/api/feature-edit';
import type { FeatureEditFinding, FeatureEditOpPayload, FeatureEditPreviewSummary } from '@/lib/types/feature-edit';
import { refreshDatasetLayer } from '@/lib/edit/refresh-dataset-layer';
import { featureEditCommand } from '@/lib/workbench/undo';
import { useHudStore } from '@/lib/store/useHudStore';
import { clearSelection, getSelection, publishSelection } from '@/lib/selection/selection-store';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { t as tNow, currentLocale } from '@/lib/i18n/t';

export interface SubmitEditOptions {
  featureIds?: string[];
  /** opLog/undo 标签（i18n key，默认 map.edit.defaultLabel）。 */
  labelKey?: string;
  ownerToken?: string | null;
  signal?: AbortSignal;
  /** 提交成功后是否注册撤销（默认 true；批量导入类可关）。 */
  registerUndo?: boolean;
}

export type SubmitEditResult =
  | { ok: true; planId: string; revision: number | undefined; createdWfids: string[]; retiredWfids: string[] }
  | { ok: false; stage: 'preview' | 'approve'; code: string; findings?: FeatureEditFinding[]; message?: string };

/** 单击要素 → 可寻址 id（properties._wfid 优先，沿 feature-id 链）。 */
export function featureIdFromProperties(properties: Record<string, unknown> | null | undefined): string | null {
  if (!properties) return null;
  const wfid = properties._wfid;
  if (typeof wfid === 'string' && wfid) return wfid;
  const fallback = properties.id ?? properties.OBJECTID ?? properties.fid ?? properties.feature_id;
  if (typeof fallback === 'string' && fallback) return fallback;
  if (typeof fallback === 'number') return String(fallback);
  return null;
}

/** 退役身份从 selection 语义移除（不静默指向随机行；V17）。 */
export function pruneSelectionIds(retiredIds: string[]): void {
  if (!retiredIds.length) return;
  try {
    const sel = getSelection();
    if (!sel || !sel.selected_ids?.length) return;
    const removed = new Set(retiredIds);
    const kept = sel.selected_ids.filter((id) => !removed.has(id));
    if (kept.length === sel.selected_ids.length) return;
    if (!kept.length) {
      clearSelection();
      return;
    }
    publishSelection('select', {
      source: sel.source,
      layer_id: sel.layer_id,
      feature_id: kept[0],
      selected_ids: kept,
      id_field: sel.id_field,
    });
  } catch {
    /* selection 维护失败不阻断编辑主链路 */
  }
}

/**
 * 手动编辑提交（preview → owner approve → 回执驱动收敛）。
 * 同步返回判别结果；刷新/撤销注册/selection 清理在成功路径内完成。
 */
export async function submitEditOps(
  sessionId: string,
  refId: string,
  ops: FeatureEditOpPayload[],
  opts: SubmitEditOptions = {},
): Promise<SubmitEditResult> {
  let plan: FeatureEditPreviewSummary;
  const ownerToken = opts.ownerToken !== undefined ? opts.ownerToken : getSessionIdentity().ownerToken ?? null;
  try {
    plan = await previewFeatureEdit(
      sessionId,
      { refId, featureIds: opts.featureIds },
      ops,
      ownerToken,
    );
  } catch (err) {
    return { ok: false, stage: 'preview', code: 'network_error', message: String((err as Error)?.message ?? err) };
  }
  if (!plan.committable || !plan.plan_id) {
    return {
      ok: false,
      stage: 'preview',
      code: 'validation',
      findings: plan.validation ?? [],
      message: plan.validation?.[0]?.message,
    };
  }
  const planId = plan.plan_id;
  let receipt: FeatureEditDecisionResult2;
  try {
    const decided = await decideFeatureEditPlan(sessionId, planId, 'approve', ownerToken);
    receipt = decided as FeatureEditDecisionResult2;
  } catch (err) {
    const status = (err as { status?: number })?.status;
    return {
      ok: false,
      stage: 'approve',
      code: status === 409 ? 'stale_revision' : status === 410 ? 'expired' : 'network_error',
      message: String((err as Error)?.message ?? err),
    };
  }
  const rev = receipt.receipt?.content_revision_after;
  const createdWfids = receipt.receipt?.created_wfids ?? [];
  const retiredWfids = receipt.receipt?.retired_wfids ?? [];

  // ── 回执驱动收敛（失败不回滚服务端事实，只诚实反映） ──
  if (typeof rev === 'number') {
    refreshDatasetLayer(sessionId, refId, rev, { ownerToken, signal: opts.signal });
  }
  pruneSelectionIds(retiredWfids);
  if (opts.registerUndo !== false) {
    featureEditCommand({
      label: tNow(opts.labelKey ?? 'map.edit.defaultLabel'),
      actor: 'user',
      refId,
      undo: () => undoFeatureEdit(sessionId, refId, ownerToken),
      redo: () => redoFeatureEdit(sessionId, refId, ownerToken),
    });
  }
  try {
    useHudStore.getState().pushOpLog({
      id: `fe-${planId}`,
      type: 'feature_edit',
      label: tNow(opts.labelKey ?? 'map.edit.defaultLabel'),
      time: new Date().toLocaleTimeString(currentLocale()),
      actor: 'user',
      detail: rev !== undefined ? `rev ${rev}` : undefined,
      reversible: opts.registerUndo !== false,
    });
  } catch {
    /* opLog 失败不影响编辑结果 */
  }
  return { ok: true, planId, revision: rev, createdWfids, retiredWfids };
}

/* 局部类型别名（决策响应的 receipt 结构）。 */
interface FeatureEditDecisionResult2 {
  status: string;
  receipt?: {
    entry_seq?: number;
    content_revision_after?: number;
    changed_wfids?: string[];
    stamp_status?: string;
    created_wfids?: string[];
    retired_wfids?: string[];
  };
}
