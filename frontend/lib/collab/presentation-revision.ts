/**
 * Per-layer presentation revision. Local commits and remote collab events
 * share this record so a late rev N cannot overwrite an already adopted N+1
 * on the same layer. Other layers are unaffected (no global gate).
 */

const layerRevision = new Map<string, number>();

export function noteLayerPresentationRevision(layerId: string, revision: number): void {
  if (!layerId || !Number.isFinite(revision)) return;
  const prev = layerRevision.get(layerId);
  if (prev == null || revision >= prev) layerRevision.set(layerId, revision);
}

export function shouldAdoptLayerPresentation(layerId: string, revision: number): boolean {
  if (!layerId || !Number.isFinite(revision)) return false;
  const prev = layerRevision.get(layerId);
  return prev == null || revision >= prev;
}

export function resetLayerPresentationRevisions(): void {
  layerRevision.clear();
}
