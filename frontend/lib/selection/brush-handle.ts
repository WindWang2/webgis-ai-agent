/**
 * brush-handle — 截断框选的服务端句柄增强流（G24 / P3，review P1-1/P1-2 抽取）。
 *
 * 时序：map-panel 在 brush onUp 同步发布有界选择（ids ≤50 + bbox 谓词）→
 * 本模块异步建服务端句柄 → **迟到守卫**（epoch 未变 + store revision 仍为
 * 发布时值）→ 通过才补发布（handle/count）+ 补发 IR（selection_handle，
 * Agent 侧 webgis_selection_inspect 的合法获取通道）。
 *
 * 守卫语义（A17/A20）：
 * - 会话已切换（epoch 前进）→ 丢弃（不覆盖新会话的选择）；
 * - 已有更新选择（revision 前进，用户又点/框了别的）→ 丢弃（stale 点击
 *   不得覆盖新选择）；句柄由服务端 TTL 收敛（零 mutation）；
 * - 建柄失败 → null（调用方保持谓词选择 + 诚实反馈，不伪造全集）。
 */
import { createSelectionHandle, type SelectionHandleMeta } from '@/lib/api/selection-handles';
import {
  getSelection,
  getSelectionEpoch,
  publishSelection,
} from '@/lib/selection/selection-store';
import { emitFeatureSelected } from '@/lib/interaction/bus';
import { getSessionIdentity } from '@/lib/store/session-identity';

export interface BrushHandleFlowParams {
  sessionId: string;
  refId: string;
  layerId: string;
  bbox: [number, number, number, number];
  /** descriptor.content_revision（revision guard + 句柄快照）。 */
  revision: number;
  selectedIds: string[];
  idField: string | null;
  /** 渲染面命中数（服务端 count 不可证时的披露兜底）。 */
  matchedCountRendered: number;
  /** 首次发布后的 selection-store revision（守卫锚点）。 */
  publishedRevision: number;
  /** 首次发布时捕获的 epoch（守卫锚点）。 */
  epochAtPublish: number;
}

/**
 * 判别结果（R2-1：失败必须可反馈，迟到必须可区分 —— 二者对 UI 语义不同）：
 * - augmented：建柄成功且守卫通过（已补发布 + IR 补发）；
 * - discarded：迟到（epoch/revision 前进）—— 本次框选已被更新交互取代，
 *   静默丢弃（不得弹「失败」，用户已在新上下文里）；
 * - failed：建柄失败（网络/409/410…）—— 调用方给出诚实反馈，选择保持
 *   有界谓词形态（不伪造全集）。
 */
export type BrushHandleOutcome =
  | { status: 'augmented'; handle: string; count: number | null; countExact: boolean }
  | { status: 'discarded' }
  | { status: 'failed' };

/** 异步建柄 + 守卫 + 补发布 + IR 补发（零服务端 mutation on failure/late）。 */
export async function augmentBrushSelectionWithHandle(
  p: BrushHandleFlowParams,
): Promise<BrushHandleOutcome> {
  const { ownerToken } = getSessionIdentity();
  let meta: SelectionHandleMeta;
  try {
    meta = await createSelectionHandle({
      sessionId: p.sessionId,
      refId: p.refId,
      scope: { kind: 'bbox', bbox: p.bbox },
      revision: p.revision,
      ownerToken,
    });
  } catch {
    return { status: 'failed' }; // 建柄失败：保持谓词选择（诚实降级）
  }
  // 迟到守卫 1：会话世代
  if (getSelectionEpoch() !== p.epochAtPublish) return { status: 'discarded' };
  // 迟到守卫 2：store 里仍是本次发布的选中（未被更新的交互替换）
  const now = getSelection();
  if (!now || now.revision !== p.publishedRevision) return { status: 'discarded' };
  const authoritative = meta.count_exact && typeof meta.count === 'number';
  publishSelection('brush', {
    source: 'map',
    layer_id: p.layerId,
    artifact_ref: p.refId.startsWith('ref:') ? p.refId : undefined,
    selected_ids: p.selectedIds,
    id_field: p.idField ?? undefined,
    predicate: { kind: 'bbox', bbox: p.bbox },
    matched_count: meta.count ?? p.matchedCountRendered,
    handle: meta.handle,
    handle_count: typeof meta.count === 'number' ? meta.count : undefined,
    handle_count_exact: meta.count_exact,
    bbox: p.bbox,
  });
  emitFeatureSelected({
    layerId: p.layerId,
    featureId: p.selectedIds[0],
    datasetRef: p.refId.startsWith('ref:') ? p.refId : undefined,
    selectedIds: p.selectedIds,
    matchedCount: meta.count ?? p.matchedCountRendered,
    bbox: p.bbox,
    selectionHandle: meta.handle,
  });
  return {
    status: 'augmented',
    handle: meta.handle,
    count: meta.count,
    countExact: meta.count_exact && authoritative,
  };
}
