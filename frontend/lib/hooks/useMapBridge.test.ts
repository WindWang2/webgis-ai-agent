import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { useMapBridge } from './useMapBridge';
import { resetViewportSeq } from '@/lib/utils/viewport-seq';
import * as chatApi from '@/lib/api/chat';
import type { SSEEvent } from '@/lib/api/chat';
import { MapActionContext } from '@/lib/contexts/map-action-context';
import type { MapActionContextType } from '@/lib/contexts/map-action-context';
import type { MapActionPayload, MapActionTerminalStatus } from '@/lib/types';
import type { MapActionTerminalDetails } from '@/lib/contexts/map-action-context';
import type { MapActionAckSink } from '@/lib/api/map-action-acks';
import { devOnly } from '@/lib/utils/logger';

const hudState = vi.hoisted(() => ({
  layers: [] as Array<{ _mapspecFingerprint?: string }>,
  activeProjectId: null as string | null,
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({
      setAiStatus: vi.fn(),
      layers: hudState.layers,
      activeProjectId: hudState.activeProjectId,
    }),
  },
}));

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8000',
}));

const mockStreamChat = vi.spyOn(chatApi, 'streamChat');

function makeAsyncGen(events: SSEEvent[]): AsyncGenerator<SSEEvent> {
  async function* gen() {
    for (const e of events) yield e;
  }
  return gen();
}

// V3: a minimal MapActionContext value that captures the registered ACK sink
// (registerAckSink) and records clearActions() calls. The context upgrade
// (FE-1) lands these on the real provider — the test injects them via the
// mock value so the bridge's V3 wiring can be exercised without it.
// reportTerminal mirrors the real provider's ack shape so store-mounted
// emissions that the bridge acks directly can be asserted on the sink.
function makeAckWrapper(
  sinkHolder: { current: MapActionAckSink | null },
  clearActions: () => void,
  ctxDispatch: (action: MapActionPayload) => void = vi.fn(),
) {
  return function AckWrapper({ children }: { children: React.ReactNode }) {
    const value = {
      actions: [],
      dispatchAction: ctxDispatch,
      popAction: vi.fn(),
      selectedBaseLayer: 0,
      setSelectedBaseLayer: vi.fn(),
      registerSnapshotFn: vi.fn(),
      getMapSnapshot: vi.fn(() => null),
      registerAckSink: (sink: MapActionAckSink) => {
        // Wrap in a spy so tests can assert on acks the bridge emits through
        // reportTerminal (store-mounted direct acks); the real sink still runs.
        sinkHolder.current = vi.fn(sink);
        return () => {
          sinkHolder.current = null;
        };
      },
      clearActions,
      reportTerminal: (
        action: MapActionPayload,
        status: MapActionTerminalStatus,
        details?: MapActionTerminalDetails,
      ) => {
        sinkHolder.current?.({
          action_id: action.action_id!,
          command: action.command,
          status,
          error: details?.error,
          started_at: details?.startedAt,
          finished_at: details?.finishedAt,
          duration_ms: details?.durationMs ?? null,
          correlation: action.correlation ?? null,
          requested: (action.params ?? {}) as Record<string, unknown> | null,
          actual: details?.actual !== undefined ? (details.actual as Record<string, unknown> | null) : null,
        });
      },
    } as unknown as MapActionContextType;
    return React.createElement(MapActionContext.Provider, { value }, children);
  };
}

describe('useMapBridge', () => {
  const dispatchAction = vi.fn();
  const onEvent = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    resetViewportSeq();
    hudState.layers = [];
    hudState.activeProjectId = null;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls streamChat even when sessionId is undefined (new session)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([]));
    const { result } = renderHook(() =>
      useMapBridge(undefined, dispatchAction, onEvent)
    );
    await act(async () => {
      await result.current.send('hello', {});
    });
    expect(mockStreamChat).toHaveBeenCalledWith(
      'hello', undefined, { viewport_seq: 1 }, expect.any(AbortSignal), undefined, null, undefined, null
    );
  });

  it('calls streamChat with (message, sessionId, mapState, signal)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([]));
    const { result } = renderHook(() =>
      useMapBridge('sid-123', dispatchAction, onEvent)
    );
    await act(async () => {
      await result.current.send('hello', { zoom: 10 });
    });
    expect(mockStreamChat).toHaveBeenCalledWith(
      'hello', 'sid-123', { zoom: 10, viewport_seq: 1 }, expect.any(AbortSignal), undefined, null, undefined, null
    );
  });

  it('#558: passes the store activeProjectId into streamChat (project context injection)', async () => {
    hudState.activeProjectId = 'proj-7';
    mockStreamChat.mockReturnValue(makeAsyncGen([]));
    const { result } = renderHook(() =>
      useMapBridge('sid-123', dispatchAction, onEvent)
    );
    await act(async () => {
      await result.current.send('hello', {});
    });
    expect(mockStreamChat).toHaveBeenCalledWith(
      'hello', 'sid-123', expect.anything(), expect.any(AbortSignal), undefined, null, undefined, 'proj-7'
    );
    hudState.activeProjectId = null;
  });

  it('calls onEvent for each SSEEvent in the stream', async () => {
    const events: SSEEvent[] = [
      { event: 'thinking', data: { content: '...' } },
      { event: 'content', data: { content: 'hi' } },
      { event: 'done', data: {} },
    ];
    mockStreamChat.mockReturnValue(makeAsyncGen(events));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onEvent).toHaveBeenNthCalledWith(1, events[0]);
    expect(onEvent).toHaveBeenNthCalledWith(2, events[1]);
  });

  it('command-wins-over-bbox: dispatches command when both present', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: { command: 'fly_to', params: { center: [116, 39], zoom: 12 } },
        bbox: [115, 38, 117, 40],
      },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [116, 39], zoom: 12 },
      // V3: every step_result dispatch now carries the session correlation
      correlation: { session_id: 's1' },
    });
  });

  it('bbox-only: calls bboxToFlyTo + dispatchAction when no command', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: { bbox: [116, 39, 117, 40] },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' })
    );
  });

  it('skips string data (SSE parse failure) without throwing', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'content', data: 'UNPARSEABLE_STRING' as unknown as Record<string, unknown> },
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await expect(
      act(async () => { await result.current.send('q', {}); })
    ).resolves.not.toThrow();
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('skips invalid bbox (west >= east)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: { bbox: [120, 30, 110, 40] },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('aborts in-flight SSE stream on unmount', async () => {
    mockStreamChat.mockImplementation(async function*(_msg, _sid, _snap, signal) {
      await new Promise<void>((resolve) => {
        signal?.addEventListener('abort', () => resolve());
      });
      yield { event: 'done', data: {} };
    });

    const { result, unmount } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    // Start send (it will hang waiting for abort)
    act(() => { result.current.send('q', {}); });
    expect(mockStreamChat).toHaveBeenCalled();
    unmount();
    // No assertion needed — cleanup runs AbortController.abort()
  });

  it('aborts previous stream when send() called again', async () => {
    const abortResolvers: Array<() => void> = [];
    mockStreamChat.mockImplementation(async function*(_msg, _sid, _snap, signal) {
      await new Promise<void>((resolve) => {
        abortResolvers.push(resolve);
        signal?.addEventListener('abort', () => resolve());
      });
      yield { event: 'done', data: {} };
    });

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    // Start first send (hangs)
    act(() => { result.current.send('first', {}); });
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
    // Start second send — this aborts the first
    act(() => { result.current.send('second', {}); });
    expect(mockStreamChat).toHaveBeenCalledTimes(2);
  });

  it('ignores an SSE event attributed to another session', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'thinking', data: { session_id: 's1' } },
      {
        event: 'step_result',
        data: {
          session_id: 'stale-session',
          result: { command: 'fly_to', params: { center: [1, 2], zoom: 10 } },
        },
      },
      { event: 'done', data: { session_id: 's1' } },
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );

    await act(async () => { await result.current.send('q', {}); });

    expect(dispatchAction).not.toHaveBeenCalled();
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls.every(
      ([event]) => (event.data as any).session_id === 's1'
    )).toBe(true);
  });

  it('aborts the old stream when starting a new undefined session', async () => {
    let wasAborted = false;
    mockStreamChat.mockImplementation(async function*(_msg, _sid, _snap, signal) {
      await new Promise<void>((resolve) => {
        signal?.addEventListener('abort', () => {
          wasAborted = true;
          resolve();
        });
      });
    });
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | undefined }) =>
        useMapBridge(sid, dispatchAction, onEvent),
      { initialProps: { sid: 's1' as string | undefined } },
    );
    act(() => { void result.current.send('q', {}); });

    act(() => { rerender({ sid: undefined }); });

    expect(wasAborted).toBe(true);
  });

  it('onViewportChange is stable across re-renders', () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([]));
    const { result, rerender } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    const first = result.current.onViewportChange;
    rerender();
    expect(result.current.onViewportChange).toBe(first);
  });

  it('onViewportChange changes when sessionId changes', () => {
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useMapBridge(sid, dispatchAction, onEvent),
      { initialProps: { sid: 's1' } }
    );
    const first = result.current.onViewportChange;
    rerender({ sid: 's2' });
    expect(result.current.onViewportChange).not.toBe(first);
  });

  it('map-state POSTs and send() snapshot carry a monotonic seq (F4)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal('fetch', fetchMock);
    // 'thinking' → aiStatus gate allows viewport POSTs during the turn.
    // The generator never ends, so the turn stays in 'thinking'.
    async function* hangingTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'thinking', data: {} };
      await new Promise(() => {});
    }
    mockStreamChat.mockReturnValue(hangingTurn());
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    act(() => { result.current.send('q', {}); });

    // send() stamp comes first (seq 1) — the turn-start write outranks any
    // in-flight POST that predates it.
    expect(mockStreamChat).toHaveBeenCalledWith(
      'q', 's1', expect.objectContaining({ viewport_seq: 1 }), expect.any(AbortSignal), undefined, null, undefined, null
    );

    // throttled POST #1 → seq 2
    act(() => { result.current.onViewportChange([1, 2], 10, 0, 0); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body1 = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body1.seq).toBe(2);
    expect(body1.viewport).toEqual({ center: [1, 2], zoom: 10, bearing: 0, pitch: 0 });

    // advance past the 2s throttle → POST #2 → seq 3
    act(() => { vi.advanceTimersByTime(2100); });
    act(() => { result.current.onViewportChange([3, 4], 11, 0, 0); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const body2 = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(body2.seq).toBe(3);

    // session switch resets the counter so the next session starts fresh
    const { result: result2, rerender: rerender2 } = renderHook(
      ({ sid }: { sid: string }) => useMapBridge(sid, dispatchAction, onEvent),
      { initialProps: { sid: 's1' } }
    );
    rerender2({ sid: 's2' });
    mockStreamChat.mockReturnValue(hangingTurn());
    act(() => { result2.current.send('x', {}); });
    expect(mockStreamChat).toHaveBeenLastCalledWith(
      'x', 's2', expect.objectContaining({ viewport_seq: 1 }), expect.any(AbortSignal), undefined, null, undefined, null
    );
    vi.unstubAllGlobals();
  });

  // ─── Heatmap command dispatch (RC1 regression tests) ───
  // Heatmap tools put data at the top level of result, not under result.params.
  // The bridge must destructure result → {command, ...rest} and pass rest as params.

  it('heatmap raster with result.image is store-mounted: acks succeeded directly, no re-dispatch (V3 FIX-2)', async () => {
    // use-sse-stream mounts the layer from result.image; the handler must not
    // re-run it (double mount / ack FAILED for work that succeeded).
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_heatmap_raster',
          image: 'data:image/png;base64,ABC123',
          bbox: [116.0, 39.0, 117.0, 40.0],
          legend_spec: { type: 'continuous', min: 0, max: 1 },
          action_id: 'ma-raster',
        },
      },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper },
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).not.toHaveBeenCalled();
    expect(sinkHolder.current).toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'ma-raster',
      command: 'add_heatmap_raster',
      status: 'succeeded',
      // ROUND-2: marker is store_mounted (not confirmed) — the mount is trusted
      // but not convergence-verifiable; and the ack fires only AFTER the mount.
      actual: { store_mounted: true },
      correlation: { session_id: 's1' },
    }));
  });

  it('heatmap raster with image under explicit params (not store-mounted) still dispatches', async () => {
    // No result.image / geojson_ref at payload top level → nothing store-mounted
    // → the bridge destructures result and passes params to the handler as before.
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_heatmap_raster',
          params: { image: 'data:image/png;base64,ABC123', bbox: [116.0, 39.0, 117.0, 40.0] },
          legend_spec: { type: 'continuous', min: 0, max: 1 },
          action_id: 'ma-raster-params',
        },
      },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'add_heatmap_raster',
      params: { image: 'data:image/png;base64,ABC123', bbox: [116.0, 39.0, 117.0, 40.0] },
      action_id: 'ma-raster-params',
      // V3: every step_result dispatch now carries the session correlation
      correlation: { session_id: 's1' },
    });
  });

  it('native heatmap: dispatches metadata + palette as params', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_native_heatmap',
          metadata: { render_type: 'native', point_count: 50, radius: 2000, palette: 'classic' },
          type: 'FeatureCollection',
        },
      },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'add_native_heatmap',
      params: expect.objectContaining({
        metadata: { render_type: 'native', point_count: 50, radius: 2000, palette: 'classic' },
      }),
      // V3: every step_result dispatch now carries the session correlation
      correlation: { session_id: 's1' },
    });
  });

  // ─── DUP-1: bounded auto-reconnect with Last-Event-ID resume ──────────────

  it('tracks the last received event id and sends it on reconnect (DUP-1)', async () => {
    async function* failingTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'token', data: { content: 'a' }, id: '1' };
      yield { event: 'token', data: { content: 'b' }, id: '2' };
      yield { event: 'token', data: { content: 'c' }, id: '3' };
      throw new TypeError('network dropped'); // mid-stream drop after 3 events
    }
    async function* resumedTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'done', data: {} };
    }
    mockStreamChat
      .mockImplementationOnce(() => failingTurn())
      .mockImplementationOnce(() => resumedTurn());

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    act(() => { result.current.send('q', {}); });
    await act(async () => {}); // let attempt 1 fail + schedule the backoff
    await act(async () => { vi.advanceTimersByTime(500); }); // fire the backoff
    await act(async () => {}); // let attempt 2 run to the terminal

    expect(mockStreamChat).toHaveBeenCalledTimes(2);
    // The reconnect re-POSTs the same turn with the last received id as
    // Last-Event-ID (7th arg) so the backend replays only the missed events.
    expect(mockStreamChat.mock.calls[1]).toEqual([
      'q', 's1', expect.anything(), expect.any(AbortSignal), undefined, null, '3', null,
    ]);
    expect(onEvent).toHaveBeenCalledWith({ event: 'done', data: {} });
  });

  it('sends Last-Event-ID "0" when the drop happened before any event', async () => {
    async function* emptyTurn(): AsyncGenerator<SSEEvent> {
      throw new TypeError('network dropped before first event');
    }
    async function* resumedTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'done', data: {} };
    }
    mockStreamChat
      .mockImplementationOnce(() => emptyTurn())
      .mockImplementationOnce(() => resumedTurn());

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 1, baseDelayMs: 500 })
    );
    act(() => { result.current.send('q', {}); });
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(500); });
    await act(async () => {});

    expect(mockStreamChat).toHaveBeenCalledTimes(2);
    expect(mockStreamChat.mock.calls[1][6]).toBe('0');
  });

  it('dedups replayed events by id on reconnect (DUP-1)', async () => {
    async function* firstTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'token', data: { content: 'a' }, id: '1' };
      yield { event: 'token', data: { content: 'b' }, id: '2' };
      // abrupt close — the stream ends with no terminal event
    }
    async function* resumedTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'token', data: { content: 'b' }, id: '2' }; // already seen
      yield { event: 'token', data: { content: 'c' }, id: '3' };
      yield { event: 'done', data: {} };
    }
    mockStreamChat
      .mockImplementationOnce(() => firstTurn())
      .mockImplementationOnce(() => resumedTurn());

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    act(() => { result.current.send('q', {}); });
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(500); });
    await act(async () => {});

    const seen = onEvent.mock.calls.map((c) => ({ event: c[0].event, id: c[0].id }));
    expect(seen).toEqual([
      { event: 'token', id: '1' },
      { event: 'token', id: '2' },
      { event: 'token', id: '3' }, // replayed id '2' was skipped
      { event: 'done', id: undefined },
    ]);
  });

  it('does not reconnect when the option is off (opt-in, DUP-1)', async () => {
    async function* failingTurn(): AsyncGenerator<SSEEvent> {
      throw new TypeError('network dropped');
    }
    mockStreamChat.mockImplementationOnce(() => failingTurn());
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
    const last = onEvent.mock.calls.at(-1)?.[0];
    expect(last?.event).toBe('error');
  });

  it('stops reconnecting after maxAttempts (bounded backoff, DUP-1)', async () => {
    async function* failingTurn(): AsyncGenerator<SSEEvent> {
      throw new TypeError('network dropped');
    }
    mockStreamChat.mockImplementation(() => failingTurn());
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    act(() => { result.current.send('q', {}); });
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(500); }); // attempt 2 (base)
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(1000); }); // attempt 3 (doubled)
    await act(async () => {});
    expect(mockStreamChat).toHaveBeenCalledTimes(3); // initial + 2 retries, then give up
    const last = onEvent.mock.calls.at(-1)?.[0];
    expect(last?.event).toBe('error');
  });

  it('never reconnects after a terminal event (DUP-1)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
  });

  it('never reconnects after a resume-miss error terminal (resumed:false)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'error', data: { error: 'resume unavailable', resumed: false } },
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
    expect(result.current.aiStatus).toBe('error');
  });

  // ─── V3: correlation + action_id passthrough (design §6) ──────────────────

  it('passes backend action_id + full correlation through for a single command (V3)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: { command: 'fly_to', params: { center: [116, 39], zoom: 12 }, action_id: 'ma-abc123' },
        step_id: 'step-1',
        turn_id: 'turn-1',
        task_id: 'task-9',
      },
      id: '7',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [116, 39], zoom: 12 },
      action_id: 'ma-abc123',
      correlation: {
        session_id: 's1',
        task_id: 'task-9',
        step_id: 'step-1',
        turn_id: 'turn-1',
        sse_event_id: '7',
      },
    });
  });

  it('passes per-command action_id + correlation for batch commands (V3)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          commands: [
            { command: 'add_layer', params: { id: 'a' }, action_id: 'ma-111' },
            { command: 'remove_layer', params: { id: 'b' }, action_id: 'ma-222' },
          ],
        },
        step_id: 'step-2',
        turn_id: 'turn-1',
      },
      id: '8',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledTimes(2);
    expect(dispatchAction).toHaveBeenNthCalledWith(1, {
      command: 'add_layer',
      params: { id: 'a' },
      action_id: 'ma-111',
      correlation: { session_id: 's1', step_id: 'step-2', turn_id: 'turn-1', sse_event_id: '8' },
    });
    expect(dispatchAction).toHaveBeenNthCalledWith(2, {
      command: 'remove_layer',
      params: { id: 'b' },
      action_id: 'ma-222',
      correlation: { session_id: 's1', step_id: 'step-2', turn_id: 'turn-1', sse_event_id: '8' },
    });
  });

  it('bbox fallback mints a client fe- action_id + correlation (V3)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: { bbox: [116, 39, 117, 40], step_id: 'step-3', turn_id: 'turn-2' },
      id: '9',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({
        command: 'fly_to',
        // ROUND-2: fe-<uuid> (crypto.randomUUID, Date.now+random fallback) —
        // NOT a counter that resets on reload and collides with the same
        // session's earlier acks (backend first-terminal-wins).
        action_id: expect.stringMatching(/^fe-[0-9a-z-]{4,}$/),
        correlation: { session_id: 's1', step_id: 'step-3', turn_id: 'turn-2', sse_event_id: '9' },
      })
    );
  });

  it('mints a UNIQUE fe- id per synthesized action (no counter reset collisions after reload)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'step_result', data: { bbox: [116, 39, 117, 40], step_id: 's1', turn_id: 't1' }, id: '9' },
      { event: 'step_result', data: { bbox: [110, 20, 111, 21], step_id: 's2', turn_id: 't1' }, id: '10' },
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(dispatchAction).toHaveBeenCalledTimes(2);
    const ids = dispatchAction.mock.calls.map((c) => c[0].action_id);
    expect(ids[0]).toMatch(/^fe-[0-9a-z-]{4,}$/);
    expect(ids[1]).toMatch(/^fe-[0-9a-z-]{4,}$/);
    expect(ids[0]).not.toBe(ids[1]);
  });

  it('does NOT re-dispatch replayed step_result ids on reconnect (DUP-1)', async () => {
    async function* firstTurn(): AsyncGenerator<SSEEvent> {
      yield {
        event: 'step_result',
        data: { result: { command: 'fly_to', params: { center: [1, 2], zoom: 10 } }, step_id: 's1', turn_id: 't1' },
        id: '5',
      };
      throw new TypeError('network dropped');
    }
    async function* resumedTurn(): AsyncGenerator<SSEEvent> {
      // The server replays id '5' (stale Last-Event-ID) — must be skipped.
      yield {
        event: 'step_result',
        data: { result: { command: 'fly_to', params: { center: [1, 2], zoom: 10 } }, step_id: 's1', turn_id: 't1' },
        id: '5',
      };
      yield {
        event: 'step_result',
        data: { result: { command: 'fly_to', params: { center: [3, 4], zoom: 11 } }, step_id: 's2', turn_id: 't1' },
        id: '6',
      };
      yield { event: 'done', data: {} };
    }
    mockStreamChat
      .mockImplementationOnce(() => firstTurn())
      .mockImplementationOnce(() => resumedTurn());

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 500 })
    );
    act(() => { result.current.send('q', {}); });
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(500); });
    await act(async () => {});

    // Exactly two dispatches: the original id '5' and the new id '6' — the
    // replayed '5' must NOT re-dispatch.
    expect(dispatchAction).toHaveBeenCalledTimes(2);
    expect(dispatchAction.mock.calls.map((c) => c[0].correlation?.sse_event_id)).toEqual(['5', '6']);
  });

  // ─── V3: ACK sender wiring (design §6) ────────────────────────────────────

  it('batches terminal acks into one debounced POST (V3)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    const sink = sinkHolder.current;
    expect(sink).toBeTruthy();
    act(() => {
      sink!({ action_id: 'ma-1', command: 'fly_to', status: 'succeeded' });
      sink!({ action_id: 'ma-2', command: 'fly_to', status: 'succeeded' });
      sink!({ action_id: 'ma-3', command: 'fly_to', status: 'succeeded' });
    });
    expect(fetchMock).not.toHaveBeenCalled(); // 500ms debounce not elapsed
    await act(async () => { vi.advanceTimersByTime(500); });

    expect(fetchMock).toHaveBeenCalledTimes(1); // coalesced into one POST
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://localhost:8000/api/v1/chat/sessions/s1/map-action-ack');
    expect(JSON.parse(init.body as string).acks).toHaveLength(3);
    vi.unstubAllGlobals();
  });

  it('ACK POST transient failure retries without blocking the stream or map (V3)', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ accepted: 1, duplicates: 0 }) });
    vi.stubGlobal('fetch', fetchMock);
    const warnSpy = vi.spyOn(devOnly, 'warn');
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    act(() => {
      sinkHolder.current!({ action_id: 'ma-1', command: 'fly_to', status: 'failed', error: 'boom' });
    });
    await act(async () => { vi.advanceTimersByTime(500); });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(warnSpy).toHaveBeenCalled();

    // The map/stream loop is unaffected: a fresh turn still dispatches actions
    // while the ACK retry timer is still pending.
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: { result: { command: 'fly_to', params: { center: [1, 2], zoom: 10 } }, step_id: 's9', turn_id: 't9' },
      id: '10',
    }]));
    await act(async () => { await result.current.send('q2', {}); });
    expect(dispatchAction).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(400); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryBody = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(retryBody.acks[0].action_id).toBe('ma-1');

    warnSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('ACK sink still POSTs after React Strict Mode remount', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ accepted: 1, duplicates: 0 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const Inner = makeAckWrapper(sinkHolder, vi.fn());
    function StrictWrap({ children }: { children: React.ReactNode }) {
      return React.createElement(React.StrictMode, null, React.createElement(Inner, null, children));
    }

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper: StrictWrap }
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(sinkHolder.current).toBeTruthy();
    act(() => {
      sinkHolder.current!({ action_id: 'ma-strict', command: 'fly_to', status: 'succeeded' });
    });
    await act(async () => { vi.advanceTimersByTime(500); });

    expect(fetchMock).toHaveBeenCalled();
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.acks[0].action_id).toBe('ma-strict');
    vi.unstubAllGlobals();
  });

  it('unmount flush is not aborted so the tail ACK POST can finish', async () => {
    let capturedSignal: AbortSignal | undefined;
    let release: ((value: unknown) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      capturedSignal = init.signal as AbortSignal | undefined;
      return new Promise((resolve) => {
        release = resolve;
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result, unmount } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    act(() => {
      sinkHolder.current!({ action_id: 'ma-tail', command: 'fly_to', status: 'succeeded' });
    });
    act(() => { unmount(); });
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(capturedSignal?.aborted).toBe(false);

    await act(async () => {
      release?.({
        ok: true,
        status: 200,
        json: async () => ({ accepted: 1, duplicates: 0 }),
      });
    });

    expect(capturedSignal?.aborted).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it('unmount flushes once and does not leak a retry timer (fault 10)', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result, unmount } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    act(() => {
      sinkHolder.current!({ action_id: 'ma-unmount', command: 'fly_to', status: 'succeeded' });
    });
    act(() => { unmount(); });
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it('does not apply an ACK repair whose mapspec fingerprint is no longer live', async () => {
    hudState.layers = [{ _mapspecFingerprint: 'carto-sha256:new' }];
    const staleRepair = {
      action_id: 'ma-carto-stale',
      command: 'cartographic_runtime_repair' as const,
      params: { id: 'layer-1', mapspec_fingerprint: 'carto-sha256:old' },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ accepted: 1, duplicates: 0, repair_action: staleRepair }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const ctxDispatch = vi.fn();
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn(), ctxDispatch);

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    act(() => {
      sinkHolder.current!({ action_id: 'ma-orig', command: 'add_layer', status: 'succeeded' });
    });
    await act(async () => { vi.advanceTimersByTime(500); });

    expect(ctxDispatch).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('duplicate ACK repair_action responses do not dispatch twice (fault 11)', async () => {
    const repair = {
      action_id: 'ma-carto-1',
      command: 'cartographic_runtime_repair' as const,
      params: { id: 'layer-1' },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ accepted: 1, duplicates: 0, repair_action: repair }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const ctxDispatch = vi.fn();
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn(), ctxDispatch);

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper }
    );
    await act(async () => { await result.current.send('q', {}); });

    act(() => {
      sinkHolder.current!({ action_id: 'ma-orig-1', command: 'add_layer', status: 'succeeded' });
    });
    await act(async () => { vi.advanceTimersByTime(500); });
    act(() => {
      sinkHolder.current!({ action_id: 'ma-orig-2', command: 'add_layer', status: 'succeeded' });
    });
    await act(async () => { vi.advanceTimersByTime(500); });

    expect(ctxDispatch).toHaveBeenCalledTimes(1);
    expect(ctxDispatch).toHaveBeenCalledWith(repair);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });

  it('session switch calls clearActions and flushes the pending ACK queue (V3)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    const clearActions = vi.fn();
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, clearActions);

    mockStreamChat.mockReturnValue(makeAsyncGen([{ event: 'done', data: {} }]));
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useMapBridge(sid, dispatchAction, onEvent),
      { wrapper, initialProps: { sid: 's1' } }
    );
    await act(async () => { await result.current.send('q', {}); });

    // One terminal ack queued (debounce not elapsed yet).
    act(() => {
      sinkHolder.current!({ action_id: 'ma-1', command: 'fly_to', status: 'succeeded' });
    });
    expect(fetchMock).not.toHaveBeenCalled();

    // Switch session → pending actions cancelled + ACK queue flushed.
    act(() => { rerender({ sid: 's2' }); });

    expect(clearActions).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/sessions/s1/map-action-ack');
    expect(JSON.parse(init.body as string).acks[0].action_id).toBe('ma-1');
    vi.unstubAllGlobals();
  });

  // ─── V3 review FIX-2 (7) [P2]: store-mounted data emissions ────────────
  // use-sse-stream's onEvent mounts a HUD layer when the step_result payload
  // carries `geojson_ref` or `result.image`. Dispatching those commands to the
  // handler afterwards would double-mount or fail (invalid_params /
  // target_not_found) for work that already succeeded — the store mount IS the
  // execution. The bridge must ack succeeded directly with the correlation.

  it('store-mounted step_result acks succeeded directly instead of dispatching (V3, P2)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_native_heatmap',
          metadata: { render_type: 'native', point_count: 50, radius: 2000, palette: 'classic' },
          type: 'FeatureCollection',
          action_id: 'ma-heat',
        },
        geojson_ref: 'ref-heat-1',
        step_id: 'step-4',
        turn_id: 'turn-3',
      },
      id: '11',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper },
    );
    await act(async () => { await result.current.send('q', {}); });

    // handler NOT invoked — the store mount already executed the work
    expect(dispatchAction).not.toHaveBeenCalled();
    const sink: any = sinkHolder.current;
    expect(sink).toBeTruthy();
    // ROUND-2: the ack is reported only AFTER onEvent (the mount) returned —
    // never claims success BEFORE the mount ran.
    expect(onEvent.mock.invocationCallOrder[0]).toBeLessThan(sink.mock.invocationCallOrder[0]);
    expect(sink).toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'ma-heat',
      command: 'add_native_heatmap',
      status: 'succeeded',
      // ROUND-2: store_mounted (not confirmed) — not convergence-verifiable
      actual: { store_mounted: true },
      correlation: {
        session_id: 's1',
        step_id: 'step-4',
        turn_id: 'turn-3',
        sse_event_id: '11',
      },
    }));
    vi.unstubAllGlobals();
  });

  it('store-mounted add_heatmap_raster (result.image) acks succeeded directly (V3, P2)', async () => {
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());

    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_heatmap_raster',
          image: 'data:image/png;base64,ABC123',
          bbox: [116.0, 39.0, 117.0, 40.0],
          action_id: 'ma-raster',
        },
        step_id: 'step-5',
        turn_id: 'turn-3',
      },
      id: '12',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
      { wrapper },
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(dispatchAction).not.toHaveBeenCalled();
    expect(sinkHolder.current).toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'ma-raster',
      command: 'add_heatmap_raster',
      status: 'succeeded',
      // ROUND-2: store_mounted (not confirmed) — not convergence-verifiable
      actual: { store_mounted: true },
      correlation: { session_id: 's1', step_id: 'step-5', turn_id: 'turn-3', sse_event_id: '12' },
    }));
  });

  it('does NOT skip non-mount commands even when the payload is store-mounted (V3, P2)', async () => {
    // A fly_to riding on a store-mounted payload must still dispatch — the store
    // mount only mounts layers, it never moves the camera.
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: { command: 'fly_to', params: { center: [116, 39], zoom: 12 }, action_id: 'ma-fly' },
        geojson_ref: 'ref-x',
        step_id: 's1',
        turn_id: 't1',
      },
      id: '13',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to', action_id: 'ma-fly' }),
    );
  });

  it('store-mounted ack is FAILED (never succeeded/confirmed) when the mount (onEvent) throws (ROUND-2)', async () => {
    const sinkHolder: { current: MapActionAckSink | null } = { current: null };
    const wrapper = makeAckWrapper(sinkHolder, vi.fn());
    // The store mount throws → the work did NOT happen; claiming success would
    // be a fake ack. The bridge must report failed instead.
    const mountOnEvent = vi.fn((e: SSEEvent) => {
      if (e.event === 'step_result') throw new Error('mount exploded');
    });

    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_native_heatmap',
          type: 'FeatureCollection',
          action_id: 'ma-heat',
        },
        geojson_ref: 'ref-heat-1',
        step_id: 'step-4',
        turn_id: 'turn-3',
      },
      id: '11',
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, mountOnEvent),
      { wrapper },
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(dispatchAction).not.toHaveBeenCalled();
    // The bridge rethrows → the stream error path synthesizes an error event.
    expect(mountOnEvent.mock.calls.map((c) => c[0].event)).toContain('error');
    expect(sinkHolder.current).toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'ma-heat',
      command: 'add_native_heatmap',
      status: 'failed',
      error: 'mount exploded',
    }));
    // never a fake succeeded/confirmed
    expect(sinkHolder.current).not.toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'ma-heat',
      status: 'succeeded',
    }));
  });

  // ─── Invariants Hardening Tests (INV-1, INV-3, INV-9, INV-10) ────────────

  it('INV-3: terminal state cannot be rolled back by subsequent out-of-order running events', async () => {
    // Stream delivers a terminal done, followed by an out-of-order acting event
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'thinking', data: {} },
      { event: 'done', data: {} },
      { event: 'acting', data: {} }, // out-of-order late event
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(result.current.aiStatus).toBe('done'); // Must NOT roll back to 'acting'
  });

  it('INV-3: error terminal cannot be rolled back by subsequent out-of-order step_start', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([
      { event: 'thinking', data: {} },
      { event: 'error', data: { error: 'something broke' } },
      { event: 'step_start', data: {} }, // out-of-order late event
    ]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });

    expect(result.current.aiStatus).toBe('error'); // Must NOT roll back to 'acting'
  });

  it('INV-10: non-retryable 4xx HTTP errors (e.g. 400/401/403/404) stop reconnect immediately', async () => {
    const apiError = new Error('Not found');
    (apiError as any).status = 404;
    mockStreamChat.mockImplementation(() => {
      throw apiError;
    });

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 3, baseDelayMs: 500 })
    );

    await act(async () => {
      await result.current.send('q', {});
    });

    // Must NOT retry 3 times when error is a 404 client error!
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
    expect(result.current.aiStatus).toBe('error');
  });

  it('INV-9: aborting stream during reconnect backoff sleep stops immediately without sending next request', async () => {
    async function* failingTurn(): AsyncGenerator<SSEEvent> {
      throw new TypeError('network dropped');
    }
    async function* resumedTurn(): AsyncGenerator<SSEEvent> {
      yield { event: 'done', data: {} };
    }
    mockStreamChat
      .mockImplementationOnce(() => failingTurn())
      .mockImplementationOnce(() => resumedTurn());

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, { maxAttempts: 2, baseDelayMs: 1000 })
    );

    act(() => {
      void result.current.send('first', {});
    });
    await act(async () => {}); // Attempt 1 fails, enters 1000ms backoff sleep

    // New send() is initiated while attempt 1 is sleeping
    act(() => {
      void result.current.send('second', {});
    });

    // Advance timers past the first backoff
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    // The sleeping first stream must have been aborted and never made its 2nd attempt
    // Total mockStreamChat calls should be: 1 (first attempt 1) + 1 (second stream attempt 1) = 2
    expect(mockStreamChat).toHaveBeenCalledTimes(2);
    expect(mockStreamChat.mock.calls[1][0]).toBe('second');
  });
});

