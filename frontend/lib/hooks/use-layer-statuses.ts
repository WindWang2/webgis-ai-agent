'use client';
/**
 * useLayerStatuses — subscribe the Layer Manager to its derivation inputs
 * (Workspace V2 / Goal C2).
 *
 * Subscribes to the two external generations that can change a layer's
 * derived status — the render-evidence stash (latest observation) and the
 * MapSpec live generation (revision advances) — and derives the closed
 * status vocabulary per layer row via the pure `deriveLayerStatus`.
 * Read-only: no store writes, no MapSpec writes.
 */
import { useMemo, useSyncExternalStore } from 'react';
import type { Layer } from '@/lib/types/layer';
import {
  getLayerEvidence,
  getLayerEvidenceGeneration,
  subscribeLayerEvidence,
} from '@/lib/layers/render-evidence';
import { deriveLayerStatus, type LayerStatus } from '@/lib/layers/layer-status';
import {
  getMapSpecLiveGeneration,
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';

export function useLayerStatuses(layers: Layer[]): Record<string, LayerStatus> {
  // Two independent external stores — one subscription each; both cheap
  // (generation counters, no snapshots cloned per render). The generations
  // are ALSO the memo keys: evidence/revision changes must re-derive, or
  // the badges freeze until an unrelated layers identity change.
  const evidenceGeneration = useSyncExternalStore(subscribeLayerEvidence, getLayerEvidenceGeneration);
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);

  return useMemo(() => {
    const { revision } = getMapSpecSessionCursor();
    const out: Record<string, LayerStatus> = {};
    for (const layer of layers) {
      out[layer.id] = deriveLayerStatus({
        layer,
        evidence: getLayerEvidence(layer.id),
        currentRevision: revision,
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generations are the change signals
  }, [layers, evidenceGeneration, specGeneration]);
}
