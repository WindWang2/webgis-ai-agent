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

export async function toggleLayerAndCommit(layerId: string): Promise<void> {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  const previous = layer?.visible !== false;
  useHudStore.getState().toggleLayer(layerId);
  try {
    await commitLayerPresentation({ layerId, visible: !previous });
  } catch {
    useHudStore.getState().updateLayer(layerId, { visible: previous });
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
  } catch {
    useHudStore.getState().setLayers(previous);
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
  } catch {
    useHudStore.getState().setLayers(previous);
  }
}

export async function setLayerOpacityAndCommit(layerId: string, opacity: number): Promise<void> {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  const previous = layer?.opacity ?? 1;
  useHudStore.getState().updateLayer(layerId, { opacity });
  try {
    await commitLayerPresentation({ layerId, opacity });
  } catch {
    useHudStore.getState().updateLayer(layerId, { opacity: previous });
  }
}
