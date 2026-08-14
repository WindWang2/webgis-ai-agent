import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMapBridge } from './useMapBridge';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import type { SSEEvent } from '@/lib/api/chat';

vi.mock('@/lib/api/chat', () => ({
  streamChat: vi.fn(),
}));

import { streamChat } from '@/lib/api/chat';
const mockStreamChat = vi.mocked(streamChat);

async function* makeAsyncGen(events: SSEEvent[], delayMs = 0): AsyncGenerator<SSEEvent> {
  for (const ev of events) {
    if (delayMs > 0) {
      await new Promise((r) => setTimeout(r, delayMs));
    }
    yield ev;
  }
}

describe('Frontend SSE Adversarial Stress Tests', () => {
  const dispatchAction = vi.fn();
  const onEvent = vi.fn();
  const getMapSnapshot = vi.fn().mockReturnValue({});

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    useHudStore.getState().clearLayers();
    useHudStore.getState().clearResults();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('Adversarial 1: Rapid session hopping (A -> B -> A) during active token stream with reconnect', async () => {
    let streamCount = 0;
    mockStreamChat.mockImplementation(async function* (
      msg,
      sid,
      _snap,
      signal,
    ) {
      const idx = ++streamCount;
      yield { event: 'thinking', data: { session_id: sid } };
      yield { event: 'token', data: { content: `s${idx}_t1 `, session_id: sid }, id: '1' };
      if (signal?.aborted) return;
      yield { event: 'token', data: { content: `s${idx}_t2 `, session_id: sid }, id: '2' };
      yield { event: 'done', data: { session_id: sid }, id: '3' };
    });

    let currentSid = 'session-A';
    const sidRef = { current: currentSid };
    const setSid = vi.fn((s) => {
      currentSid = s;
      sidRef.current = s;
    });

    const { result, rerender } = renderHook(
      ({ sid }) =>
        useSSEStream(
          sid,
          setSid,
          sidRef,
          dispatchAction,
          getMapSnapshot,
          null,
          { current: null },
        ),
      { initialProps: { sid: 'session-A' } },
    );

    // 1. Start send on session A
    let sendPromiseA: Promise<void> | undefined;
    act(() => {
      sendPromiseA = result.current.handleSend('hello session A');
    });

    // 2. Mid-flight hop to session B (aborts session A)
    rerender({ sid: 'session-B' });
    sidRef.current = 'session-B';

    // 3. Start and await send on session B
    await act(async () => {
      await result.current.handleSend('hello session B');
      vi.runAllTimers();
    });

    await act(async () => {
      await sendPromiseA;
    });

    // 4. Mid-flight hop back to session A
    rerender({ sid: 'session-A' });
    sidRef.current = 'session-A';

    await act(async () => {
      vi.runAllTimers();
    });

    // Verify: No infinite loops, no uncaught exceptions, active status is stable
    expect(result.current.isLoading).toBe(false);
  });

  it('Adversarial 2: Duplicate out-of-order burst delivery does not corrupt state or trigger duplicate actions', async () => {
    mockStreamChat.mockReturnValue(
      makeAsyncGen([
        { event: 'thinking', data: { session_id: 's1' } },
        { event: 'token', data: { content: 'chunk 1 ' }, id: '1' },
        { event: 'token', data: { content: 'chunk 1 ' }, id: '1' }, // Network duplicate F9
        { event: 'token', data: { content: 'chunk 2 ' }, id: '2' },
        {
          event: 'step_result',
          data: {
            session_id: 's1',
            tool: 'custom_search',
            result: { command: 'fly_to', bbox: [116, 39, 117, 40] },
          },
          id: '3',
        },
        {
          event: 'step_result',
          data: {
            session_id: 's1',
            tool: 'custom_search',
            result: { command: 'fly_to', bbox: [116, 39, 117, 40] },
          },
          id: '3', // Duplicate step_result F9
        },
        { event: 'done', data: { session_id: 's1' }, id: '4' },
        { event: 'acting', data: { session_id: 's1' } }, // Out of order running event F10
      ]),
    );

    const { result } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent),
    );

    await act(async () => {
      await result.current.send('test dup burst', {});
    });

    // fly_to action dispatched exactly once (deduped id: 3)
    expect(dispatchAction).toHaveBeenCalledTimes(1);
    expect(dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' }),
    );
    // Terminal status remains 'done' despite late 'acting' event
    expect(result.current.aiStatus).toBe('done');
  });

  it('Adversarial 3: Unmount during exponential backoff timer cleans up timer and aborts immediately', async () => {
    async function* failingStream(): AsyncGenerator<SSEEvent> {
      throw new Error('dropped');
    }
    mockStreamChat.mockImplementation(() => failingStream());

    const { result, unmount } = renderHook(() =>
      useMapBridge('s1', dispatchAction, onEvent, undefined, {
        maxAttempts: 3,
        baseDelayMs: 2000,
      }),
    );

    act(() => {
      void result.current.send('failing turn', {});
    });
    await act(async () => {}); // Attempt 1 fails, enters 2000ms sleep

    // Unmount hook while sleeping
    unmount();

    // Advance time far into future
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    // Stream must NOT have retried further after unmount
    expect(mockStreamChat).toHaveBeenCalledTimes(1);
  });
});
