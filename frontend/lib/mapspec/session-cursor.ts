/** Session cursor for user MapSpec Mutations (#639). No session → chrome stays local. */

import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { PendingPresentation } from '@/lib/mapspec/live-spec';

let sessionId: string | undefined;
let revision = 0;
let ownerToken: string | null = null;
let committed: MapSpec | null = null;
let pending: PendingPresentation = {};
let pendingRemoved: string[] = [];
let generation = 0;
const listeners = new Set<() => void>();

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

function resetLiveState(): void {
  committed = null;
  pending = {};
  pendingRemoved = [];
}

export function subscribeMapSpecLive(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getMapSpecLiveGeneration(): number {
  return generation;
}

export function setMapSpecSessionCursor(
  nextId: string | undefined,
  nextRevision = 0,
  nextOwnerToken: string | null = null,
): void {
  sessionId = nextId;
  revision = Number.isFinite(nextRevision) ? nextRevision : 0;
  ownerToken = nextOwnerToken;
  resetLiveState();
  emit();
}

export function getMapSpecSessionCursor(): {
  sessionId: string | undefined;
  revision: number;
  ownerToken: string | null;
} {
  return { sessionId, revision, ownerToken };
}

export function setMapSpecRevision(nextRevision: number): void {
  revision = nextRevision;
}

export function getCommittedMapSpec(): MapSpec | null {
  return committed;
}

export function commitMapSpecDocument(mapspec: unknown): void {
  if (!mapspec || typeof mapspec !== 'object') return;
  const spec = mapspec as MapSpec;
  if (!Array.isArray(spec.layers) && (spec.sources == null || typeof spec.sources !== 'object')) {
    return;
  }
  committed = spec;
  emit();
}

export function getPendingPresentation(): PendingPresentation {
  return pending;
}

export function mergePendingPresentation(
  layerId: string,
  patch: { visible?: boolean; opacity?: number },
): void {
  if (!layerId) return;
  if (patch.visible === undefined && patch.opacity === undefined) return;
  pending = {
    ...pending,
    [layerId]: { ...pending[layerId], ...patch },
  };
  emit();
}

export function clearPendingPresentation(layerId?: string): void {
  if (!layerId) {
    if (Object.keys(pending).length === 0) return;
    pending = {};
  } else if (pending[layerId]) {
    const { [layerId]: _dropped, ...rest } = pending;
    pending = rest;
  } else {
    return;
  }
  emit();
}

export function getPendingRemoved(): string[] {
  return pendingRemoved;
}

export function markPendingRemoved(layerId: string): void {
  if (!layerId || pendingRemoved.includes(layerId)) return;
  pendingRemoved = [...pendingRemoved, layerId];
  emit();
}

export function clearPendingRemoved(layerId?: string): void {
  if (!layerId) {
    if (pendingRemoved.length === 0) return;
    pendingRemoved = [];
  } else if (pendingRemoved.includes(layerId)) {
    pendingRemoved = pendingRemoved.filter((id) => id !== layerId);
  } else {
    return;
  }
  emit();
}
