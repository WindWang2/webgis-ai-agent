'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { streamChat } from '@/lib/api/chat';
import type { SSEEvent } from '@/lib/api/chat';
import { useHudStore } from '@/lib/store/useHudStore';
import type { AiStatus } from '@/lib/store/hud-types';
import type { MapActionPayload } from '@/lib/types';
import { bboxToFlyTo, isValidBbox } from '@/lib/utils/geo';
import { API_BASE } from '@/lib/api/config';
import type { StepResultEvent } from '@/lib/types/agent-events';

const MAP_STATE_THROTTLE_MS = 2000;

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
): {
  aiStatus: AiStatus;
  send: (content: string, mapSnapshot: Record<string, unknown>) => Promise<void>;
  onViewportChange: (center: [number, number], zoom: number, bearing: number, pitch: number) => void;
} {
  if (process.env.NODE_ENV === 'development' && !onEvent) {
    console.error('[useMapBridge] onEvent prop is required');
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

  // [DX1] Auto-abort on sessionId change and unmount — AbortController is fully internal
  useEffect(() => {
    if (prevSessionIdRef.current !== undefined && sessionId !== undefined && prevSessionIdRef.current !== sessionId) {
      // Abort only on explicit session switch, not on server assignment for a new session
      abortControllerRef.current?.abort();
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

      try {
        for await (const event of streamChat(content, sessionId, mapSnapshot, controller.signal)) {
          if (controller.signal.aborted) break;

          // Skip unparseable data — streamChat yields raw string on JSON.parse failure
          if (typeof event.data === 'string') {
            console.warn('[useMapBridge] SSE parse failure, skipping:', event.event);
            onEvent(event);
            continue;
          }

          const data = event.data as Record<string, unknown>;

          // aiStatus transitions
          if (event.event === 'thinking') setAiStatus('thinking');
          else if (event.event === 'acting' || event.event === 'step_start') setAiStatus('acting');
          else if (event.event === 'done' || event.event === 'task_complete') setAiStatus('done');
          else if (event.event === 'error' || event.event === 'step_error' || event.event === 'task_error') setAiStatus('error');

          // step_result: command-wins-over-bbox priority; dispatch before forwarding to onEvent
          if (event.event === 'step_result') {
            const stepData = data as unknown as StepResultEvent;
            const commandFired = !!stepData.result?.command;
            if (commandFired) {
              dispatchAction({
                command: stepData.result!.command as MapActionPayload['command'],
                params: (stepData.result!.params || {}) as MapActionPayload['params'],
              });
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
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setAiStatus('error');
        console.error('[useMapBridge] SSE stream error:', err);
        onEvent({ event: 'error', data: { error: err instanceof Error ? err.message : String(err) } as unknown as Record<string, unknown> });
      } finally {
        if (abortControllerRef.current === controller) {
          // Still the active controller — update aiStatus appropriately
          if (controller.signal.aborted) {
            setAiStatus('idle');
          } else if (aiStatusRef.current === 'thinking' || aiStatusRef.current === 'acting') {
            setAiStatus('done');
          }
          abortControllerRef.current = null;
        }
        // If not the active controller, a new send() has taken over — leave aiStatus alone
      }
    },
    [sessionId, dispatchAction, onEvent, setAiStatus]
  );

  // [ENG-D3] useCallback([sessionId]) — stable ref so MapPanel's handleMove deps don't churn
  const onViewportChange = useCallback(
    (center: [number, number], zoom: number, bearing: number, pitch: number) => {
      if (aiStatusRef.current !== 'thinking' && aiStatusRef.current !== 'acting') return;
      const now = Date.now();
      if (now - lastMapStatePushRef.current < MAP_STATE_THROTTLE_MS) return;
      lastMapStatePushRef.current = now;
      if (!sessionId) return;
      fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}/map-state`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ viewport: { center, zoom, bearing, pitch } }),
      }).catch((e) => console.warn('[useMapBridge] map-state POST failed:', e));
    },
    [sessionId]
  );

  return { aiStatus, send, onViewportChange };
}
