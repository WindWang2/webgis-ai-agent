/**
 * Dev/test-only performance counters (Harness–Map Interaction V3, FE-3).
 *
 * The MapSpecRuntime constructs its RenderDebouncer with an `onFrameStats`
 * sink that feeds this module (the debouncer's FrameStats instrument existed
 * but was never wired — findings E5). Tests assert work counts through
 * `getPerfCounters()` so render-volume regressions (e.g. a reconcile storm
 * re-applying unchanged specs) fail loudly instead of silently degrading.
 *
 * AC-06 (ADR-0155) P3: patch-class counters — the incremental paint/layout
 * patch path must show ZERO remove/add for paint-only changes, and any
 * fallback to the recompile safety net must be visible in counts + evidence.
 *
 * Counters are plain integers — no allocations beyond the incremented value —
 * so wiring them in production costs nothing measurable.
 */

export interface PerfFrameStats {
  executedOps: number;
  remainingOps: number;
  durationMs: number;
  budgetExceeded: boolean;
}

interface PerfCounters {
  /** Number of debouncer frames processed (each processQueue run). */
  debounceFrames: number;
  /** Total render operations executed across all debouncer frames. */
  executedRenderOps: number;
  /** AC-06: layers removed via removeLayerSafe (recompile path included). */
  layerRemoves: number;
  /** AC-06: layers added via addLayerSafe (recompile path included). */
  layerAdds: number;
  /** AC-06 P3: paint-only patches applied via setPaintProperty (no churn). */
  layerPaintPatches: number;
  /** AC-06 P3: layout-only patches applied via setLayoutProperty (no churn). */
  layerLayoutPatches: number;
  /** AC-06 P3: filter patches applied via setFilter (V4 fast path). */
  layerFilterPatches: number;
  /** AC-06 P3: incremental patches that FAILED and fell back to recompile. */
  recompileFallbacks: number;
}

const counters: PerfCounters = {
  debounceFrames: 0,
  executedRenderOps: 0,
  layerRemoves: 0,
  layerAdds: 0,
  layerPaintPatches: 0,
  layerLayoutPatches: 0,
  layerFilterPatches: 0,
  recompileFallbacks: 0,
};

/** Sink for RenderDebouncer's onFrameStats option. */
export function recordDebounceFrame(stats: PerfFrameStats): void {
  counters.debounceFrames += 1;
  counters.executedRenderOps += stats.executedOps;
}

/** AC-06 P3: individual patch-class counters (called by MapSpecRuntime). */
export function recordLayerRemove(): void {
  counters.layerRemoves += 1;
}
export function recordLayerAdd(): void {
  counters.layerAdds += 1;
}
export function recordPaintPatch(): void {
  counters.layerPaintPatches += 1;
}
export function recordLayoutPatch(): void {
  counters.layerLayoutPatches += 1;
}
export function recordFilterPatch(): void {
  counters.layerFilterPatches += 1;
}
export function recordRecompileFallback(): void {
  counters.recompileFallbacks += 1;
}

/** Snapshot of the counters (tests read this; do not use in prod code paths). */
export function getPerfCounters(): Readonly<PerfCounters> {
  return { ...counters };
}

/** Test-only: reset counters between tests. */
export function resetPerfCounters(): void {
  counters.debounceFrames = 0;
  counters.executedRenderOps = 0;
  counters.layerRemoves = 0;
  counters.layerAdds = 0;
  counters.layerPaintPatches = 0;
  counters.layerLayoutPatches = 0;
  counters.layerFilterPatches = 0;
  counters.recompileFallbacks = 0;
}
