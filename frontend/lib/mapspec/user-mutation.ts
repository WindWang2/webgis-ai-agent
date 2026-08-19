import { apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { presentationFromMapSpec } from '@/lib/session/map-state-restore';
import { getMapSpecSessionCursor, setMapSpecRevision } from '@/lib/mapspec/session-cursor';

export interface LayerPresentationPatch {
  layerId: string;
  visible?: boolean;
  opacity?: number;
}

interface MutationResponse {
  success: boolean;
  mutation_revision?: number;
  mapspec?: { layers?: any[] };
}

function mapspecLayerId(layerId: string): string {
  const layer = useHudStore.getState().layers.find((item) => item.id === layerId);
  return String(layer?._mapspecLayerId || layerId);
}

export async function commitLayerPresentation(patch: LayerPresentationPatch): Promise<void> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return;
  if (patch.visible === undefined && patch.opacity === undefined) return;

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
  const pres = presentationFromMapSpec(data.mapspec, mapspecLayerId(patch.layerId));
  if (pres.visible !== undefined || pres.opacity !== undefined) {
    useHudStore.getState().updateLayer(patch.layerId, pres);
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
