/**
 * Batched map-action ACK sender (V3 harness–map closed loop, design §6).
 *
 * The MapActionContext reports every terminal action (succeeded / failed /
 * cancelled / superseded) through a `registerAckSink(fn)` DI seam. useMapBridge
 * registers the sink created here, which batches acks and POSTs them to
 * `POST /api/v1/chat/sessions/{sid}/map-action-ack`.
 *
 * Delivery guarantees are deliberately weak — this is a metrics channel, not
 * map state:
 * - fire-and-forget: a failed POST drops the batch (devOnly log), the map must
 *   never break because an ACK failed;
 * - 500ms debounce: a burst of terminal events coalesces into one POST;
 * - flush() on session switch / unmount so the tail of a session is not lost.
 *
 * Auth + transport mirror the map-state push in useMapBridge.ts exactly
 * (X-Session-Token header, API_BASE, plain fetch with .catch).
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

export interface MapActionAckSenderOptions {
  /** Current session at enqueue time (acks without correlation.session_id). */
  getSessionId: () => string | undefined;
  /** Owner token, same source as the map-state push. */
  getToken: (sessionId?: string) => string | null;
  /** Debounce window before a batch is POSTed (default 500ms). */
  debounceMs?: number;
  /** Backend accepts at most 50 acks per POST (design §4) — larger flushes are chunked. */
  maxAcksPerPost?: number;
  /** Test seam; defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /** Receives a typed backend follow-up such as an AUTO_SAFE repair action. */
  onResponse?: (sessionId: string, body: unknown) => void;
}

export interface MapActionAckSender {
  /** Pass to MapActionContext.registerAckSink. */
  sink: MapActionAckSink;
  /** POST everything queued right now (session switch / unmount). */
  flush: () => void;
  /** Drop the queue and cancel the pending debounce timer. */
  dispose: () => void;
}

const DEFAULT_DEBOUNCE_MS = 500;
const MAX_ACKS_PER_POST = 50;

export function createMapActionAckSender(options: MapActionAckSenderOptions): MapActionAckSender {
  const {
    getSessionId,
    getToken,
    debounceMs = DEFAULT_DEBOUNCE_MS,
    maxAcksPerPost = MAX_ACKS_PER_POST,
    fetchImpl,
    onResponse,
  } = options;

  // Session id is captured per ack at enqueue time: acks produced just before a
  // session switch still POST to the session that issued the action.
  let queue: Array<{
    sessionId: string | undefined;
    token: string | null;
    ack: MapActionAck;
  }> = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  const postBatch = (sessionId: string, token: string | null, acks: MapActionAck[]): void => {
    const doFetch = fetchImpl ?? fetch;
    doFetch(
      `${API_BASE}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/map-action-ack`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'X-Session-Token': token } : {}),
        },
        body: JSON.stringify({ acks }),
      },
    ).then(async (response) => {
      if (!response.ok || typeof response.json !== 'function') return;
      try {
        onResponse?.(sessionId, await response.json());
      } catch {
        // ACK storage already succeeded; an absent/malformed optional response
        // body must not affect map interaction.
      }
    }).catch((e) => {
      // Fire-and-forget: a failed ACK POST must never degrade the map or stream.
      devOnly.warn('[map-action-acks] ACK POST failed, dropping batch:', e);
    });
  };

  const flush = (): void => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (queue.length === 0) return;
    const pending = queue;
    queue = [];
    // Group by session, preserving insertion order. A plain array of pairs is
    // used instead of Map iteration: the repo tsconfig has no ES2015+ target,
    // so `for...of` over a Map would fail tsc (TS2802).
    const groups: Array<{
      sessionId: string | undefined;
      token: string | null;
      acks: MapActionAck[];
    }> = [];
    for (const item of pending) {
      const group = groups.find(
        (g) => g.sessionId === item.sessionId && g.token === item.token,
      );
      if (group) group.acks.push(item.ack);
      else groups.push({ sessionId: item.sessionId, token: item.token, acks: [item.ack] });
    }
    for (const { sessionId, token, acks } of groups) {
      if (!sessionId) {
        devOnly.warn('[map-action-acks] dropping acks with no session:', acks.length);
        continue;
      }
      for (let i = 0; i < acks.length; i += maxAcksPerPost) {
        postBatch(sessionId, token, acks.slice(i, i + maxAcksPerPost));
      }
    }
  };

  const sink: MapActionAckSink = (ack) => {
    const sessionId = ack.correlation?.session_id ?? getSessionId();
    queue.push({
      sessionId,
      token: getToken(sessionId),
      ack,
    });
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, debounceMs);
  };

  const dispose = (): void => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    queue = [];
  };

  return { sink, flush, dispose };
}
