'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { streamChat } from '@/lib/api/chat';
import type { SSEEvent } from '@/lib/api/chat';
import { useHudStore } from '@/lib/store/useHudStore';
import type { AiStatus } from '@/lib/store/hud-types';
import type { MapActionPayload } from '@/lib/types';
import { bboxToFlyTo, isValidBbox } from '@/lib/utils/geo';
import { API_BASE } from '@/lib/api/config';
import type { StepResultEvent } from '@/lib/types/agent-events';


import { devOnly } from "@/lib/utils/logger";
import {
  nextViewportSeq,
  resetViewportSeq,
  viewportSeqTracker,
} from "@/lib/utils/viewport-seq";
const MAP_STATE_THROTTLE_MS = 2000;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * DUP-1 auto-reconnect policy. Opt-in and bounded: at most `maxAttempts`
 * reconnects with exponential backoff (`baseDelayMs * 2^attempt`). A reconnect
 * re-POSTs the SAME turn with the last received SSE event id (`Last-Event-ID`),
 * which the backend treats as a read-only resume — it replays the missed
 * events and terminates, and NEVER starts a new turn. Bounded attempts mean a
 * dead server/endpoint cannot spin forever.
 */
export interface SseReconnectOptions {
  /** Max automatic reconnects after an interrupted stream (0 = off). */
  maxAttempts?: number;
  /** Base backoff for the first reconnect; doubles per attempt (ms). */
  baseDelayMs?: number;
}

/**
 * useMapBridge: owns the SSE loop, AbortController, aiStatus, and live map-state push.
 *
 * - sessionId undefined → all SSE/POST calls are silently skipped.
 * - AbortController is internal: auto-aborts on sessionId change and unmount [DX1].
 * - send() aborts any in-flight stream before starting a new one [ENG-P4].
 * - onViewportChange is stable per sessionId (useCallback dep) — safe to bind at 60fps [ENG-D3].
 * - onEvent ordering constraint: define the callback AFTER colors (page.tsx:436), or read
 *   accentColor via useHudStore.getState() to avoid stale closure.
 *
 * @param sessionId - Current chat session ID (undefined = no-op)
 * @param dispatchAction - Map action dispatcher (from useMapAction())
 * @param onEvent - Called for every SSEEvent; page.tsx owns message state + layer-add logic
 */
export function useMapBridge(
  sessionId: string | undefined,
  dispatchAction: (action: MapActionPayload) => void,
  onEvent: (event: SSEEvent) => void,
  sessionTokenRef?: React.MutableRefObject<string | null>,
  reconnect?: SseReconnectOptions,
): {
  aiStatus: AiStatus;
  send: (content: string, mapSnapshot: Record<string, unknown>) => Promise<void>;
  onViewportChange: (center: [number, number], zoom: number, bearing: number, pitch: number) => void;
} {
  if (process.env.NODE_ENV === 'development' && !onEvent) {
    devOnly.error('[useMapBridge] onEvent prop is required');
  }

  const [aiStatus, setAiStatusLocal] = useState<AiStatus>('idle');
  const aiStatusRef = useRef<AiStatus>('idle');
  const abortControllerRef = useRef<AbortController | null>(null);
  const lastMapStatePushRef = useRef<number>(0);
  const prevSessionIdRef = useRef(sessionId);

  const setAiStatus = useCallback((status: AiStatus) => {
    aiStatusRef.current = status;
    setAiStatusLocal(status);
    useHudStore.getState().setAiStatus(status);
  }, []);

  // [DX1] Auto-abort on sessionId change and unmount — AbortController is fully internal.
  // F4: the viewport seq tracker is per-session (server seqs are session-scoped),
  // so reset it whenever the active session changes — including a fresh
  // undefined→assigned assignment.
  useEffect(() => {
    if (prevSessionIdRef.current !== sessionId) {
      if (prevSessionIdRef.current !== undefined && sessionId !== undefined) {
        // Abort only on explicit session switch, not on server assignment for a new session
        abortControllerRef.current?.abort();
      }
      resetViewportSeq();
    }
    prevSessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  const send = useCallback(
    async (content: string, mapSnapshot: Record<string, unknown>): Promise<void> => {
      // [ENG-P4] abort any in-flight stream before starting a new one
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      setAiStatus('thinking');

      // B-P2-9: track whether the stream ended with a proper terminal event.
      // An abrupt close (server died / proxy cut / dropped connection with no
      // final event) used to be reported as 'done' — the user saw partial
      // content as a complete answer. Any of these events marks a real end.
      let gotTerminal = false;

      // DUP-1: last SSE event id received this turn. On a reconnect it is sent
      // back as `Last-Event-ID` so the backend replays only the missed events
      // (resume is a read — it never re-executes the turn).
      let lastEventId: string | undefined;
      const maxAttempts = reconnect?.maxAttempts ?? 0;
      const baseDelayMs = reconnect?.baseDelayMs ?? 500;

      try {
        for (let attempt = 0; ; attempt++) {
          const isLastAttempt = attempt >= maxAttempts;
          let attemptError: unknown = null;

          try {
            // SEC-08：把当前持有的 owner_token 附在请求头，匿名会话后端据此放行。
            // F4: stamp the snapshot with the client's monotonic viewport seq so
            // the backend turn-start write outranks any older in-flight throttled
            // POST that may land after it.
            // DUP-1: attempts after the first carry the last received event id
            // (or "0" if the drop happened before any event — replay from the
            // buffered start) so the backend resumes instead of re-executing.
            for await (const event of streamChat(
              content,
              sessionId,
              { ...mapSnapshot, viewport_seq: nextViewportSeq(viewportSeqTracker) },
              controller.signal,
              undefined,
              sessionTokenRef?.current ?? null,
              attempt > 0 ? (lastEventId ?? "0") : undefined,
            )) {
              if (controller.signal.aborted) break;

              // DUP-1 dedup: a resume replays events after Last-Event-ID, which
              // should never overlap what the client already processed — but if
              // the server replays an id we have seen (stale Last-Event-ID, ring
              // eviction, races), skip it so token appends / layer adds / map
              // commands are NOT applied twice.
              if (event.id) {
                const idNum = Number(event.id);
                if (
                  lastEventId !== undefined &&
                  !Number.isNaN(idNum) &&
                  idNum <= Number(lastEventId)
                ) {
                  continue;
                }
                lastEventId = event.id;
              }

              // Skip unparseable data — streamChat yields raw string on JSON.parse failure
              if (typeof event.data === 'string') {
                devOnly.warn('[useMapBridge] SSE parse failure, skipping:', event.event);
                onEvent(event);
                continue;
              }

              const data = event.data as Record<string, unknown>;

              // aiStatus transitions
              if (event.event === 'thinking') setAiStatus('thinking');
              else if (event.event === 'acting' || event.event === 'step_start') setAiStatus('acting');
              else if (event.event === 'done' || event.event === 'task_complete') {
                gotTerminal = true;
                setAiStatus('done');
              } else if (event.event === 'error' || event.event === 'step_error' || event.event === 'task_error') {
                gotTerminal = true;
                setAiStatus('error');
              }
              // /review P2-7: backend emits task_cancelled when the user aborts a
              // streaming response (commit 2b978de). Without this branch, aiStatus
              // stays stuck in 'thinking' / 'acting' and the composer never frees.
              else if (event.event === 'task_cancelled') {
                gotTerminal = true;
                setAiStatus('idle');
              }

              // step_result: command-wins-over-bbox priority; dispatch before forwarding to onEvent
              if (event.event === 'step_result') {
                const stepData = data as unknown as StepResultEvent;
                const commandFired = !!stepData.result?.command;
                const batchCommands = (stepData.result as any)?.commands as
                  | Array<{ command: string; params?: Record<string, unknown> }>
                  | undefined;
                if (commandFired) {
                  // Heatmap tools put data (image, bbox, geojson, palette…) at the
                  // top level of the result — NOT under a `params` sub-key.
                  // Destructure to separate the command from the rest, then pass
                  // the rest as params so map-action-handler can use them.
                  const { command: _cmd, params: _explicitParams, ...rest } = stepData.result!;
                  const actionParams = (_explicitParams && Object.keys(_explicitParams).length > 0)
                    ? _explicitParams
                    : rest;
                  dispatchAction({
                    command: stepData.result!.command as MapActionPayload['command'],
                    params: actionParams as MapActionPayload['params'],
                  });
                } else if (Array.isArray(batchCommands) && batchCommands.length > 0) {
                  // Batch tool emits a sequence of commands (e.g. export_batch_maps).
                  // The MapActionHandler queue processes one-at-a-time via popAction.
                  for (const cmd of batchCommands) {
                    if (!cmd?.command) continue;
                    dispatchAction({
                      command: cmd.command as MapActionPayload['command'],
                      params: (cmd.params || {}) as MapActionPayload['params'],
                    });
                  }
                } else {
                  const bbox = stepData.result?.bbox ?? stepData.bbox;
                  if (isValidBbox(bbox)) {
                    try {
                      dispatchAction({ command: 'fly_to', params: bboxToFlyTo(bbox) });
                    } catch {
                      // invalid bbox (e.g. degenerate after isValidBbox — defensive)
                    }
                  }
                }
              }

              onEvent(event);
            }
            if (controller.signal.aborted) break;
          } catch (err: unknown) {
            if ((err as Error)?.name === 'AbortError') return;
            attemptError = err;
          }

          if (gotTerminal || controller.signal.aborted) break;

          // DUP-1 reconnect decision. Two reasons to retry, both meaning "the
          // stream was cut before a terminal event":
          //   1. the read threw a network-level error (TypeError / ApiError), or
          //   2. the stream ended cleanly but while still thinking/acting (an
          //      abrupt close with no done/error — B-P2-9).
          // Both resume with Last-Event-ID; a terminal (incl. the server's
          // `error {resumed:false}` on a miss) ends the loop — no retry.
          const abruptClose =
            !attemptError &&
            (aiStatusRef.current === 'thinking' || aiStatusRef.current === 'acting');

          if (isLastAttempt) {
            // No reconnects left (or none configured): surface the failure.
            // The `finally` block reports an exhausted abrupt close; a thrown
            // error is reported here.
            if (attemptError) {
              setAiStatus('error');
              devOnly.error('[useMapBridge] SSE stream error:', attemptError);
              onEvent({
                event: 'error',
                data: { error: attemptError instanceof Error ? attemptError.message : String(attemptError) } as unknown as Record<string, unknown>,
              });
            }
            break;
          }

          if (!attemptError && !abruptClose) break; // clean end, nothing to resume

          await sleep(baseDelayMs * 2 ** attempt); // backoff before the next attempt
        }
      } finally {
        if (abortControllerRef.current === controller) {
          // Still the active controller — update aiStatus appropriately
          if (controller.signal.aborted) {
            setAiStatus('idle');
          } else if (aiStatusRef.current === 'thinking' || aiStatusRef.current === 'acting') {
            // B-P2-9: the stream ended while still thinking/acting. If a real
            // terminal event (done/task_complete/task_cancelled/error) arrived
            // it already set the status; landing here means the stream was cut
            // without one (server died, proxy dropped, network blip — or all
            // DUP-1 reconnect attempts were exhausted). Surface it as an error
            // instead of silently showing partial content as a complete answer.
            if (gotTerminal) {
              setAiStatus('done');
            } else {
              setAiStatus('error');
              onEvent({
                event: 'error',
                data: { error: '连接已断开（未收到完成信号）。' } as unknown as Record<string, unknown>,
              });
            }
          }
          abortControllerRef.current = null;
        }
        // If not the active controller, a new send() has taken over — leave aiStatus alone
      }
    },
    [sessionId, dispatchAction, onEvent, setAiStatus, sessionTokenRef, reconnect]
  );

  // [ENG-D3] useCallback([sessionId]) — stable ref so MapPanel's handleMove deps don't churn
  const onViewportChange = useCallback(
    (center: [number, number], zoom: number, bearing: number, pitch: number) => {
      if (aiStatusRef.current !== 'thinking' && aiStatusRef.current !== 'acting') return;
      const now = Date.now();
      if (now - lastMapStatePushRef.current < MAP_STATE_THROTTLE_MS) return;
      lastMapStatePushRef.current = now;
      if (!sessionId) return;
      // SEC-08：匿名会话的 map-state 写入同样受 owner_token 保护。
      // F4: the POST carries a monotonic seq so the backend can reject it as
      // stale if a newer write (e.g. the next turn's snapshot) lands first.
      const token = sessionTokenRef?.current ?? null;
      fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}/map-state`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'X-Session-Token': token } : {}),
        },
        body: JSON.stringify({
          viewport: { center, zoom, bearing, pitch },
          seq: nextViewportSeq(viewportSeqTracker),
        }),
      }).catch((e) => devOnly.warn('[useMapBridge] map-state POST failed:', e));
    },
    [sessionId, sessionTokenRef]
  );

  // 审计 F25：返回对象用 useMemo 包裹，避免每次 render 都创建新对象引用
  // -> 下游 useCallback/useMemo 依赖 bridge 的不会每次都失效。
  return useMemo(() => ({ aiStatus, send, onViewportChange }), [aiStatus, send, onViewportChange]);
}
