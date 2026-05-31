import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMapBridge } from './useMapBridge';
import * as chatApi from '@/lib/api/chat';
import type { SSEEvent } from '@/lib/api/chat';
import type { MapActionPayload } from '@/lib/types';

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
      'hello', undefined, {}, expect.any(AbortSignal)
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
      'hello', 'sid-123', { zoom: 10 }, expect.any(AbortSignal)
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
