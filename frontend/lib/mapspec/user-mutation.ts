import { ApiError, apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
import {
  clearPendingPresentation,
  clearPendingRemoved,
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  markPendingRemoved,
  mergePendingPresentation,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { useToastStore } from '@/components/ui/toast';
import { tagUserDisplayed, untagUserPinned } from '@/lib/chat/turn-focus';

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

function applyCommittedMapSpec(mapspec: { layers?: any[] } | undefined): void {
  commitMapSpecDocument(mapspec);
  if (!mapspec) return;
  for (const layer of useHudStore.getState().layers) {
    const pres = presentationFromMapSpec(mapspec, mapspecLayerId(layer.id));
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
      useHudStore.getState().updateLayer(layer.id, pres);
    }
  }
}

function mapspecLayerId(layerId: string): string {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  return String(layer?._mapspecLayerId || layerId);
}

export async function commitLayerPresentation(patch: LayerPresentationPatch): Promise<void> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return;
  if (patch.visible === undefined && patch.opacity === undefined) return;
  const specLayerId = mapspecLayerId(patch.layerId);
  mergePendingPresentation(specLayerId, { visible: patch.visible, opacity: patch.opacity });
  if (specLayerId !== patch.layerId) {
    mergePendingPresentation(patch.layerId, { visible: patch.visible, opacity: patch.opacity });
  }

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
        label: 'MapSpec presentation mutation',
      },
    );
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    applyCommittedMapSpec(data.mapspec);
    clearPendingPresentation(specLayerId);
    clearPendingPresentation(patch.layerId);
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) {
      clearPendingPresentation(specLayerId);
      clearPendingPresentation(patch.layerId);
      throw err;
    }
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    applyCommittedMapSpec(superseded.mapspec);
    clearPendingPresentation(specLayerId);
    clearPendingPresentation(patch.layerId);
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
  const previous = layer?.visible !== false;
  useHudStore.getState().toggleLayer(layerId);
  // 「地图随对话」：用户手动点开的层标记为当前轮 —— 后续同轮 agent 展示
  // 不会把它当旧轮收起（不与用户对抗）。只处理"点开"方向（previous 为
  // hidden）；隐藏方向解除 pin（此后 Agent 收口语义恢复常态）。
  if (previous === false) {
    tagUserDisplayed(layerId);
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
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return;
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
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    applyCommittedMapSpec(data.mapspec);
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
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    applyCommittedMapSpec(superseded.mapspec);
  }
}

export async function commitMapSpecMutation(
  body: Record<string, unknown>,
): Promise<MutationResponse | void> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return;
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
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    applyCommittedMapSpec(data.mapspec);
    return data;
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) throw err;
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    if (!superseded.mapspec) throw err;
    applyCommittedMapSpec(superseded.mapspec);
    // #692 真实性：同上——superseded 收敛不静默
    try {
      useToastStore.getState().addToast(
        superseded.correction_hint || '本操作已被更新的地图状态取代，已恢复为最新状态',
        'warning',
      );
    } catch { /* toast 不可用不得影响状态收敛 */ }
    return superseded;
  }
}

export async function removeLayerAndCommit(layerId: string): Promise<void> {
  const previous = useHudStore.getState().layers;
  const specLayerId = mapspecLayerId(layerId);
  markPendingRemoved(specLayerId);
  if (specLayerId !== layerId) markPendingRemoved(layerId);
  useHudStore.getState().removeLayer(layerId);
  try {
    await commitMapSpecMutation({
      intent: 'remove_layer',
      layer_id: specLayerId,
    });
  } catch (err) {
    useHudStore.getState().setLayers(previous);
    toastRollback('删除图层', err);
  } finally {
    clearPendingRemoved(specLayerId);
    clearPendingRemoved(layerId);
  }
}

export async function reorderLayersAndCommit(layers: { id: string; _mapspecLayerId?: string }[]): Promise<void> {
  const previous = useHudStore.getState().layers;
  useHudStore.getState().reorderLayers(layers as any);
  try {
    await commitMapSpecMutation({
      intent: 'reorder_layers',
      layer_ids: layers.map((layer) => String(layer._mapspecLayerId || layer.id)),
    });
  } catch (err) {
    useHudStore.getState().setLayers(previous);
    toastRollback('图层排序', err);
  }
}

export async function setLayerOpacityAndCommit(layerId: string, opacity: number): Promise<void> {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  const previous = layer?.opacity ?? 1;
  useHudStore.getState().updateLayer(layerId, { opacity });
  try {
    await commitLayerPresentation({ layerId, opacity });
  } catch (err) {
    useHudStore.getState().updateLayer(layerId, { opacity: previous });
    toastRollback('不透明度调整', err);
  }
}
