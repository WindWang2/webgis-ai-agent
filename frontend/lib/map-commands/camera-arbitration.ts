/**
 * Camera arbitration (Harness–Map Interaction V3).
 *
 * Human-vs-AI camera ownership, deliberately minimal:
 * - `map-panel.tsx` reports user gestures (dragstart/zoomstart/rotatestart/pitchstart
 *   events that carry an `originalEvent` — programmatic moves have none).
 * - While a user gesture is active, AI camera commands WAIT (bounded) instead of
 *   fighting the gesture.
 * - If a user gesture starts while an AI camera animation is in flight, subscribers
 *   are notified so the command can stop the animation and end as `cancelled`
 *   (superseded_by_user) instead of snapping back.
 *
 * Module-level singleton: one live map per page, so a shared registry is correct
 * and keeps MapCommandContext free of extra plumbing. All timers are injectable-
 * friendly (setTimeout only) so vitest fake timers work.
 */

type GestureListener = () => void;

let gesturing = false;
let activeGestures = 0;
const gestureStartListeners = new Set<GestureListener>();
const gestureEndListeners = new Set<GestureListener>();

/** User gesture started (map-panel gesture handler, originalEvent present). */
export function notifyUserGestureStart(): void {
  activeGestures += 1;
  const wasGesturing = gesturing;
  gesturing = true;
  if (!wasGesturing) {
    for (const cb of Array.from(gestureStartListeners)) cb();
  }
}

/** User gesture ended (dragend/zoomend/rotateend/pitchend). */
export function notifyUserGestureEnd(): void {
  activeGestures = Math.max(0, activeGestures - 1);
  if (activeGestures === 0 && gesturing) {
    gesturing = false;
    for (const cb of Array.from(gestureEndListeners)) cb();
  }
}

export function isUserGesturing(): boolean {
  return gesturing;
}

/**
 * Subscribe to "user gesture started" (used to cancel in-flight AI camera
 * animations). Returns an unsubscribe function.
 */
export function onUserGestureStart(cb: GestureListener): () => void {
  gestureStartListeners.add(cb);
  return () => gestureStartListeners.delete(cb);
}

/**
 * Resolve once no user gesture is active. If none is active, resolve immediately.
 * Otherwise wait for gesture end, bounded by `timeoutMs` (default 3000) — after
 * the timeout the promise resolves anyway (callers proceed; a stuck gesture must
 * never wedge the action queue).
 */
export function waitForGestureEnd(timeoutMs = 3000): Promise<void> {
  if (!gesturing) return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      gestureEndListeners.delete(onEnd);
      resolve();
    };
    const onEnd = () => finish();
    const timer = setTimeout(finish, timeoutMs);
    gestureEndListeners.add(onEnd);
  });
}

/** Test-only: reset all arbitration state between tests. */
export function _resetCameraArbitrationForTests(): void {
  gesturing = false;
  activeGestures = 0;
  gestureStartListeners.clear();
  gestureEndListeners.clear();
}
