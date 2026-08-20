'use client';

import { useHudStore } from './useHudStore';
import { apiFetch } from '@/lib/api/transport';
import type { Layer } from '@/lib/types/layer';

export type EnsureLayerReason = 'filter' | 'export-vector' | 'selection-detail' | 'attribute-table';

export interface EnsureLayerResult {
  status: 'hydrated' | 'already-hydrated' | 'single-feature' | 'fallback' | 'no-ref' | 'not-found';
  feature?: Record<string, unknown>;
  source?: unknown;
}

const VECTOR_TILE_THRESHOLD = 5000;
export const FEATURE_ID_KEYS = ['id', 'OBJECTID', 'fid', 'osm_id', '@id', 'featureId', 'feature_id'];

let _currentSessionId: string | undefined;
let _currentOwnerToken: string | null | undefined;

export function setLayerDataSession(sessionId: string | undefined, ownerToken: string | null | undefined): void {
  _currentSessionId = sessionId;
  _currentOwnerToken = ownerToken;
}

export function getLayerDataSession(): { sessionId: string | undefined; ownerToken: string | null | undefined } {
  return { sessionId: _currentSessionId, ownerToken: _currentOwnerToken };
}

export function isMvtLayer(layer: Layer): boolean {
  return !!(
    layer._tileUrl &&
    layer._descriptor &&
    layer._descriptor.mvt_capable &&
    layer._descriptor.feature_count > VECTOR_TILE_THRESHOLD
  );
}

function extractSessionIdFromTileUrl(tileUrl?: string): string | undefined {
  if (!tileUrl) return undefined;
  try {
    const url = new URL(tileUrl, 'http://dummy');
    return url.searchParams.get('session_id') ?? undefined;
  } catch {
    return undefined;
  }
}

const pendingHydrations = new Map<string, Promise<EnsureLayerResult>>();

export async function ensureLayerData(
  layerId: string,
  reason: EnsureLayerReason,
  opts?: { featureId?: string | number; signal?: AbortSignal },
): Promise<EnsureLayerResult> {
  const state = useHudStore.getState();
  const layer = state.layers.find((l) => l.id === layerId) as Layer | undefined;
  if (!layer) return { status: 'not-found' };
  if (!layer._refId) return { status: 'no-ref', source: layer.source };

  const sid = _currentSessionId ?? extractSessionIdFromTileUrl(layer._tileUrl);
  const token = _currentOwnerToken;

  if (reason === 'selection-detail') {
    let fid: string | number | undefined = opts?.featureId;
    if (fid == null) {
      const sel: any = (state as any).selectedFeature;
      if (sel) {
        // Try to match selection to this layer (parent id logic)
        const selLayerId = sel.layerId as string | undefined;
        const matchesLayer =
          !selLayerId ||
          selLayerId === layerId ||
          selLayerId.startsWith(layerId) ||
          layerId.startsWith(selLayerId) ||
          selLayerId.includes(layerId) ||
          layerId.includes(selLayerId);
        if (matchesLayer) {
          fid = sel.featureId as string | number | undefined;
          if (fid == null && sel.properties && typeof sel.properties === 'object') {
            for (const k of FEATURE_ID_KEYS) {
              const v = (sel.properties as Record<string, unknown>)[k];
              if (v != null && v !== '') {
                fid = v as string | number;
                break;
              }
            }
          }
          // `h-` is the synthetic content-hash fallback (h-xxxxxxxx from
          // shortContentHash) when a feature has no stable id — see map-panel
          // commitSelection and use-sse-stream resolveFeatureId; not a real id.
          if (typeof fid === 'string' && fid.startsWith('h-')) fid = undefined;
          if (typeof fid === 'string' && !fid.trim()) fid = undefined;
        }
      }
    }
    if (fid == null || (typeof fid === 'string' && !fid.trim())) {
      return { status: 'fallback' };
    }
    const ref = layer._refId;
    const url = `/api/v1/layers/data/${encodeURIComponent(ref)}/feature/${encodeURIComponent(String(fid))}?session_id=${encodeURIComponent(sid ?? '')}`;
    try {
      const feature = await apiFetch<Record<string, unknown>>(url, {
        ownerToken: token ?? undefined,
        label: 'Feature detail error',
        signal: opts?.signal,
      });
      return { status: 'single-feature', feature };
    } catch (e: any) {
      if (e && (e.status === 404 || e?.status === 403)) {
        // 404 → feature not found → honest fallback; 403 should propagate? but treat as fallback for selection UX
        if (e.status === 404) return { status: 'fallback' };
      }
      throw e;
    }
  }

  // filter / export-vector / attribute-table: full hydration on demand
  if (!isMvtLayer(layer)) {
    return { status: 'already-hydrated', source: layer.source };
  }
  const src: any = layer.source;
  if (src && Array.isArray(src.features) && src.features.length > 0) {
    return { status: 'already-hydrated', source: src };
  }

  // All three full-hydration reasons fetch the same FC — share one in-flight key.
  const cacheKey = layerId;
  if (pendingHydrations.has(cacheKey)) {
    return pendingHydrations.get(cacheKey)!;
  }
  const promise = (async (): Promise<EnsureLayerResult> => {
    const ref = layer._refId!;
    const url = `/api/v1/layers/data/${encodeURIComponent(ref)}?session_id=${encodeURIComponent(sid ?? '')}`;
    const geojson = await apiFetch<any>(url, {
      ownerToken: token ?? undefined,
      label: 'Layer data error',
    });
    useHudStore.getState().updateLayer(layerId, { source: geojson as any });
    return { status: 'hydrated', source: geojson };
  })().finally(() => {
    pendingHydrations.delete(cacheKey);
  });
  pendingHydrations.set(cacheKey, promise);
  return promise;
}

// #667 exporter helper: hydrate MVT layers on demand (shared for both vector-export sites)
export async function hydrateMvtLayers(layers: Layer[], reason: EnsureLayerReason = 'export-vector'): Promise<void> {
  const targets = layers.filter(isMvtLayer);
  if (targets.length === 0) return;
  await Promise.all(targets.map((l) => ensureLayerData(l.id, reason).catch(() => {})));
}

// For tests: clear pending cache
export function _clearPendingHydrationsForTests(): void {
  pendingHydrations.clear();
}
