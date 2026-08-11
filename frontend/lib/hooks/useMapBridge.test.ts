import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMapBridge } from './useMapBridge';
import { resetViewportSeq } from '@/lib/utils/viewport-seq';
import * as chatApi from '@/lib/api/chat';
import type { SSEEvent } from '@/lib/api/chat';

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({
      setAiStatus: vi.fn(),
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

describe('useMapBridge', () => {
  const dispatchAction = vi.fn();
  const onEvent = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    resetViewportSeq();
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
      'hello', undefined, { viewport_seq: 1 }, expect.any(AbortSignal), undefined, null
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
      'hello', 'sid-123', { zoom: 10, viewport_seq: 1 }, expect.any(AbortSignal), undefined, null
    );
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
      'q', 's1', expect.objectContaining({ viewport_seq: 1 }), expect.any(AbortSignal), undefined, null
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
      'x', 's2', expect.objectContaining({ viewport_seq: 1 }), expect.any(AbortSignal), undefined, null
    );
    vi.unstubAllGlobals();
  });

  // ─── Heatmap command dispatch (RC1 regression tests) ───
  // Heatmap tools put data at the top level of result, not under result.params.
  // The bridge must destructure result → {command, ...rest} and pass rest as params.

  it('heatmap raster: dispatches image + bbox as params (not result.params)', async () => {
    mockStreamChat.mockReturnValue(makeAsyncGen([{
      event: 'step_result',
      data: {
        result: {
          command: 'add_heatmap_raster',
          image: 'data:image/png;base64,ABC123',
          bbox: [116.0, 39.0, 117.0, 40.0],
          legend_spec: { type: 'continuous', min: 0, max: 1 },
        },
      },
    }]));
    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent)
    );
    await act(async () => { await result.current.send('q', {}); });
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'add_heatmap_raster',
      params: expect.objectContaining({
        image: 'data:image/png;base64,ABC123',
        bbox: [116.0, 39.0, 117.0, 40.0],
        legend_spec: { type: 'continuous', min: 0, max: 1 },
      }),
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
    });
  });
});
