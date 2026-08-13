/**
 * Batched map-action ACK sender (V3 harness–map closed loop, design §6).
 *
 * After #356 the ACK endpoint may return `repair_action`, which useMapBridge
 * dispatches onto the map. Delivery is therefore control-plane, not a pure
 * metrics channel: bounded, idempotent, transient-resilient — not exactly-once.
 *
 * - session id + owner token are captured per ACK at enqueue;
 * - retries reuse the same action_id (backend first-terminal-wins);
 * - only classified transients are retried (network, timeout, 408, 429, 5xx,
 *   HTTP 200 with `dropped > 0`);
 * - auth / validation 4xx / 410 are permanent drops;
 * - queue and attempt counts are bounded; POSTs stay chunked at max 50;
 * - retry never blocks map rendering or chat streaming;
 * - dispose() cancels debounce, retry, and request-timeout timers.
 *
 * sendBeacon / fetch keepalive are not used: sendBeacon cannot set
 * X-Session-Token, and keepalive's 64KB cap can silently fail a legal batch.
 */

import { API_BASE } from '@/lib/api/config';
import { devOnly } from '@/lib/utils/logger';
import type { MapActionCorrelation, MapActionTerminalStatus } from '@/lib/types';

/** One terminal map-action report. Mirrors the backend MapActionAck schema (design §4). */
export interface MapActionAck {
  action_id: string;
  command: string;
  status: MapActionTerminalStatus;
  error?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number | null;
  correlation?: MapActionCorrelation | null;
  requested?: Record<string, unknown> | null;
  actual?: Record<string, unknown> | null;
}

/** registerAckSink-compatible callback shape. */
export type MapActionAckSink = (ack: MapActionAck) => void;

export type AckDeliveryKind = 'success' | 'transient' | 'permanent';
export type AckDeliveryReason =
  | 'ok'
  | 'network'
  | 'timeout'
  | 'http_408'
  | 'http_429'
  | 'http_5xx'
  | 'backend_dropped'
  | 'http_401'
  | 'http_403'
  | 'http_404'
  | 'http_410'
  | 'http_4xx'
  | 'validation'
  | 'invalid_body';

export interface MapActionAckSenderOptions {
  /** Current session at enqueue time (acks without correlation.session_id). */
  getSessionId: () => string | undefined;
  /** Owner token, same source as the map-state push. */
  getToken: (sessionId?: string) => string | null;
  /** Debounce window before a batch is POSTed (default 500ms). */
  debounceMs?: number;
  /** Backend accepts at most 50 acks per POST (design §4) — larger flushes are chunked. */
  maxAcksPerPost?: number;
  /** Max POST attempts per ACK (initial + retries). */
  maxAttempts?: number;
  /** Max in-memory queued ACKs; overflow drops oldest. */
  maxQueue?: number;
  /** Base delay for exponential backoff (ms). */
  retryBaseMs?: number;
  /** Cap for exponential backoff (ms). */
  retryMaxMs?: number;
  /** Per-request abort timeout (ms). 0 disables. */
  requestTimeoutMs?: number;
  /** Test seam for jitter in [0, 1). */
  random?: () => number;
  /** Test seam for "now" (ms). */
  now?: () => number;
  /** Test seam; defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /**
   * Receives a typed backend follow-up such as an AUTO_SAFE repair action.
   * Return `false` when the follow-up was not applied (stale session) so the
   * sender does not burn the repair id.
   */
  onResponse?: (sessionId: string, body: unknown) => boolean | void;
}

export interface MapActionAckSender {
  /** Pass to MapActionContext.registerAckSink. */
  sink: MapActionAckSink;
  /** POST everything queued right now (session switch / unmount). */
  flush: () => void;
  /** Drop the queue and cancel pending debounce / retry / request timers. */
  dispose: () => void;
}

const DEFAULT_DEBOUNCE_MS = 500;
const MAX_ACKS_PER_POST = 50;
export const DEFAULT_MAX_ATTEMPTS = 3;
export const DEFAULT_MAX_QUEUE = 200;
export const DEFAULT_RETRY_BASE_MS = 250;
export const DEFAULT_RETRY_MAX_MS = 4000;
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const DELIVERED_REPAIR_CAP = 256;

interface QueuedAck {
  sessionId: string | undefined;
  token: string | null;
  ack: MapActionAck;
  attempts: number;
  readyAt: number;
}

function isTimeoutError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = (error as { name?: string }).name;
  return name === 'AbortError' || name === 'TimeoutError' || name === 'ApiTimeoutError';
}

function droppedCount(body: unknown): number {
  if (!body || typeof body !== 'object') return 0;
  const value = (body as { dropped?: unknown }).dropped;
  return typeof value === 'number' && value > 0 ? value : 0;
}

function repairActionId(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined;
  const repair = (body as { repair_action?: { action_id?: unknown } }).repair_action;
  return typeof repair?.action_id === 'string' && repair.action_id
    ? repair.action_id
    : undefined;
}

/** Pure classifier: transient vs permanent vs success. No I/O. */
export function classifyAckDelivery(input: {
  error?: unknown;
  status?: number;
  dropped?: number;
}): { kind: AckDeliveryKind; reason: AckDeliveryReason } {
  if (input.error !== undefined) {
    if (isTimeoutError(input.error)) return { kind: 'transient', reason: 'timeout' };
    return { kind: 'transient', reason: 'network' };
  }
  const status = input.status;
  if (status === 408) return { kind: 'transient', reason: 'http_408' };
  if (status === 429) return { kind: 'transient', reason: 'http_429' };
  if (typeof status === 'number' && status >= 500) return { kind: 'transient', reason: 'http_5xx' };
  if (status === 401) return { kind: 'permanent', reason: 'http_401' };
  if (status === 403) return { kind: 'permanent', reason: 'http_403' };
  if (status === 404) return { kind: 'permanent', reason: 'http_404' };
  if (status === 410) return { kind: 'permanent', reason: 'http_410' };
  if (status === 422) return { kind: 'permanent', reason: 'validation' };
  if (typeof status === 'number' && status >= 400 && status < 500) {
    return { kind: 'permanent', reason: 'http_4xx' };
  }
  if ((input.dropped ?? 0) > 0) return { kind: 'transient', reason: 'backend_dropped' };
  return { kind: 'success', reason: 'ok' };
}

function retryDelayMs(
  attemptsAfterFail: number,
  retryBaseMs: number,
  retryMaxMs: number,
  random: () => number,
): number {
  const exp = Math.min(retryBaseMs * (2 ** Math.max(0, attemptsAfterFail - 1)), retryMaxMs);
  return Math.floor(exp * (0.5 + random()));
}

function itemKey(sessionId: string | undefined, actionId: string): string {
  return `${sessionId ?? ''}::${actionId}`;
}

export function createMapActionAckSender(options: MapActionAckSenderOptions): MapActionAckSender {
  const {
    getSessionId,
    getToken,
    debounceMs = DEFAULT_DEBOUNCE_MS,
    maxAcksPerPost = MAX_ACKS_PER_POST,
    maxAttempts = DEFAULT_MAX_ATTEMPTS,
    maxQueue = DEFAULT_MAX_QUEUE,
    retryBaseMs = DEFAULT_RETRY_BASE_MS,
    retryMaxMs = DEFAULT_RETRY_MAX_MS,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    random = Math.random,
    now = () => Date.now(),
    fetchImpl,
    onResponse,
  } = options;

  let queue: QueuedAck[] = [];
  const inFlight = new Set<string>();
  const deliveredRepairs = new Set<string>();
  const deliveredRepairOrder: string[] = [];
  const pendingTimers = new Set<ReturnType<typeof setTimeout>>();
  const liveControllers = new Set<AbortController>();
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let generation = 0;

  const trackTimer = (id: ReturnType<typeof setTimeout>): ReturnType<typeof setTimeout> => {
    pendingTimers.add(id);
    return id;
  };

  const untrackTimer = (id: ReturnType<typeof setTimeout> | null): void => {
    if (id === null) return;
    clearTimeout(id);
    pendingTimers.delete(id);
  };

  const logEvent = (event: string, fields: Record<string, unknown>): void => {
    devOnly.warn(`[map-action-acks] ${event}`, fields);
  };

  const hasItem = (sessionId: string | undefined, actionId: string): boolean => {
    const key = itemKey(sessionId, actionId);
    if (inFlight.has(key)) return true;
    for (let i = 0; i < queue.length; i += 1) {
      if (itemKey(queue[i].sessionId, queue[i].ack.action_id) === key) return true;
    }
    return false;
  };

  const enqueueItem = (item: QueuedAck, evictOldest: boolean): boolean => {
    if (queue.length >= maxQueue) {
      if (!evictOldest) {
        logEvent('queue overflow', { dropped: 1, queueSize: queue.length });
        return false;
      }
      queue.shift();
      logEvent('queue overflow', { dropped: 1, queueSize: queue.length });
    }
    queue.push(item);
    return true;
  };

  const rememberRepair = (sessionId: string, repairId: string): void => {
    const key = `${sessionId}:${repairId}`;
    if (deliveredRepairs.has(key)) return;
    deliveredRepairs.add(key);
    deliveredRepairOrder.push(key);
    if (deliveredRepairOrder.length > DELIVERED_REPAIR_CAP) {
      const old = deliveredRepairOrder.shift();
      if (old) deliveredRepairs.delete(old);
    }
  };

  const notify = (sessionId: string, body: unknown, gen: number): void => {
    if (gen !== generation) return;
    const repairId = repairActionId(body);
    if (repairId && deliveredRepairs.has(`${sessionId}:${repairId}`)) return;
    const applied = onResponse?.(sessionId, body);
    if (repairId && applied !== false) rememberRepair(sessionId, repairId);
  };

  const scheduleRetry = (): void => {
    if (retryTimer !== null) return;
    const nowTs = now();
    let nextAt = Number.POSITIVE_INFINITY;
    for (let i = 0; i < queue.length; i += 1) {
      if (queue[i].readyAt > nowTs && queue[i].readyAt < nextAt) {
        nextAt = queue[i].readyAt;
      }
    }
    if (nextAt === Number.POSITIVE_INFINITY) return;
    retryTimer = trackTimer(setTimeout(() => {
      retryTimer = null;
      flushInternal(false);
    }, Math.max(0, nextAt - now())));
  };

  const scheduleDebounce = (): void => {
    untrackTimer(debounceTimer);
    debounceTimer = trackTimer(setTimeout(() => {
      debounceTimer = null;
      flushInternal(false);
    }, debounceMs));
  };

  const settleBatch = (
    sessionId: string,
    items: QueuedAck[],
    classified: { kind: AckDeliveryKind; reason: AckDeliveryReason },
    gen: number,
  ): void => {
    for (let i = 0; i < items.length; i += 1) {
      inFlight.delete(itemKey(items[i].sessionId, items[i].ack.action_id));
    }
    if (gen !== generation) return;
    if (classified.kind === 'success') return;
    if (classified.kind === 'permanent') {
      logEvent('permanent drop', { reason: classified.reason, count: items.length });
      return;
    }
    const retryable: QueuedAck[] = [];
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      item.attempts += 1;
      if (item.attempts >= maxAttempts) {
        logEvent('exhausted retry', { attempts: item.attempts, count: 1 });
      } else {
        item.readyAt = now() + retryDelayMs(item.attempts, retryBaseMs, retryMaxMs, random);
        retryable.push(item);
      }
    }
    if (retryable.length === 0) return;
    logEvent('transient retry', {
      reason: classified.reason,
      attempt: retryable[0].attempts,
      count: retryable.length,
    });
    for (let i = 0; i < retryable.length; i += 1) {
      enqueueItem(retryable[i], false);
    }
    scheduleRetry();
  };

  const postBatch = (sessionId: string, token: string | null, items: QueuedAck[]): void => {
    const gen = generation;
    for (let i = 0; i < items.length; i += 1) {
      inFlight.add(itemKey(items[i].sessionId, items[i].ack.action_id));
    }
    const doFetch = fetchImpl ?? fetch;
    const acks = items.map((item) => item.ack);
    const controller = new AbortController();
    liveControllers.add(controller);
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    if (requestTimeoutMs > 0) {
      timeoutId = trackTimer(setTimeout(() => {
        controller.abort();
      }, requestTimeoutMs));
    }
    const finishRequest = (): void => {
      untrackTimer(timeoutId);
      timeoutId = null;
      liveControllers.delete(controller);
    };

    doFetch(
      `${API_BASE}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/map-action-ack`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'X-Session-Token': token } : {}),
        },
        body: JSON.stringify({ acks }),
        signal: controller.signal,
      },
    ).then(async (response) => {
      let body: unknown;
      if (typeof response.json === 'function') {
        try {
          body = await response.json();
        } catch (error: unknown) {
          finishRequest();
          if (isTimeoutError(error)) {
            settleBatch(sessionId, items, classifyAckDelivery({ error }), gen);
            return;
          }
          settleBatch(sessionId, items, { kind: 'transient', reason: 'invalid_body' }, gen);
          return;
        }
      }
      finishRequest();
      if (response.ok && (body === undefined || typeof body !== 'object' || body === null)) {
        settleBatch(sessionId, items, { kind: 'transient', reason: 'invalid_body' }, gen);
        return;
      }
      const classified = classifyAckDelivery({
        status: response.status,
        dropped: response.ok ? droppedCount(body) : undefined,
      });
      try {
        if (response.ok && body !== undefined) {
          notify(sessionId, body, gen);
        }
      } catch {
        // Repair dispatch is optional closed-loop control. It must not
        // reclassify a settled POST as a transport failure.
      }
      settleBatch(sessionId, items, classified, gen);
    }).catch((error: unknown) => {
      finishRequest();
      settleBatch(sessionId, items, classifyAckDelivery({ error }), gen);
    });
  };

  const flushInternal = (force: boolean): void => {
    untrackTimer(debounceTimer);
    debounceTimer = null;
    untrackTimer(retryTimer);
    retryTimer = null;
    if (queue.length === 0) return;

    const nowTs = now();
    const pending: QueuedAck[] = [];
    const kept: QueuedAck[] = [];
    for (let i = 0; i < queue.length; i += 1) {
      if (force || queue[i].readyAt <= nowTs) pending.push(queue[i]);
      else kept.push(queue[i]);
    }
    queue = kept;
    if (pending.length === 0) {
      if (queue.length > 0) scheduleRetry();
      return;
    }

    const groups: Array<{
      sessionId: string | undefined;
      token: string | null;
      items: QueuedAck[];
    }> = [];
    for (let i = 0; i < pending.length; i += 1) {
      const item = pending[i];
      const group = groups.find(
        (g) => g.sessionId === item.sessionId && g.token === item.token,
      );
      if (group) group.items.push(item);
      else groups.push({ sessionId: item.sessionId, token: item.token, items: [item] });
    }
    for (let g = 0; g < groups.length; g += 1) {
      const { sessionId, token, items } = groups[g];
      if (!sessionId) {
        logEvent('permanent drop', { reason: 'no_session', count: items.length });
        continue;
      }
      for (let i = 0; i < items.length; i += maxAcksPerPost) {
        postBatch(sessionId, token, items.slice(i, i + maxAcksPerPost));
      }
    }
    if (queue.length > 0) scheduleRetry();
  };

  const sink: MapActionAckSink = (ack) => {
    const sessionId = ack.correlation?.session_id ?? getSessionId();
    if (hasItem(sessionId, ack.action_id)) return;
    enqueueItem({
      sessionId,
      token: getToken(sessionId),
      ack,
      attempts: 0,
      readyAt: now(),
    }, true);
    scheduleDebounce();
  };

  const flush = (): void => {
    flushInternal(true);
  };

  const dispose = (): void => {
    generation += 1;
    pendingTimers.forEach((id) => {
      clearTimeout(id);
    });
    pendingTimers.clear();
    liveControllers.forEach((controller) => {
      controller.abort();
    });
    liveControllers.clear();
    inFlight.clear();
    debounceTimer = null;
    retryTimer = null;
    queue = [];
  };

  return { sink, flush, dispose };
}
