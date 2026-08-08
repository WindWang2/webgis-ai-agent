import { diffSpecs, SpecPatch } from "./reconciler";
import type { MapSpec } from "./types";

/**
 * WorkerReconcilerBridge — offload `diffSpecs` to a Web Worker so large-spec
 * tree comparison never blocks the UI thread (ADR-0036 / raf_render_debouncer
 * design, issue #227).
 *
 * Falls back to running `diffSpecs` synchronously on the main thread when a
 * Worker is unavailable (SSR, Node, test environments, bundler without worker
 * support). The fallback is transparent: callers always `await` the same shape.
 *
 * NOTE: this module deliberately does NOT import `./reconciler.worker` at the
 * top level. Importing the worker entry on the main thread would clobber
 * `window.onmessage` (its `self.onmessage` registration is guarded only by
 * `typeof self !== "undefined"`, which is true on the browser main thread).
 * The worker is referenced by URL only.
 */

interface DiffRequest {
  id: string;
  prev: MapSpec | null;
  next: MapSpec;
}

interface DiffResponse {
  id: string;
  patch: SpecPatch;
  error?: string;
}

let sharedWorker: Worker | null = null;
let nextRequestId = 0;
let activeRequestsCount = 0;

/**
 * Hard ceiling for how long `diffSpecsAsync` waits on a worker before
 * resolving with the empty patch. Guards against the worker silently wedging
 * (script error, OOM) without ever posting — otherwise the reconcile pipeline
 * would hang with no retry. Exported so tests can advance fake timers by
 * exactly one window.
 */
export const DIFF_WORKER_TIMEOUT_MS = 30_000;

/**
 * The no-op patch shape: "nothing to apply", identical to what `diffSpecs`
 * produces for identical specs.
 */
const EMPTY_PATCH: SpecPatch = { sources: [], layers: [] };

/**
 * Test-only: drop the cached worker so the next call re-evaluates the
 * environment (e.g. to exercise the fallback path after a worker test).
 */
export function _resetWorkerBridgeForTests(): void {
  sharedWorker = null;
  nextRequestId = 0;
  activeRequestsCount = 0;
}

function createWorker(): Worker | null {
  if (sharedWorker) return sharedWorker;
  try {
    if (typeof Worker === "undefined" || typeof URL === "undefined") return null;
    sharedWorker = new Worker(new URL("./reconciler.worker", import.meta.url), {
      type: "module",
    });
    return sharedWorker;
  } catch {
    // Bundler / environment without worker support — fall back to sync diff.
    return null;
  }
}

/**
 * Diff two specs, off the main thread when a Worker is available.
 * Resolves with the SpecPatch (or an empty patch on worker error — the caller
 * treats that as "nothing to apply", same as diffSpecs' no-op contract).
 */
export function diffSpecsAsync(prev: MapSpec | null, next: MapSpec): Promise<SpecPatch> {
  const worker = createWorker();
  if (!worker) {
    return Promise.resolve(diffSpecs(prev, next));
  }

  activeRequestsCount++;

  return new Promise<SpecPatch>((resolve) => {
    const id = `diff-${++nextRequestId}`;

    let settled = false;

    /**
     * Settle the request exactly once. Decrement active requests count;
     * terminate the worker only when no requests are pending.
     */
    const finish = (patch: SpecPatch) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      worker.removeEventListener("message", onMessage);
      worker.onerror = null;
      worker.onmessageerror = null;
      workerWithOnexit.onexit = null;
      activeRequestsCount = Math.max(0, activeRequestsCount - 1);
      if (activeRequestsCount === 0) {
        worker.terminate();
        sharedWorker = null;
      }
      resolve(patch);
    };

    const onMessage = (event: MessageEvent<DiffResponse>) => {
      if (event.data?.id !== id) return;
      finish(event.data.patch);
    };

    worker.addEventListener("message", onMessage);

    // lib.dom no longer types the deprecated `onexit` (Chrome-only); browsers
    // still fire it with a CloseEvent whose `code` is the exit status.
    const workerWithOnexit = worker as unknown as {
      onexit: ((event: CloseEvent) => void) | null;
    };

    // Worker died before posting (script error, OOM, ...) — no-op contract.
    worker.onerror = () => finish(EMPTY_PATCH);
    // Structured-clone / deserialization failure of the response.
    worker.onmessageerror = () => finish(EMPTY_PATCH);
    // Explicit non-zero exit without a response.
    workerWithOnexit.onexit = (event) => {
      if (event.code !== 0) finish(EMPTY_PATCH);
    };

    // All settle paths fire asynchronously, so `timer` is always assigned
    // before `finish` can run.
    const timer = setTimeout(() => finish(EMPTY_PATCH), DIFF_WORKER_TIMEOUT_MS);

    const request: DiffRequest = { id, prev, next };
    worker.postMessage(request);
  });
}
