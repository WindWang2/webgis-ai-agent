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
 *   5. **AbortSignal**: caller abort cancels the in-flight fetch. Multiple
 *      callers sharing a Promise all get the same abort propagation.
 *
 * The cache is in-memory and process-local. It is intentionally NOT persisted
 * to localStorage — server pagination/scoping must remain authoritative.
 */

import { apiFetch } from './transport';

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
   * header (forwarded to the shared transport). Deliberately NOT part of the
   * cache key — the key already includes params.session_id, and the token is
   * constant within a session.
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
}

const cache = new Map<string, CacheEntry<unknown>>();

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
    const entry = cache.get(firstKey);
    if (entry?.abortController) {
      try { entry.abortController.abort(); } catch { /* ignore */ }
    }
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
    const path = key.split('?')[0].split('|').slice(1).join('|');
    if (path === pathPrefix || path.startsWith(`${pathPrefix}/`) || path.startsWith(`${pathPrefix}?`)) {
      keysToRemove.push(key);
    }
  });
  for (const key of keysToRemove) {
    const entry = cache.get(key);
    if (entry?.abortController) {
      try { entry.abortController.abort(); } catch { /* ignore */ }
    }
    cache.delete(key);
    removed += 1;
  }
  return removed;
}

/** Wipe the entire cache (e.g. on session switch). */
export function clearCache(): void {
  cache.forEach((entry) => {
    if (entry?.abortController) {
      try { entry.abortController.abort(); } catch { /* ignore */ }
    }
  });
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
  const key = cacheKey('GET', path, options.params);
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
  if (existing?.promise && !options.forceRefresh) {
    const data = (await existing.promise) as T;
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
  // Propagate caller signal to the shared controller.
  if (options.signal) {
    if (options.signal.aborted) controller.abort();
    else options.signal.addEventListener('abort', () => controller.abort(), { once: true });
  }
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
  }).finally(() => {
    const cur = cache.get(key);
    if (cur && cur.generation === generation) {
      cur.promise = undefined;
      cur.abortController = undefined;
    }
  });

  entry.promise = promise as Promise<unknown>;
  enforceBound();

  const data = await promise;
  const after = cache.get(key);
  return {
    data,
    cached: false,
    stale: after ? after.generation > generation : false,
    generation,
  };
}
