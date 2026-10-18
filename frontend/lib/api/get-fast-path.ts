/**
 * GET Fast Path — request coalescing, in-flight dedup, and short-lived cache
 * for idempotent REST calls.
 *
 * F-FE-FGP: prior to this, every component that needed the same project/dataset
 * list on a page would fire its own fetch, and tab switch / quick re-mounts
 * would all trigger parallel identical requests. The pattern below is the
 * minimal local abstraction the audit asked for — no SWR/React-Query:
 *
 *   1. **In-flight dedup**: if the same (method, path, params) request is
 *      already in flight, all callers share the same Promise. Stops
 *      `Promise.all([fetchProjects(), fetchProjects()])` from doubling RTTs.
 *   2. **Short-lived cache**: GET responses are cached for `DEFAULT_TTL_MS`
 *      (per-option override), keyed by `${method}|${path}|${paramsHash}`.
 *      Cache is bounded to `MAX_ENTRIES` entries (LRU-style eviction on insert).
 *   3. **Generation/sequence guard**: every successful fetch bumps a
 *      per-key monotonic generation; if a slow response arrives after a
 *      mutation has fired a refresh, the new generation supersedes the
 *      cached value and the stale call resolves as `stale=true` (caller can
 *      discard or display an inline indicator). The cache itself is replaced
 *      with the freshest value; we never serve old data when newer exists.
 *   4. **Mutation invalidation**: callers invalidate by path prefix after
 *      create/update/delete; bounded eviction prevents unbounded growth.
 *   5. **AbortSignal**: each caller's abort rejects only THAT caller. The
 *      shared fetch is cancelled once every caller has aborted (F-12).
 *
 * The cache is in-memory and process-local. It is intentionally NOT persisted
 * to localStorage — server pagination/scoping must remain authoritative.
 */

import { apiFetch } from './transport';
import { getAuthUser } from '../auth/tokenStore';

const DEFAULT_TTL_MS = 5_000; // 5s — short enough that stale-after-mutation
                              // is rare, long enough to dedupe parallel
                              // mounts and tab switches.
const MAX_ENTRIES = 256;

export interface GetFastPathOptions {
  /** Force refresh (skip cache lookup, but still dedupe in-flight). */
  forceRefresh?: boolean;
  /** TTL override in ms (0 = no caching, just dedupe). */
  ttlMs?: number;
  /** Request body/params as object → query string. */
  params?: Record<string, string | number | boolean | undefined | null>;
  /** Abort signal shared by all callers. */
  signal?: AbortSignal;
  /** Request id propagated through to the transport. */
  requestId?: string;
  /** Per-call label for ApiError messages. */
  label?: string;
  /** Per-call timeout. */
  timeoutMs?: number;
  /** Request credentials mode (e.g. "include" for cookie-bearing endpoints). */
  credentials?: RequestCredentials;
  /**
   * SEC-08 (#1109): anonymous-session ownership token → X-Session-Token
   * header (forwarded to the shared transport). F-12: a fingerprint of it
   * (plus the signed-in user id) scopes the cache key, so a different
   * identity never reads another identity's cached response.
   */
  ownerToken?: string | null;
}

export interface GetFastPathResult<T> {
  data: T;
  /** True when served from the short-lived cache (not a fresh network roundtrip). */
  cached: boolean;
  /** True when the response arrived after a newer generation already won. */
  stale: boolean;
  /** Monotonic generation counter for this cache key. */
  generation: number;
}

interface CacheEntry<T> {
  value: T;
  generation: number;
  insertedAt: number;
  ttlMs: number;
  promise?: Promise<unknown>; // currently in-flight (for dedup)
  abortController?: AbortController;
  /**
   * F-12: callers still interested in the in-flight fetch. Callers without a
   * signal count as permanently live. The shared fetch is aborted only when
   * EVERY caller has aborted — one caller's unmount never rejects the others.
   */
  liveCallers?: number;
}

const cache = new Map<string, CacheEntry<unknown>>();

/**
 * F-12: identity scope of a cache entry (signed-in user + anonymous owner
 * token). A logout/login or session-token change within the TTL must not
 * serve the previous identity's response. Encoded so it never contains the
 * `|` / `?` separators invalidateCache parses.
 */
function identityScope(ownerToken?: string | null): string {
  let userId = '';
  try {
    userId = getAuthUser()?.id ?? '';
  } catch {
    userId = '';
  }
  let owner = '';
  if (ownerToken) {
    // Short non-cryptographic fingerprint: the raw capability never sits in a key.
    let h = 0;
    for (let i = 0; i < ownerToken.length; i += 1) h = (Math.imul(31, h) + ownerToken.charCodeAt(i)) | 0;
    owner = (h >>> 0).toString(36);
  }
  if (!userId && !owner) return '';
  return `${encodeURIComponent(userId)};${owner}::`;
}

/** Abort-aware wait: reject with AbortError when THIS caller's signal fires. */
function awaitWithSignal<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  const abortError = () => new DOMException('The operation was aborted.', 'AbortError');
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(
      (v) => { signal.removeEventListener('abort', onAbort); resolve(v); },
      (e) => { signal.removeEventListener('abort', onAbort); reject(e); },
    );
  });
}

/** Register a caller on a shared in-flight entry (F-12 ref-counted abort). */
function joinInFlight(entry: CacheEntry<unknown>, signal?: AbortSignal): void {
  entry.liveCallers = (entry.liveCallers ?? 0) + 1;
  if (!signal) return;
  const leave = () => {
    entry.liveCallers = (entry.liveCallers ?? 1) - 1;
    if (entry.liveCallers <= 0 && entry.abortController) {
      try { entry.abortController.abort(); } catch { /* ignore */ }
    }
  };
  if (signal.aborted) leave();
  else signal.addEventListener('abort', leave, { once: true });
}

/** Generate a stable cache key from method, path, and params. */
function cacheKey(method: string, path: string, params?: Record<string, unknown>): string {
  if (!params) return `${method}|${path}`;
  // Stable serialization: sort keys so {a:1,b:2} and {b:2,a:1} hash the same.
  const keys = Object.keys(params).sort();
  const parts: string[] = [];
  for (const k of keys) {
    const v = params[k];
    if (v === undefined || v === null) continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  if (!parts.length) return `${method}|${path}`;
  return `${method}|${path}?${parts.join('&')}`;
}

/** Build the actual request path with query string from params. */
function buildPath(path: string, params?: Record<string, unknown>): string {
  if (!params) return path;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  if (!parts.length) return path;
  const sep = path.includes('?') ? '&' : '?';
  return `${path}${sep}${parts.join('&')}`;
}

/** Enforce MAX_ENTRIES by removing the oldest entry (LRU). */
function enforceBound() {
  if (cache.size <= MAX_ENTRIES) return;
  // First-key is oldest in insertion order (Map iteration).
  const firstKey = cache.keys().next().value as string | undefined;
  if (firstKey !== undefined) {
    // F-12: evict without aborting — in-flight waiters still get their data
    // (the generation check skips the write-back for an evicted entry).
    cache.delete(firstKey);
  }
}

/**
 * Invalidate all cache entries whose key starts with the given path prefix.
 * Call this after mutations (create/update/delete) to prevent stale reads.
 */
export function invalidateCache(pathPrefix: string): number {
  let removed = 0;
  // Collect first to avoid mutating during iteration.
  const keysToRemove: string[] = [];
  cache.forEach((_value, key) => {
    const unscoped = key.includes('::') ? key.slice(key.indexOf('::') + 2) : key;
    const path = unscoped.split('?')[0].split('|').slice(1).join('|');
    if (path === pathPrefix || path.startsWith(`${pathPrefix}/`) || path.startsWith(`${pathPrefix}?`)) {
      keysToRemove.push(key);
    }
  });
  for (const key of keysToRemove) {
    // F-12: mark stale by eviction, never abort — other components awaiting
    // the in-flight GET still resolve; the next call refetches fresh.
    cache.delete(key);
    removed += 1;
  }
  return removed;
}

/** Wipe the entire cache (e.g. on session switch). */
export function clearCache(): void {
  // F-12: no abort — callers own cancellation via their signals.
  cache.clear();
}

/** Internal: snapshot for tests / debugging. */
export function _cacheSize(): number {
  return cache.size;
}

/**
 * GET with in-flight dedup, short-lived cache, and generation guard.
 *
 * Use this for any idempotent GET that benefits from dedupe (project list,
 * dataset list, workflow list, data-fabric sources/catalog, session list).
 * For mutations and SSE, use apiFetch/openStream directly.
 */
export async function fastGet<T = unknown>(
  path: string,
  options: GetFastPathOptions = {},
): Promise<GetFastPathResult<T>> {
  const key = identityScope(options.ownerToken) + cacheKey('GET', path, options.params);
  const now = Date.now();
  const ttl = options.ttlMs ?? DEFAULT_TTL_MS;
  const existing = cache.get(key);

  // Cache hit (no force refresh, not expired, no in-flight stale).
  if (
    existing &&
    !existing.promise &&
    !options.forceRefresh &&
    ttl > 0 &&
    now - existing.insertedAt < ttl
  ) {
    return {
      data: existing.value as T,
      cached: true,
      stale: false,
      generation: existing.generation,
    };
  }

  // In-flight dedup: if a previous caller already fired, share their Promise.
  // Exception (#1555 journey root cause): an in-flight entry whose controller
  // is ALREADY aborted is a dead request — React StrictMode's double mount
  // aborts mount #1 synchronously, so mount #2 must not join its doomed
  // promise (it would inherit ERR_ABORTED and the caller's data never loads).
  // Fall through to a fresh fetch instead.
  if (
    existing?.promise &&
    !options.forceRefresh &&
    !existing.abortController?.signal.aborted
  ) {
    joinInFlight(existing, options.signal);
    const data = (await awaitWithSignal(existing.promise, options.signal)) as T;
    const after = cache.get(key);
    return {
      data,
      cached: true,
      stale: after ? after.generation > existing.generation : false,
      generation: after?.generation ?? existing.generation,
    };
  }

  // Otherwise: set up a fresh fetch shared by all dedupe'd callers.
  const controller = new AbortController();
  const generation = (existing?.generation ?? 0) + 1;
  const entry: CacheEntry<T> = {
    value: (existing?.value ?? undefined) as T,
    generation,
    insertedAt: now,
    ttlMs: ttl,
    abortController: controller,
  };
  cache.set(key, entry as CacheEntry<unknown>);

  const promise = apiFetch<T>(buildPath(path, options.params), {
    method: 'GET',
    signal: controller.signal,
    requestId: options.requestId,
    label: options.label,
    timeoutMs: options.timeoutMs,
    credentials: options.credentials,
    ownerToken: options.ownerToken,
  }).then((data) => {
    const cur = cache.get(key);
    if (cur && cur.generation === generation) {
      cur.value = data;
      cur.insertedAt = Date.now();
    }
    return data;
  }).catch((err: unknown) => {
    // 失败的请求不配占缓存位：拒绝（abort/超时/网络错）后删除本条目，
    // 让下一个 caller fresh fetch，而不是在 TTL 内反复读到毒化的空值
    // （#1555 journey 根因的另一半）。
    const cur = cache.get(key);
    if (cur && cur.generation === generation) {
      cache.delete(key);
    }
    throw err;
  }).finally(() => {
    const cur = cache.get(key);
    if (cur && cur.generation === generation) {
      cur.promise = undefined;
      cur.abortController = undefined;
    }
  });

  entry.promise = promise as Promise<unknown>;
  // F-12: the creator is just the first caller of the shared fetch.
  joinInFlight(entry as CacheEntry<unknown>, options.signal);
  enforceBound();

  const data = await awaitWithSignal(promise, options.signal);
  const after = cache.get(key);
  return {
    data,
    cached: false,
    stale: after ? after.generation > generation : false,
    generation,
  };
}
