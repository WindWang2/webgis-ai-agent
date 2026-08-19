import { ApiError, apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
import { getMapSpecSessionCursor, setMapSpecRevision } from '@/lib/mapspec/session-cursor';

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

  try {
    const data = await apiFetch<MutationResponse>(
      `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
      {
        method: 'POST',
        body: {
          intent: 'patch_layer_presentation',
          expected_revision: revision,
          layer_id: mapspecLayerId(patch.layerId),
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
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) throw err;
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    if (!superseded.mapspec) throw err;
    applyCommittedMapSpec(superseded.mapspec);
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
