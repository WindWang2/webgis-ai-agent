import { diffSpecs, SpecPatch } from "./reconciler";
import type { GeoJSONMapSpecSource, MapSpec, MapSpecSource, RasterMapSpecSource } from "./types";

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
 * FE-01: how long the worker is kept warm after the last in-flight diff
 * settles. Reconciles serialize (reconcileTail in runtime.ts), so the active
 * request count almost always returns to 0 between two reconciles. Terminating
 * at 0 made the next reconcile pay the full module-worker boot cost. The idle
 * timeout keeps the worker warm across the typical reconcile gap; only an
 * explicit `disposeWorker()` (called from MapSpecRuntime.dispose) tears it down
 * immediately. Exported so tests can advance fake timers by exactly one window.
 */
export const DIFF_WORKER_IDLE_MS = 10_000;

/**
 * The no-op patch shape: "nothing to apply", identical to what `diffSpecs`
 * produces for identical specs.
 */
const EMPTY_PATCH: SpecPatch = { sources: [], layers: [] };

/**
 * FIX-3-6: set when `diffSpecsAsync` resolves EMPTY_PATCH because the worker
 * failed/timed out (not because the specs are actually equal). The runtime
 * consults this via `consumeDiffLastFailed()`: an empty patch + this flag means
 * "the diff is unknown", so it must NOT advance `appliedSpec` — interactive
 * ids derive from appliedSpec, and advancing would claim the map reflects
 * layers it never received. Reset on any successful diff (the flag can never
 * leak into a later reconcile, whose success clears it).
 */
let lastDiffFailed = false;

/**
 * FIX-3-6: read and clear the failed-diff flag atomically. Used by
 * MapSpecRuntime.applyPatchDebounced to detect a worker-failure empty patch;
 * exported for the runtime test suite as well.
 */
export function consumeDiffLastFailed(): boolean {
  const v = lastDiffFailed;
  lastDiffFailed = false;
  return v;
}

/**
 * Test-only: drop the cached worker so the next call re-evaluates the
 * environment (e.g. to exercise the fallback path after a worker test). Also
 * cancels any pending idle-termination timer and clears the inlineData /
 * imageRef identity caches so tests start from a clean slate.
 */
export function _resetWorkerBridgeForTests(): void {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  if (sharedWorker) {
    try { sharedWorker.terminate(); } catch { /* already gone */ }
  }
  sharedWorker = null;
  nextRequestId = 0;
  activeRequestsCount = 0;
  inlineTokenCache = new WeakMap();
  lastInlineDataBySource = new Map();
  imageTokenByContent.clear();
  lastDiffFailed = false;
}

// ── FE-02: inline GeoJSON stripping ────────────────────────────────────────
//
// postMessage clones its payload via structured clone. Inline GeoJSON
// FeatureCollections (large POI layers) were crossing that boundary twice per
// reconcile — as `prev` and as `next` — even though `diffSpecs` only needs to
// compare data IDENTITY, not contents. We strip `inlineData` to a stable
// identity token before posting; the worker's deep-equality check then runs on
// a tiny token instead of megabytes of coordinates.
//
// Identity contract (must match diffSpecs' previous behavior):
//  - same object reference as a previously-seen payload → same token (the
//    common case: hudStateToMapSpec reuses `layer.source` by reference across
//    reconciles, so unchanged layers diff to "no change").
//  - a new object that is deep-equal to the last payload for that source →
//    reuse the last token (preserves the prior "no change" outcome).
//  - otherwise → a fresh token (a real data change → "update").
//
// The patch returned by the worker carries the STRIPPED source objects in
// `next`; `rehydratePatch` maps them back to the original (unstripped) source
// from the `next` spec the caller passed in, so MapSpecRuntime.applySource
// still sees the real `inlineData`.

let inlineTokenSeq = 0;
let inlineTokenCache: WeakMap<object, number> = new WeakMap();
// Per source id: the last inlineData object + the token assigned to it, so a
// new-but-equal payload can reuse the same token (deep-equal fallback).
// FE-3: FIFO-bounded (INLINE_TOKEN_CACHE_MAX) — a long session that swaps many
// distinct GeoJSON payloads can't retain every past object (findings E4).
//
// FIX-3-8: eviction is INSERTION-ORDER FIFO (`keys().next()` = oldest insert),
// NOT LRU — nothing here tracks access recency, and the registry never mutates
// an entry once written (same-reference payloads hit the WeakMap fast path, so
// re-registration never happens for live data). This deliberately mirrors the
// imageRef registry's documented eviction below. (The design doc's "LRU" label
// for this registry is a misnomer — FIFO is the implemented contract.)
//
// Immutable-FC contract: like the geometry-mix memo in mapspec-runtime/adapter.ts
// (geometryProfileCache, WeakMap keyed on the FeatureCollection reference),
// this identity cache assumes sources are REPLACED, never mutated in place —
// a same-reference FC that is mutated would keep its stale token and diff as
// "no change". The adapter honors this by emitting `inlineData: layer.source`
// unchanged across reconciles.
let lastInlineDataBySource: Map<string, { data: object; token: number }> = new Map();

/**
 * FE-3: max entries in the per-source inline-data registry. Evicted sources
 * re-seen with deep-equal data get a fresh token and diff as "update" —
 * harmless: the runtime re-applies the same bytes idempotently (mirrors the
 * imageRef registry's documented eviction behavior).
 */
export const INLINE_TOKEN_CACHE_MAX = 32;

/** Test-only: current size of the per-source inline-data registry. */
export function _inlineTokenRegistrySizeForTests(): number {
  return lastInlineDataBySource.size;
}

/**
 * Return a stable identity token for an inlineData payload. Two payloads that
 * should be considered "the same data" yield the same number.
 */
function inlineIdentityToken(sourceId: string, data: object): number {
  // 1. Same reference → cached token (fast path; the overwhelmingly common
  //    case because the adapter reuses `layer.source` by reference).
  const cached = inlineTokenCache.get(data);
  if (cached !== undefined) return cached;

  // 2. New reference — is it deep-equal to the last payload for this source?
  //    If so, reuse the existing token so the diff still reports "no change".
  const last = lastInlineDataBySource.get(sourceId);
  if (last && isDeepEqualInline(last.data, data)) {
    inlineTokenCache.set(data, last.token);
    return last.token;
  }

  // 3. Genuinely new data → fresh monotonic token, registry FIFO-bounded.
  const token = ++inlineTokenSeq;
  inlineTokenCache.set(data, token);
  lastInlineDataBySource.set(sourceId, { data, token });
  if (lastInlineDataBySource.size > INLINE_TOKEN_CACHE_MAX) {
    const oldest = lastInlineDataBySource.keys().next().value;
    if (oldest !== undefined) lastInlineDataBySource.delete(oldest);
  }
  return token;
}

/**
 * Shallow-ish deep equality for the identity fallback. Mirrors the subset of
 * `reconciler.isDeepEqual` behavior that matters for FeatureCollections
 * (recurses, handles arrays + plain objects). Kept local so this module stays
 * dependency-free; the fallback only runs on a new reference per changed
 * source, so its cost is negligible versus the structured-clone savings.
 */
function isDeepEqualInline(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (!isDeepEqualInline(a[i], b[i])) return false;
    }
    return true;
  }
  const oa = a as Record<string, unknown>;
  const ob = b as Record<string, unknown>;
  const ka = Object.keys(oa);
  const kb = Object.keys(ob);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.prototype.hasOwnProperty.call(ob, k)) return false;
    if (!isDeepEqualInline(oa[k], ob[k])) return false;
  }
  return true;
}

// ── FE-12: inline raster image stripping ───────────────────────────────────
//
// Raster heatmap sources carry the full base64 image as `imageRef`
// (data:image/png;base64,<multi-MB body>). Unlike geojson inlineData (FE-02)
// these used to pass through stripInlineForTransfer, so postMessage
// structured-cloned the whole image on EVERY reconcile — as `prev` AND `next`
// — even when the image never changed. We strip `imageRef` to a stable
// identity token exactly like geojson, and the worker's deep-equality check
// then compares a tiny token instead of megabytes of base64.
//
// Token identity contract (mirrors inlineIdentityToken's observable behavior):
//  - same imageRef CONTENT (same string value, whether the same reference or
//    a freshly-built string with equal bytes) → same token. The token is keyed
//    on the string VALUE, so content-addressed lookup replaces the WeakMap +
//    deep-equal fallback FE-02 needs for objects — a JS Map hashes its string
//    keys internally, so there is zero collision risk and invalidation on
//    content change is automatic (new bytes → new token → diff reports
//    "update").
//  - the registry is FIFO-bounded (IMAGE_TOKEN_CACHE_MAX) so a long session
//    that regenerates many heatmaps can't retain every past image. An evicted
//    image that reappears gets a fresh token and diffs as "update" — harmless:
//    the runtime re-applies the same bytes idempotently (renderer F28).

const IMAGE_TOKEN_CACHE_MAX = 16;
let imageTokenSeq = 0;
// Content → token. Insertion-ordered so the oldest entry can be evicted.
const imageTokenByContent: Map<string, number> = new Map();

/**
 * Return a stable identity token for an imageRef payload. Two payloads that
 * should be considered "the same image" yield the same number.
 */
function imageRefIdentityToken(imageRef: string): number {
  const cached = imageTokenByContent.get(imageRef);
  if (cached !== undefined) return cached;
  const token = ++imageTokenSeq;
  imageTokenByContent.set(imageRef, token);
  if (imageTokenByContent.size > IMAGE_TOKEN_CACHE_MAX) {
    const oldest = imageTokenByContent.keys().next().value;
    if (oldest !== undefined) imageTokenByContent.delete(oldest);
  }
  return token;
}

/**
 * Produce a copy of `spec` whose geojson sources carry only an identity token
 * in place of `inlineData` and whose raster sources carry only an identity
 * token in place of the base64 `imageRef` (FE-12). Non-inline sources and all
 * layers pass through untouched. Returns the original spec reference when it
 * has nothing to strip (avoids an allocation in the common no-inline case).
 */
function stripInlineForTransfer(spec: MapSpec | null): MapSpec | null {
  if (!spec) return null;
  const sources = spec.sources;
  if (!sources) return spec;
  const ids = Object.keys(sources);
  let anyStrippable = false;
  for (const id of ids) {
    const s = sources[id];
    const geo = s as GeoJSONMapSpecSource;
    const ras = s as RasterMapSpecSource;
    if (s && ((geo.type === "geojson" && geo.inlineData) || (ras.type === "raster" && typeof ras.imageRef === "string"))) {
      anyStrippable = true;
      break;
    }
  }
  if (!anyStrippable) return spec;

  // Shallow-clone the spec + sources map; only the inline-bearing source
  // entries are replaced (everything else is shared by reference).
  const strippedSources: Record<string, MapSpecSource> = { ...sources };
  for (const id of ids) {
    const s = sources[id];
    const geo = s as GeoJSONMapSpecSource;
    if (geo.type === "geojson" && geo.inlineData && typeof geo.inlineData === "object") {
      const token = inlineIdentityToken(id, geo.inlineData as object);
      strippedSources[id] = {
        type: "geojson",
        ...(geo.dataPath !== undefined ? { dataPath: geo.dataPath } : {}),
        ...(geo.url !== undefined ? { url: geo.url } : {}),
        inlineData: { __inlineToken: token } as any,
      } as MapSpecSource;
    }
    const ras = s as RasterMapSpecSource;
    if (ras.type === "raster" && typeof ras.imageRef === "string") {
      const token = imageRefIdentityToken(ras.imageRef);
      strippedSources[id] = {
        type: "raster",
        imageRef: { __inlineToken: token } as any,
        bounds: ras.bounds,
        ...(ras.imageSize !== undefined ? { imageSize: ras.imageSize } : {}),
      } as MapSpecSource;
    }
  }
  return { ...spec, sources: strippedSources };
}

/**
 * Map stripped source objects in a worker-produced patch back to the real
 * (unstripped) source definition from `fullSpec`. Without this, the runtime's
 * `applySource` would receive `{ inlineData: { __inlineToken: N } }` and try
 * to add an empty GeoJSON source.
 */
function rehydratePatch(patch: SpecPatch, prevFull: MapSpec | null, nextFull: MapSpec): SpecPatch {
  if (patch.sources.length === 0) return patch;
  const nextSources = nextFull.sources || {};
  const prevSources = prevFull?.sources || {};
  const rehydrated = patch.sources.map((change) => {
    if (change.kind === "remove" || !change.next) return change;
    const original = (change.id in nextSources)
      ? nextSources[change.id]
      : prevSources[change.id];
    if (!original) return change;
    return { ...change, next: original };
  });
  return { ...patch, sources: rehydrated };
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

// FE-01: pending idle-termination handle. Set when activeRequestsCount returns
// to 0; cleared (and re-armed) whenever a new request starts or disposeWorker
// runs. Keeping the worker warm across the gap between two serialized
// reconciles avoids re-booting the module worker on every spec change.
let idleTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * FE-01: schedule the worker to terminate after DIFF_WORKER_IDLE_MS of
 * inactivity. Cancels any previously-armed idle timer first.
 */
function scheduleIdleTermination(worker: Worker): void {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  idleTimer = setTimeout(() => {
    idleTimer = null;
    // Only terminate if this is still the active worker (disposeWorker or a
    // test reset may have already torn it down).
    if (sharedWorker === worker) {
      try { worker.terminate(); } catch { /* already gone */ }
      sharedWorker = null;
    }
  }, DIFF_WORKER_IDLE_MS);
}

/**
 * FE-01: explicitly tear down the shared worker immediately. Called from
 * MapSpecRuntime.dispose() so an unmounting map doesn't leak a warm worker.
 * Cancels the idle timer. Safe to call when no worker exists.
 */
export function disposeWorker(): void {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  if (sharedWorker) {
    try { sharedWorker.terminate(); } catch { /* already gone */ }
    sharedWorker = null;
  }
}

/**
 * Diff two specs, off the main thread when a Worker is available.
 * Resolves with the SpecPatch (or an empty patch on worker error — the caller
 * treats that as "nothing to apply", same as diffSpecs' no-op contract).
 *
 * FE-01: the worker is kept warm (see DIFF_WORKER_IDLE_MS) instead of being
 * terminated the moment the active request count hits 0, so two back-to-back
 * reconciles don't each pay the module-worker boot cost.
 *
 * FE-02/FE-12: inline GeoJSON (`inlineData`) and inline raster images
 * (`imageRef`) are stripped to identity tokens before posting (see
 * stripInlineForTransfer); the returned patch is rehydrated so callers still
 * receive the real source definitions.
 */
export function diffSpecsAsync(prev: MapSpec | null, next: MapSpec): Promise<SpecPatch> {
  if (prev === next) {
    lastDiffFailed = false;
    return Promise.resolve({ sources: [], layers: [] });
  }

  const worker = createWorker();
  if (!worker) {
    // Sync fallback computes a REAL diff — no failure-mode empty patch here.
    // Clear any stale failure flag so a prior worker error can't leak.
    const patch = diffSpecs(prev, next);
    lastDiffFailed = false;
    return Promise.resolve(patch);
  }

  // Starting a new request cancels any pending idle termination (the worker is
  // about to be busy again) and re-arms it only when this request settles.
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  activeRequestsCount++;

  return new Promise<SpecPatch>((resolve) => {
    const id = `diff-${++nextRequestId}`;

    let settled = false;

    /**
     * Settle the request exactly once. Decrement the active request count;
     * arm the idle-termination timer when none remain (FE-01) instead of
     * terminating immediately.
     */
    const finish = (patch: SpecPatch) => {
      if (settled) return;
      settled = true;
      // FIX-3-6: distinguish a genuine no-op patch (worker replied with an
      // empty patch object) from a failure empty patch (we resolved the shared
      // EMPTY_PATCH constant ourselves). Reference equality does that: real
      // worker responses always post a freshly-constructed object.
      lastDiffFailed = patch === EMPTY_PATCH;
      clearTimeout(timer);
      worker.removeEventListener("message", onMessage);
      worker.onerror = null;
      worker.onmessageerror = null;
      workerWithOnexit.onexit = null;
      activeRequestsCount = Math.max(0, activeRequestsCount - 1);
      if (activeRequestsCount === 0 && sharedWorker === worker) {
        scheduleIdleTermination(worker);
      }
      resolve(rehydratePatch(patch, prev, next));
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

    // FE-02: post a stripped copy (inline GeoJSON → identity token). Keep the
    // full `prev`/`next` references for rehydrating the response patch.
    const strippedNext = stripInlineForTransfer(next) as MapSpec;
    const request: DiffRequest = {
      id,
      prev: stripInlineForTransfer(prev),
      next: strippedNext,
    };
    worker.postMessage(request);
  });
}
