/**
 * F4: viewport dual-writer sequencing.
 *
 * The backend `viewport` map-state key has two writers — the turn-start write
 * (execution_engine) and the client's throttled POST. Both carry a monotonic
 * per-session seq so out-of-order arrivals resolve to the latest seq. This
 * module owns the CLIENT side of that contract:
 *
 * - `nextViewportSeq` bumps a monotonic counter every time the client sends
 *   viewport state (throttled POST during thinking/acting, and the live
 *   snapshot carried by send()).
 * - `coalesceViewportState` decides whether incoming (server) viewport state
 *   should be applied: it is applied only when its seq is strictly newer than
 *   anything the client already sent or saw. Older/equal seqs are stale and
 *   ignored, so a session restore can never fly the map back to an older view.
 *
 * The tracker is per-session: callers reset it when the active session
 * changes (see useMapBridge / useWorkspaceSession), because server seqs are
 * scoped to a single session.
 */
export interface ViewportState {
  center: [number, number];
  zoom: number;
  bearing?: number;
  pitch?: number;
}

export interface ViewportSeqTracker {
  current: number;
}

export function createViewportSeqTracker(initial = 0): ViewportSeqTracker {
  return { current: initial };
}

/** Bump the tracker and return the next seq (call before sending viewport state). */
export function nextViewportSeq(tracker: ViewportSeqTracker): number {
  tracker.current += 1;
  return tracker.current;
}

/**
 * Coalesce incoming server viewport state against the client's current seq.
 *
 * Returns the seq to keep and the viewport to apply — or `null` when the
 * incoming state is stale. Missing seq info (server that predates the
 * sequencing contract) is treated as "unknown", not "stale": the state is
 * applied, so legacy sessions still restore correctly.
 */
export function coalesceViewportState(
  tracker: ViewportSeqTracker,
  incomingSeq: number | undefined,
  incoming: ViewportState | undefined,
): { seq: number; viewport: ViewportState | null } {
  if (!incoming) return { seq: tracker.current, viewport: null };
  if (incomingSeq !== undefined) {
    if (incomingSeq <= tracker.current) {
      // Stale: the client has already sent or seen newer viewport state.
      return { seq: tracker.current, viewport: null };
    }
    tracker.current = incomingSeq;
  }
  return { seq: tracker.current, viewport: incoming };
}

/**
 * Shared per-session tracker (module-level singleton). Both useMapBridge
 * (POST / send) and useWorkspaceSession (session restore) write to it, and
 * both reset it when the active session changes.
 */
export const viewportSeqTracker: ViewportSeqTracker = createViewportSeqTracker();

/** Reset the shared tracker (call when the active session changes). */
export function resetViewportSeq(): void {
  viewportSeqTracker.current = 0;
}
