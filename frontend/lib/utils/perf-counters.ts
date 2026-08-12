/**
 * Dev/test-only performance counters (Harness–Map Interaction V3, FE-3).
 *
 * The MapSpecRuntime constructs its RenderDebouncer with an `onFrameStats`
 * sink that feeds this module (the debouncer's FrameStats instrument existed
 * but was never wired — findings E5). Tests assert work counts through
 * `getPerfCounters()` so render-volume regressions (e.g. a reconcile storm
 * re-applying unchanged specs) fail loudly instead of silently degrading.
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
}

const counters: PerfCounters = { debounceFrames: 0, executedRenderOps: 0 };

/** Sink for RenderDebouncer's onFrameStats option. */
export function recordDebounceFrame(stats: PerfFrameStats): void {
  counters.debounceFrames += 1;
  counters.executedRenderOps += stats.executedOps;
}

/** Snapshot of the counters (tests read this; do not use in prod code paths). */
export function getPerfCounters(): Readonly<PerfCounters> {
  return { ...counters };
}

/** Test-only: reset counters between tests. */
export function resetPerfCounters(): void {
  counters.debounceFrames = 0;
  counters.executedRenderOps = 0;
}
