'use client';

/**
 * Lazy, metadata-first output descriptor for a result.
 *
 * Fetches `GET /layers/descriptor/{ref}` (feature_count / bbox / geometry_types /
 * estimated_bytes) — NEVER the full GeoJSON payload (spec §16). The fetch is:
 *  - per-ref deduped (module-level in-flight map + cache),
 *  - aborted on result/session switch or unmount,
 *  - guarded by a generation counter so a slow response for result A can never
 *    overwrite result B (spec §17).
 *
 * CRS is intentionally not enriched (the descriptor carries none); it stays
 * "Unknown" in the UI (spec §9).
 */
import { useEffect, useRef } from 'react';
import { apiFetch, isApiError } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { devOnly } from '@/lib/utils/logger';
import type { AnalysisResult, LayerDescriptor } from '@/lib/results/types';

interface InFlightEntry {
  promise: Promise<LayerDescriptor | null>;
  controller: AbortController;
}

// Module-level dedup: one in-flight request per ref + a small positive cache so
// re-mounting the same result detail (e.g. after a tab round-trip) doesn't refetch.
const inFlight = new Map<string, InFlightEntry>();
const cache = new Map<string, LayerDescriptor | null>();
const CACHE_TTL_MS = 30_000;

function cacheKey(ref: string, sessionId: string | null | undefined): string {
  return `${sessionId ?? '_'}::${ref}`;
}

async function fetchDescriptor(
  ref: string,
  sessionId: string | null | undefined,
  ownerToken: string | null | undefined,
  outerSignal: AbortSignal,
): Promise<LayerDescriptor | null> {
  const key = cacheKey(ref, sessionId);
  const cached = cache.get(key);
  if (cached !== undefined) return cached;

  const existing = inFlight.get(key);
  if (existing) {
    // Attach abort linkage: if the outer call aborts we still let the shared
    // request finish for other consumers, but we race-abort our own wait.
    try {
      return await existing.promise;
    } catch {
      return null;
    }
  }

  const controller = new AbortController();
  const onOuterAbort = () => controller.abort();
  outerSignal.addEventListener('abort', onOuterAbort, { once: true });

  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  const path = `/api/v1/layers/descriptor/${encodeURIComponent(ref)}${params.toString() ? `?${params}` : ''}`;

  const promise = apiFetch<LayerDescriptor>(path, {
    signal: controller.signal,
    ownerToken: ownerToken ?? undefined,
    label: 'Result descriptor error',
  })
    .then((data) => {
      cache.set(key, data ?? null);
      scheduleEvict(key);
      return data ?? null;
    })
    .catch((err) => {
      if (!isApiError(err)) devOnly.warn('[ResultDescriptor] fetch failed', err);
      // Negative-cache briefly to avoid hammering on 404 (session expired).
      cache.set(key, null);
      scheduleEvict(key);
      return null;
    })
    .finally(() => {
      inFlight.delete(key);
      outerSignal.removeEventListener('abort', onOuterAbort);
    });

  inFlight.set(key, { promise, controller });
  return promise;
}

function scheduleEvict(key: string): void {
  setTimeout(() => cache.delete(key), CACHE_TTL_MS);
}

/**
 * Enrich the given result's output with descriptor metadata. No-op when the
 * result has no ref, or is already enriched, or the descriptor is unavailable.
 */
export function useResultDescriptor(
  result: AnalysisResult | null,
  sessionId: string | null | undefined,
  ownerToken: string | null | undefined,
): void {
  const enrichResultOutput = useHudStore((s) => s.enrichResultOutput);
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const ref = result?.outputs[0]?.ref;
  const resultId = result?.id;
  const alreadyEnriched = !!result?.outputs[0]?.featureCount || !!result?.outputs[0]?.estimatedBytes;

  useEffect(() => {
    if (!resultId || !ref || alreadyEnriched) return;
    const gen = ++generationRef.current;
    const controller = new AbortController();
    abortRef.current = controller;

    fetchDescriptor(ref, sessionId, ownerToken, controller.signal).then((descriptor) => {
      // Generation guard: drop responses that no longer match the current result.
      if (gen !== generationRef.current || controller.signal.aborted) return;
      if (descriptor) enrichResultOutput(resultId, ref, descriptor);
    });

    return () => {
      controller.abort();
      if (abortRef.current === controller) abortRef.current = null;
    };
  }, [resultId, ref, alreadyEnriched, sessionId, ownerToken, enrichResultOutput]);
}
