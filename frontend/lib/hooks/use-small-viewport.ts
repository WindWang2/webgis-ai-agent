'use client';
import { useSyncExternalStore } from 'react';
import { VIEWPORT_COLLAPSE_CANVAS_HEIGHT } from '@/lib/map-components/resolve-layout';

/**
 * Small-viewport signal for the Scenario H collapse suggestion (live side).
 *
 * The pure rule lives in resolve-layout.suggestViewportCollapses (single
 * definition, export uses its own canvas); this hook only answers "is the
 * live viewport below the collapse threshold" — matchMedia-driven, SSR-safe
 * (false before mount), reduced to a boolean snapshot so consumers re-render
 * only on threshold crossings, never per pixel.
 */
const QUERY = `(max-height: ${VIEWPORT_COLLAPSE_CANVAS_HEIGHT - 1}px)`;

/** Module-level MQL：snapshot 每次渲染都会读 —— 复用同一实例。 */
let cachedMql: MediaQueryList | null = null;

function mql(): MediaQueryList | null {
  if (typeof window === 'undefined' || !window.matchMedia) return null;
  if (cachedMql === null) {
    try {
      cachedMql = window.matchMedia(QUERY);
    } catch {
      return null;
    }
  }
  return cachedMql;
}

function subscribe(onChange: () => void): () => void {
  const list = mql();
  if (!list) return () => {};
  list.addEventListener('change', onChange);
  return () => list.removeEventListener('change', onChange);
}

function snapshot(): boolean {
  return mql()?.matches ?? false;
}

export function useSmallViewport(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
