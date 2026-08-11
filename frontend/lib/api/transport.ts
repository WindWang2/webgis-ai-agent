/**
 * Unified HTTP transport for the frontend API layer.
 *
 * F-FE-3: replaces the copy-pasted `fetch` + `throw new Error(\`...${status}\`)`
 * blocks that used to live in every endpoint module (A-F-02/09/13/14) with one
 * helper. It provides:
 *
 *   - typed errors: `ApiError` carries the HTTP status, the parsed response
 *     body (FastAPI `detail`), and the request id; `ApiTimeoutError` for aborts
 *     fired by the built-in timeout. Network failures still surface as the
 *     native `TypeError`, and caller-initiated aborts as `AbortError`.
 *   - request-id propagation: every request sends an auto-generated
 *     `X-Request-ID` header (the backend CORS config already exposes
 *     `X-Request-ID` so a proxy/server can log or echo it). The id is carried
 *     on `ApiError`, and a server-echoed `X-Request-ID` response header wins.
 *   - timeout model: an AbortController-based timeout (default
 *     `DEFAULT_TIMEOUT_MS`, per-call override, `0` disables). It covers the
 *     fetch phase (until response headers); a long SSE turn is governed by the
 *     caller's own AbortSignal, never the timer.
 *   - retry safety: retries are opt-in (`retries`), and the helper REFUSES to
 *     re-send non-idempotent methods (POST/PATCH/CONNECT) no matter what —
 *     chat execute / tool execute are single-shot by construction.
 */

import { API_BASE } from './config';

export const DEFAULT_TIMEOUT_MS = 30_000;

/** Methods that may safely be re-sent after a transient failure. */
const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE', 'TRACE']);

export function isIdempotentMethod(method: string): boolean {
  return IDEMPOTENT_METHODS.has(method.toUpperCase());
}

export interface ApiFetchOptions {
  /** HTTP method; defaults to GET. */
  method?: string;
  /** Extra headers merged under the transport-managed ones. */
  headers?: Record<string, string>;
  /** JSON-serializable body; sets Content-Type: application/json. */
  body?: unknown;
  /**
   * Pre-built body (FormData / Blob / URLSearchParams / ArrayBuffer) — bypasses
   * the JSON.stringify path. Use this for file uploads, FormData posts, and any
   * payload where the platform must set Content-Type itself (e.g. multipart
   * boundary). Mutually exclusive with `body`; do not also set a JSON
   * Content-Type header in `headers` when using this.
   */
  rawBody?: BodyInit;
  /** External abort signal (session switch, unmount, user stop). */
  signal?: AbortSignal;
  /**
   * Timeout in ms before the request is aborted (0 disables). Applies to the
   * fetch (until headers); defaults to DEFAULT_TIMEOUT_MS.
   */
  timeoutMs?: number;
  /** SEC-08: anonymous-session ownership token → X-Session-Token header. */
  ownerToken?: string | null;
  /** Override the auto-generated X-Request-ID (e.g. one id per turn). */
  requestId?: string;
  /** Prefix for the ApiError message, e.g. "Chat API error". */
  label?: string;
  /** False → do not read the response body (204 deletes); resolves undefined. */
  parseJson?: boolean;
  /**
   * Request credentials mode. Defaults to "same-origin" (browser default);
   * cross-origin cookie-bearing endpoints (data-fabric, layer types) pass
   * "include" to ship the session cookie.
   */
  credentials?: RequestCredentials;
  /**
   * Opt-in retries for transient failures. IGNORED for non-idempotent methods:
   * POST/PATCH/CONNECT are never re-sent (F-FE-3 hard constraint).
   */
  retries?: number;
  /** Delay between attempts (default 200ms). */
  retryDelayMs?: number;
}

/** HTTP error response carrying status + parsed body + request id. */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  /** Parsed response body (FastAPI `detail`), raw text, or undefined. */
  readonly body: unknown;
  /** X-Request-ID sent with this request (server-echoed value if present). */
  readonly requestId?: string;
  /** True when a retry could plausibly succeed (transient 5xx server error). */
  readonly retryable: boolean;

  constructor(
    status: number,
    statusText: string,
    body?: unknown,
    requestId?: string,
    label?: string,
  ) {
    super(
      label
        ? `${label}: ${status}`
        : `Request failed: ${status}${statusText ? ` ${statusText}` : ''}`,
    );
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
    this.requestId = requestId;
    this.retryable = status >= 500;
  }
}

/** The built-in timeout fired before the server responded (headers phase). */
export class ApiTimeoutError extends Error {
  readonly timeoutMs: number;
  readonly requestId?: string;

  constructor(timeoutMs: number, requestId?: string) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = 'ApiTimeoutError';
    this.timeoutMs = timeoutMs;
    this.requestId = requestId;
  }
}

/** Type guard for ApiError. */
export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** Type guard for ApiTimeoutError. */
export function isApiTimeoutError(err: unknown): err is ApiTimeoutError {
  return err instanceof ApiTimeoutError;
}

/** Fresh unique request id (crypto.randomUUID with a non-secure fallback). */
function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function buildRequest(
  path: string,
  options: ApiFetchOptions,
  method: string,
  requestId: string,
): { url: string; init: RequestInit } {
  const headers: Record<string, string> = {
    ...(options.headers ?? {}),
    'X-Request-ID': requestId,
  };
  if (options.ownerToken) headers['X-Session-Token'] = options.ownerToken;
  const init: RequestInit = { method, headers };
  if (options.signal) init.signal = options.signal;
  if (options.credentials) init.credentials = options.credentials;
  if (options.rawBody !== undefined) {
    // Caller-supplied body: do not set Content-Type — the platform chooses the
    // correct value (multipart boundary, etc) when a BodyInit is passed.
    init.body = options.rawBody;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  return { url: `${API_BASE}${path}`, init };
}

/**
 * fetch() with an AbortController-based timeout. When the timer fires the call
 * rejects with ApiTimeoutError; when the CALLER's signal aborts, the original
 * AbortError is rethrown so consumers keep distinguishing user aborts. The
 * timer stops once headers arrive (the body is not timed — SSE turns can be
 * arbitrarily long). With no timeout configured the external signal is passed
 * straight through (native AbortError, zero wrapping).
 */
async function timedFetch(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  requestId: string,
): Promise<Response> {
  const external = init.signal;
  if (!timeoutMs) return fetch(url, init);

  const controller = new AbortController();
  let timedOut = false;
  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener('abort', onExternalAbort, { once: true });
  }
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const cleanup = () => {
    clearTimeout(timer);
    if (external) external.removeEventListener('abort', onExternalAbort);
  };

  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    cleanup();
    return response;
  } catch (err) {
    cleanup();
    if (external?.aborted) throw err; // caller abort wins over the timer
    if (timedOut) throw new ApiTimeoutError(timeoutMs, requestId);
    throw err;
  }
}

async function toApiError(
  response: Response,
  label: string | undefined,
  requestId: string,
): Promise<ApiError> {
  let body: unknown = undefined;
  try {
    const raw = await response.text();
    if (raw) {
      try {
        body = JSON.parse(raw);
      } catch {
        body = raw; // non-JSON error body (proxy HTML page, plain text)
      }
    }
  } catch {
    body = undefined; // body unreadable — keep a status-only error
  }
  const echoed = response.headers?.get?.('x-request-id');
  return new ApiError(response.status, response.statusText, body, echoed ?? requestId, label);
}

/** Parse a success body, treating 204 / empty bodies as undefined (A-F-14). */
async function parseBody<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  // Prefer .text() so the same parse path handles both JSON and non-JSON
  // success bodies (the spec'd Response always has it). Some test doubles
  // (e.g. map-exporter's mockFetchSuccess) only stub .json()/.blob() — fall
  // back to .json() in that case so test fidelity matches production.
  if (typeof response.text === 'function') {
    const text = await response.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  }
  if (typeof response.json === 'function') {
    return (await response.json()) as T;
  }
  return undefined as T;
}

function isRetryable(err: unknown, method: string): boolean {
  if (!isIdempotentMethod(method)) return false;
  if (err instanceof ApiTimeoutError) return true;
  if (err instanceof ApiError) return err.retryable;
  return err instanceof TypeError; // network-level failure
}

/**
 * Perform a JSON request and resolve with the parsed response body.
 *
 * The retry loop is the ONLY place requests are re-sent, and it is closed to
 * non-idempotent methods by construction — see isRetryable.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const attempts = isIdempotentMethod(method) ? Math.max(0, options.retries ?? 0) + 1 : 1;
  const requestId = options.requestId ?? newRequestId();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const retryDelayMs = options.retryDelayMs ?? 200;
  const { url, init } = buildRequest(path, options, method, requestId);

  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) await sleep(retryDelayMs);
    try {
      const response = await timedFetch(url, init, timeoutMs, requestId);
      if (!response.ok) throw await toApiError(response, options.label, requestId);
      if (options.parseJson === false) return undefined as T;
      return await parseBody<T>(response);
    } catch (err) {
      lastError = err;
      if (attempt === attempts - 1 || !isRetryable(err, method)) throw err;
    }
  }
  throw lastError;
}

/**
 * Open a streaming (SSE) request: resolves with the Response once headers
 * arrive (connect timeout applied), throwing ApiError on a non-ok status. The
 * caller owns the body lifecycle — stream it and drive it with its own
 * AbortSignal. Never retried: re-opening a stream the caller chose to abort is
 * the caller's decision, and streaming endpoints are POSTs anyway.
 */
export async function openStream(
  path: string,
  options: ApiFetchOptions = {},
): Promise<Response> {
  const method = (options.method ?? 'GET').toUpperCase();
  const requestId = options.requestId ?? newRequestId();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const { url, init } = buildRequest(path, options, method, requestId);
  const response = await timedFetch(url, init, timeoutMs, requestId);
  if (!response.ok) throw await toApiError(response, options.label, requestId);
  return response;
}
