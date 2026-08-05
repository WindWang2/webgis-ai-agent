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

/**
 * Test-only: drop the cached worker so the next call re-evaluates the
 * environment (e.g. to exercise the fallback path after a worker test).
 */
export function _resetWorkerBridgeForTests(): void {
  sharedWorker = null;
  nextRequestId = 0;
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

  return new Promise<SpecPatch>((resolve) => {
    const id = `diff-${++nextRequestId}`;

    const onMessage = (event: MessageEvent<DiffResponse>) => {
      if (event.data?.id !== id) return;
      worker.removeEventListener("message", onMessage);
      resolve(event.data.patch);
    };

    worker.addEventListener("message", onMessage);
    const request: DiffRequest = { id, prev, next };
    worker.postMessage(request);
  });
}
