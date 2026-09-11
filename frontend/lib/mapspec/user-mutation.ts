import { ApiError, apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
import {
  clearPendingPresentation,
  clearPendingRemoved,
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  getPendingPresentation,
  markPendingRemoved,
  mergePendingPresentation,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { useToastStore } from '@/components/ui/toast';
import { tagUserDisplayed, untagUserPinned } from '@/lib/chat/turn-focus';
import { journalOnly, presentationCommand, reorderCommand } from '@/lib/workbench/undo';
import { devOnly } from '@/lib/utils/logger';

export interface LayerPresentationPatch {
  layerId: string;
  visible?: boolean;
  opacity?: number;
}

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: { layers?: any[] };
  correction_hint?: string;
}

function supersededFromError(err: unknown): MutationResponse | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: MutationResponse } | MutationResponse | null;
  if (!body || typeof body !== 'object') return null;
  if ('detail' in body && body.detail && typeof body.detail === 'object') {
    return body.detail;
  }
  if ('status' in body && (body as MutationResponse).status === 'superseded') {
    return body as MutationResponse;
  }
  return null;
}

function applyCommittedPresentationToHud(mapspec: { layers?: any[] } | null | undefined): void {
  for (const layer of useHudStore.getState().layers) {
    const specLayerId = mapspecLayerId(layer.id);
    // 在途乐观态保护（ST-P1-2 放大器）：响应 mapspec 的 presentation 是
    // 服务端该时刻的真相，但其它层可能还有未落定的本地 pending——
    // 全层无差别回灌会把在途乐观 toggle/opacity 改回旧值。仍有 pending
    // 的层跳过（compose 会以 pending 表达其期望），由它们自己
    // 的提交响应或 superseded 收敛。
    if (getPendingPresentation()[specLayerId] != null) continue;
    const pres = presentationFromMapSpec(mapspec ?? undefined, specLayerId);
    if (pres.visible !== undefined || pres.opacity !== undefined) {
      // #739: skip no-op updates — rewriting every layer per response (even
      // when values are equal) churned the layers identity and re-triggered
      // the focus-layer effect (self-sustaining camera refits + set_view
      // POSTs while updates land).
      if (
        (pres.visible === undefined || pres.visible === layer.visible) &&
        (pres.opacity === undefined || pres.opacity === layer.opacity)
      ) {
        continue;
      }
      // B3（workbench-v4）：这是服务端回灌，不是本地编辑 —— 保留认证标签，
      // 否则该行永久失去 generation 认证（假「待同步」来源之二）。
      useHudStore.getState().updateLayer(layer.id, pres, { source: 'server' });
    }
  }
}

function applyCommittedMapSpec(
  mapspec: { layers?: any[] } | undefined,
  revision?: number,
): boolean {
  if (!commitMapSpecDocument(mapspec, revision)) {
    // V7（审计 §1-M）：权威文档缺席/畸形/旧代次 —— 地图只能继续以当前
    // committed 真相渲染。此前直接 return false，HUD 行停在乐观新值而
    // compose 回落旧 committed 值（「面板已切换、地图没变」分叉面）。
    // 现按 committed presentation 强制回灌 HUD（pending 层跳过 —— compose
    // 以 pending 表达其期望，两处一致）；下一个权威文档（SSE 镜像/下一笔
    // 响应）到达时自然收敛到新真相。
    applyCommittedPresentationToHud((getCommittedMapSpec() ?? undefined) as { layers?: any[] } | undefined);
    return false;
  }

  if (!mapspec) return true;
  applyCommittedPresentationToHud(mapspec);
  return true;
}

function mapspecLayerId(layerId: string): string {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  return String(layer?._mapspecLayerId || layerId);
}

// 用户 mutation 串行链（ST-P1-2）：所有前端发起的 MapSpec CAS 写（用户
// presentation/视图/删除/排序 + agent 可见性 durability + 组件 patch）走
// 同一条 promise 链逐笔执行——每笔在轮到自己时才读游标 revision，拿到前
// 一笔推进后的值。此前用户路径 fire-and-forget：两个并发 toggle 携带同一
// expected_revision，后到者必然 409 被回滚（“按钮看起来坏了”）。
let userMutationChain: Promise<unknown> = Promise.resolve();

export function enqueueUserMutation<T>(operation: () => Promise<T>): Promise<T> {
  const run = userMutationChain.then(operation, operation);
  // 链自身绝不因单笔失败断裂（失败语义由各操作自己的 catch 决定）。
  userMutationChain = run.catch(() => undefined);
  return run;
}

export async function commitLayerPresentation(patch: LayerPresentationPatch): Promise<void> {
  if (patch.visible === undefined && patch.opacity === undefined) return;
  // V7（review MAJOR-1）：无 `_mapspecLayerId` 的 HUD-only 行（草图层、SSE
  // 预挂载行）对服务端是未知 layer —— patch_layer_presentation 会 400
  // "Layer not found"（lifecycle_engine 对未知 id 拒绝）。本地乐观态即最终
  // 态，跳过服务端提交；行随后被 spec 绑定（syncSpecLayersToStore 回填）时
  // 会经 server 回灌对齐。
  const row = useHudStore.getState().layers.find((item) => item.id === patch.layerId);
  if (row && !row._mapspecLayerId) return;
  const specLayerId = mapspecLayerId(patch.layerId);
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  // 乐观 pending 立即落（不等排队）——compose 在链等待期间即表达本地期望。
  mergePendingPresentation(specLayerId, { visible: patch.visible, opacity: patch.opacity });
  if (specLayerId !== patch.layerId) {
    mergePendingPresentation(patch.layerId, { visible: patch.visible, opacity: patch.opacity });
  }
  // 串行链内执行：revision 在轮到本笔时才读（ST-P1-2）。
  await enqueueUserMutation(async () => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return;
    if (sessionId !== enqueuedSessionId) return; // 会话已切换：作废排队操作

    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'patch_layer_presentation',
            expected_revision: revision,
            layer_id: specLayerId,
            visible: patch.visible,
            opacity: patch.opacity,
          },
          ownerToken,
          timeoutMs: 60_000,
          label: 'MapSpec presentation mutation',
        },
      );
      // Review R1（state/concurrency MAJOR-1）：await 之后的会话复核 ——
      // 60s 超时窗内切会话时，旧响应不得把旧会话的 spec/revision/回灌
      // 写进新会话游标（stale 守卫只拒低 revision，同/高 revision 会穿透）。
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      // 先清本次收敛目标自己的 pending，再回灌 —— 响应文档对目标层有
      // 裁决权（含并发 doc 带来的其它 presentation 字段）；applyCommittedMapSpec
      // 跳过其它仍有 pending 的层（它们的旧真相不得覆盖在途乐观态）。
      // 文档提交失败（缺席/畸形）时 applyCommittedMapSpec 内部会把 HUD
      // 强制对齐 committed presentation，杜绝「面板新值/地图旧值」分叉。
      clearPendingPresentation(specLayerId);
      clearPendingPresentation(patch.layerId);
      applyCommittedMapSpec(data.mapspec, data.mutation_revision);
    } catch (err) {
      const superseded = supersededFromError(err);
      if (!superseded) {
        // 非 superseded 失败：服务端未接受本笔 mutation —— compose 回落
        // committed 真相（清 pending），调用方回滚 HUD 行，两处一致。
        clearPendingPresentation(specLayerId);
        clearPendingPresentation(patch.layerId);
        throw err;
      }
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof superseded.mutation_revision === 'number') {
        setMapSpecRevision(superseded.mutation_revision);
      }
      // superseded：服务端真相对目标层有裁决权（#692 回滚语义）——
      // 目标 pending 先清再回灌（与成功路径同序）；文档缺席时 apply 内部
      // 把 HUD 对齐 committed，调用方随后回滚 HUD，两处一致。
      clearPendingPresentation(specLayerId);
      clearPendingPresentation(patch.layerId);
      if (superseded.mapspec) {
        applyCommittedMapSpec(superseded.mapspec, superseded.mutation_revision);
      }
      // #692 真实性：409 superseded 此前静默回滚用户操作（面板/地图突然变回
      // 服务端真相零解释）——用已解析的 correction_hint 出提示（缺省兜底文案）
      try {
        useToastStore.getState().addToast(
          superseded.correction_hint || '本操作已被更新的地图状态取代，已恢复为最新状态',
          'warning',
        );
      } catch { /* toast 不可用不得影响状态收敛 */ }
      if (!superseded.mapspec) throw err;
    }
  });
}

/**
 * #1077（v2 Phase 5）：spec 承载层的持久样式提交。
 *
 * 样式面板此前对 specBacked 层完全禁用样式控件（诚实但不可用）；命令路径
 * 返回 durable:false（运行时 paint，recompile 即回滚）。本通道把样式
 * patch 经 patch_layer_style 意图写入权威 MapSpec（CAS + 串行链，与
 * presentation 提交同款纪律），成功后 applyCommittedMapSpec 让 reconcile
 * 以 spec 为源应用 —— 不再有「UI 已改色但随后回滚」。
 * HUD-only 层仍走本地 style（runtime patch），不进本通道。
 */
export async function commitLayerStyleAndCommit(
  layerId: string,
  paint: Record<string, unknown>,
): Promise<void> {
  const specLayerId = mapspecLayerId(layerId);
  if (!specLayerId || Object.keys(paint).length === 0) return;
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  await enqueueUserMutation(async () => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return;
    if (sessionId !== enqueuedSessionId) return;
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'patch_layer_style',
            expected_revision: revision,
            layer_id: specLayerId,
            paint,
          },
          ownerToken,
          timeoutMs: 60_000,
          label: 'MapSpec layer style mutation',
        },
      );
      // Review R1 MAJOR-1：await 后会话复核（同 presentation 通道）。
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      applyCommittedMapSpec(data.mapspec, data.mutation_revision);
    } catch (err) {
      const superseded = supersededFromError(err);
      if (superseded) {
        if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
        if (typeof superseded.mutation_revision === 'number') {
          setMapSpecRevision(superseded.mutation_revision);
        }
        applyCommittedMapSpec(superseded.mapspec, superseded.mutation_revision);
        if (!superseded.mapspec) throw err;
        return;
      }
      throw err;
    }
  });
}

// U-3（#885）：非 409 失败（网络断开/5xx/会话过期）此前静默回滚——用户点
// 删除图层消失一秒后又弹回，全程无解释，弱网下看起来像按钮坏了。复用
// superseded 路径的 toast 模式，按操作语义给文案。
function toastRollback(actionLabel: string, err: unknown): void {
  try {
    // 延迟 import 防循环依赖（transport ↔ store 无环，保守起见与文件内
    // 其它延迟用法一致）。
    import('@/lib/api/transport').then(({ describeApiError }) => {
      useToastStore.getState().addToast(
        `${actionLabel}未生效（已恢复）：${describeApiError(err, '网络错误')}`,
        'error',
      );
    }).catch(() => { /* toast 不可用不得影响状态收敛 */ });
  } catch { /* noop */ }
}

export async function toggleLayerAndCommit(layerId: string): Promise<void> {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  // B10（workbench-v4）：不存在的行此前仍会以缺省 previous=true 提交
  // visible:false mutation —— 对服务端未知层发写、且乐观翻转无目标。
  if (!layer) return;
  const previous = layer.visible !== false;
  // V5/W4：undo 命令载荷在变更前捕获（forward=after，inverse=before）。
  presentationCommand(
    previous ? `隐藏 ${layer.name || layerId}` : `显示 ${layer.name || layerId}`,
    layerId,
    'user',
    { visible: previous },
    { visible: !previous },
  );
  useHudStore.getState().toggleLayer(layerId);
  // 「地图随对话」：用户手动点开的层标记为当前轮 —— 后续同轮 agent 展示
  // 不会把它当旧轮收起（不与用户对抗）。只处理"点开"方向（previous 为
  // hidden）；隐藏方向解除 pin（此后 Agent 收口语义恢复常态）。
  if (previous === false) {
    // wasHidden=true：toggleLayer 已同步翻转 visible，tagUserDisplayed 不能
    // 再以 store 的 visible 判断来源（否则 pin 永远不落——review P1）。
    tagUserDisplayed(layerId, true);
  } else {
    untagUserPinned(layerId);
  }
  try {
    await commitLayerPresentation({ layerId, visible: !previous });
  } catch (err) {
    useHudStore.getState().updateLayer(layerId, { visible: previous });
    toastRollback('显隐切换', err);
  }
}

export async function commitExplicitView(view: {
  center: [number, number];
  zoom?: number;
  bearing?: number;
  pitch?: number;
}): Promise<void> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  await enqueueUserMutation(async () => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || sessionId !== enqueuedSessionId) return;
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'set_view',
            expected_revision: revision,
            center: view.center,
            zoom: view.zoom,
            pitch: view.pitch,
            bearing: view.bearing,
          },
          ownerToken,
          label: 'MapSpec set_view mutation',
        },
      );
      // Review R1 MAJOR-1：await 后会话复核。
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      applyCommittedMapSpec(data.mapspec, data.mutation_revision);
    } catch (err) {
      // audit #842: 与姊妹 mutation 同款 409 收敛 —— superseded 时回灌服务端
      // 真相（此前 fire-and-forget 调用点没有 catch：unhandled rejection +
      // 本地视图真相丢失）；其它错误吞掉并保持调用方无感。
      const superseded = supersededFromError(err);
      if (!superseded) {
        // U-3（#885）：视口真相提交失败不再静默（此前注释自述"其它错误吞掉
        // 并保持调用方无感"——断网时每次 focusLayer 后视口悄悄丢失）。
        toastRollback('视图保存', err);
        return;
      }
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof superseded.mutation_revision === 'number') {
        setMapSpecRevision(superseded.mutation_revision);
      }
      applyCommittedMapSpec(superseded.mapspec, superseded.mutation_revision);
    }
  });
}

export async function commitMapSpecMutation(
  body: Record<string, unknown>,
): Promise<MutationResponse | void> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  return enqueueUserMutation(async (): Promise<MutationResponse | void> => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || sessionId !== enqueuedSessionId) return;
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
        {
          method: 'POST',
          body: { ...body, expected_revision: revision },
          ownerToken,
          label: 'MapSpec mutation',
        },
      );
      // Review R1 MAJOR-1：await 后会话复核。
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      applyCommittedMapSpec(data.mapspec, data.mutation_revision);
      return data;
    } catch (err) {
      const superseded = supersededFromError(err);
      if (!superseded) throw err;
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof superseded.mutation_revision === 'number') {
        setMapSpecRevision(superseded.mutation_revision);
      }
      if (!superseded.mapspec) throw err;
      applyCommittedMapSpec(superseded.mapspec, superseded.mutation_revision);
      // #692 真实性：同上——superseded 收敛不静默
      try {
        useToastStore.getState().addToast(
          superseded.correction_hint || '本操作已被更新的地图状态取代，已恢复为最新状态',
          'warning',
        );
      } catch { /* toast 不可用不得影响状态收敛 */ }
      return superseded;
    }
  });
}

/**
 * 单笔 remove_layer 持久化（ST-P1-1）。superseded 时收敛 revision + 服务端
 * 真相后检查该层是否仍在服务端 spec：仍在 → 'retry'（调用方带新 revision
 * 重试一次）；已不在（并发同删）→ 'reflected'。非 409 错误原样抛出。
 */
async function removeLayerFromSpecOnce(
  specLayerId: string,
  enqueuedSessionId: string | undefined,
): Promise<'committed' | 'reflected' | 'retry'> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return 'reflected';
  // Review R2（MINOR-4）：重试前的预检 —— 首笔 409 与重试之间切会话时，
  // remove_layer 不得落在新会话的端点（破坏性写 + 新会话 revision）。
  if (sessionId !== enqueuedSessionId) return 'reflected';
  try {
    const data = await apiFetch<MutationResponse>(
      `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
      {
        method: 'POST',
        body: {
          intent: 'remove_layer',
          expected_revision: revision,
          layer_id: specLayerId,
        },
        ownerToken,
        label: 'MapSpec remove_layer mutation',
      },
    );
    // Review R1 MAJOR-1：await 后会话复核。
    if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return 'reflected';
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    // 注意：只 commit 文档、不走 applyCommittedMapSpec 的 HUD 回灌 —— 被删
    // 层的 store 行已移除，回灌只可能把 presentation 应用到无关层。
    if (data.mapspec) commitMapSpecDocument(data.mapspec, data.mutation_revision);
    return 'committed';
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) throw err;
    if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return 'reflected';
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    if (superseded.mapspec) commitMapSpecDocument(superseded.mapspec, superseded.mutation_revision);
    const stillPresent = ((superseded.mapspec as { layers?: any[] } | undefined)?.layers ?? [])
      .some((layer: any) => String(layer?.id) === specLayerId);
    return stillPresent ? 'retry' : 'reflected';
  }
}

export type RemoveLayerOutcome = 'committed' | 'reflected' | 'unsynced';

/**
 * remove_layer 持久化（一次 superseded 重试）。双 superseded → 'unsynced'：
 * 服务端被并发 mutation 持续推进，本轮无法落账——调用方应保留本地删除
 * 决策（pendingRemoved 压制 compose），由下一次 mutation 收敛，绝不把
 * 已删层复活回地图。
 */
export async function removeLayerFromSpec(
  specLayerId: string,
  enqueuedSessionId?: string | undefined,
): Promise<RemoveLayerOutcome> {
  const first = await removeLayerFromSpecOnce(specLayerId, enqueuedSessionId);
  if (first !== 'retry') return first;
  const second = await removeLayerFromSpecOnce(specLayerId, enqueuedSessionId);
  return second === 'retry' ? 'unsynced' : second;
}

export async function removeLayerAndCommit(layerId: string): Promise<void> {
  const previous = useHudStore.getState().layers;
  // V7（review MAJOR-1）：无 spec 绑定的 HUD-only 行（草图层等）对服务端
  // 未知 —— remove_layer 会 400。本地删除 + journal 即完整语义。
  const removedRow = previous.find((l) => l.id === layerId);
  if (removedRow && !removedRow._mapspecLayerId) {
    journalOnly({
      type: 'remove',
      label: `删除图层 ${layerId}`,
      actor: 'user',
    });
    useHudStore.getState().removeLayer(layerId);
    return;
  }
  const specLayerId = mapspecLayerId(layerId);
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  // V5/W4：remove 落账不可逆（服务端 spec 已删、重挂需 source 数据重取）
  // —— 仅入 journal，不进 undo 栈（诚实可逆性元数据）。
  journalOnly({
    type: 'remove',
    label: `删除图层 ${layerId}`,
    actor: 'user',
  });
  markPendingRemoved(specLayerId);
  if (specLayerId !== layerId) markPendingRemoved(layerId);
  useHudStore.getState().removeLayer(layerId);
  let outcome: RemoveLayerOutcome | 'session-switched';
  try {
    outcome = await enqueueUserMutation(
      async (): Promise<RemoveLayerOutcome | 'session-switched'> => {
        const { sessionId } = getMapSpecSessionCursor();
        if (!sessionId || sessionId !== enqueuedSessionId) {
          // 会话已切换：resetLiveState 已清 store 行与 pending，无需 POST。
          return 'session-switched';
        }
        return removeLayerFromSpec(specLayerId, enqueuedSessionId);
      },
    );
  } catch (err) {
    // 网络/服务端错误：回滚本地删除（U-3 语义——不静默弹回，给出解释）。
    // #1078(G-7): 外科式回滚 —— 只把被删的行按原位置放回，不用 await 窗口
    // 前的整表快照覆盖（快照会抹掉窗口内 SSE 并发挂载的新行，直到下一个
    // mapspec 事件才重新镜像）。
    // V7（审计 §4-M）：await 之后的会话复核 —— 网络失败恰逢切会话时，
    // 旧会话的行不得回插进新会话的空表（resetLiveState 已清行）。
    if (getMapSpecSessionCursor().sessionId === enqueuedSessionId) {
      const removedIdx = previous.findIndex((l) => l.id === layerId);
      const current = useHudStore.getState().layers;
      if (removedIdx >= 0 && !current.some((l) => l.id === layerId)) {
        const restored = [...current];
        restored.splice(Math.min(removedIdx, restored.length), 0, previous[removedIdx]);
        useHudStore.getState().setLayers(restored);
      }
      toastRollback('删除图层', err);
    }
    clearPendingRemoved(specLayerId);
    clearPendingRemoved(layerId);
    return;
  }
  if (outcome === 'session-switched') return;
  if (outcome === 'unsynced') {
    // ST-P1-1：双 superseded 时此前无条件清 pendingRemoved → committed
    // spec（仍含被删层）被 compose 重新编入 → MapSpecRuntime 把图层复活回
    // 地图，而 HUD 行已删（地图有层、面板无行的分叉）。保留 pending 让
    // compose 继续压制该层（用户删除优先），下一次 mutation 收敛。
    devOnly.warn('[user-mutation] remove_layer unsynced (double superseded); keeping pendingRemoved:', specLayerId);
    try {
      useToastStore.getState().addToast(
        '图层已在地图上移除；同步暂时被并发操作占用，将在下次操作时自动收敛',
        'warning',
      );
    } catch { /* toast 不可用不得影响状态收敛 */ }
    return;
  }
  clearPendingRemoved(specLayerId);
  clearPendingRemoved(layerId);
}

export async function reorderLayersAndCommit(layers: { id: string; _mapspecLayerId?: string }[]): Promise<void> {
  const previous = useHudStore.getState().layers;
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  // V5/W4：z 序可逆命令（inverse=先前序重放同通道）。
  reorderCommand(
    '调整图层顺序',
    'user',
    previous.map((layer) => ({ id: layer.id, _mapspecLayerId: layer._mapspecLayerId })),
    layers.map((layer) => ({ ...layer })),
  );
  useHudStore.getState().reorderLayers(layers as any);
  try {
    await commitMapSpecMutation({
      intent: 'reorder_layers',
      layer_ids: layers.map((layer) => String(layer._mapspecLayerId || layer.id)),
    });
  } catch (err) {
    // #1078(G-7): 外科式回滚 —— 在**当前**数组上恢复提交前的相对顺序，
    // await 窗口内并发挂载的新行保留在末尾；整表 setLayers(previous) 会
    // 把这些新行一并抹掉（直到下一个 mapspec 事件才重新镜像）。
    // V7（审计 §4-M）：回滚前会话复核 —— 失败恰逢切会话时旧会话的顺序
    // 不得覆盖新会话的图层表。
    if (getMapSpecSessionCursor().sessionId === enqueuedSessionId) {
      const current = useHudStore.getState().layers;
      const prevIds = new Set(previous.map((row) => String(row.id)));
      const byId = new Map(current.map((row) => [String(row.id), row]));
      const ordered = previous
        .map((row) => byId.get(String(row.id)))
        .filter((row): row is NonNullable<typeof row> => row != null);
      const additions = current.filter((row) => !prevIds.has(String(row.id)));
      useHudStore.getState().setLayers([...ordered, ...additions]);
      toastRollback('图层排序', err);
    }
  }
}

export async function setLayerOpacityAndCommit(layerId: string, opacity: number): Promise<void> {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  const previous = layer?.opacity ?? 1;
  if (previous === opacity) return;
  // V5/W4：不透明度可逆命令（inverse=先前值，同通道反向提交）。
  presentationCommand(
    `调整不透明度 ${layer?.name || layerId}`,
    layerId,
    'user',
    { opacity: previous },
    { opacity },
  );
  useHudStore.getState().updateLayer(layerId, { opacity });
  try {
    await commitLayerPresentation({ layerId, opacity });
  } catch (err) {
    useHudStore.getState().updateLayer(layerId, { opacity: previous });
    toastRollback('不透明度调整', err);
  }
}
