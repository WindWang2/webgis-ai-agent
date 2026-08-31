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

function subscribe(onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {};
  const mql = window.matchMedia(QUERY);
  mql.addEventListener('change', onChange);
  return () => mql.removeEventListener('change', onChange);
}

function snapshot(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(QUERY).matches;
}

export function useSmallViewport(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
