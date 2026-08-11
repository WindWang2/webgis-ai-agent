import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import { MapActionProvider, useMapAction } from './map-action-context';
import type { MapActionPayload } from '@/lib/types';
import type { MapActionAck } from '@/lib/api/map-action-acks';

// The provider's base-layer lazy init reads useHudStore.getState().baseLayer and
// TILE_PROVIDERS; both are mocked so the test is hermetic.
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: { getState: () => ({ baseLayer: 'Carto 深色' }) },
}));

vi.mock('@/lib/providers', () => ({
  TILE_PROVIDERS: [
    { name: 'Carto 浅色', keywords: ['carto', 'light', '浅色'] },
    { name: 'Carto 深色', keywords: ['dark', '深色'] },
    { name: 'ESRI 影像', keywords: ['satellite', '卫星'] },
  ],
}));

function makeAction(overrides: Partial<MapActionPayload> = {}): MapActionPayload {
  return {
    command: 'fly_to',
    params: { center: [116, 39], zoom: 12 },
    ...overrides,
  } as MapActionPayload;
}

function renderCtx() {
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <MapActionProvider>{children}</MapActionProvider>
  );
  return renderHook(() => useMapAction(), { wrapper });
}

describe('MapActionProvider (V3 queue + lifecycle)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('appends a normalized action on dispatch', () => {
    const { result } = renderCtx();
    act(() => {
      result.current.dispatchAction({ command: 'FLY_TO', params: { center: [116, 39], zoom: 12 } });
    });
    expect(result.current.actions).toHaveLength(1);
    // command normalized to lowercase; UPPERCASE backend emissions tolerated
    expect(result.current.actions[0].command).toBe('fly_to');
    expect(result.current.runningActionId).toBe(result.current.actions[0].action_id);
  });

  it('mints a client action_id + issued_at when the backend did not', () => {
    const { result } = renderCtx();
    act(() => {
      result.current.dispatchAction({ command: 'fly_to', params: { center: [116, 39], zoom: 12 } });
    });
    const action = result.current.actions[0];
    expect(action.action_id).toMatch(/^fe-/);
    expect(typeof action.issued_at).toBe('string');
    expect(action.issued_at!.length).toBeGreaterThan(0);
  });

  it('keeps the backend-minted action_id when present', () => {
    const { result } = renderCtx();
    act(() => {
      result.current.dispatchAction(makeAction({ action_id: 'ma-abc123' }));
    });
    expect(result.current.actions[0].action_id).toBe('ma-abc123');
  });

  it('throttles identical fly_to within 2s (same center+zoom)', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    const { result } = renderCtx();
    const action = () => makeAction({ command: 'fly_to', params: { center: [116, 39], zoom: 12 } });
    act(() => { result.current.dispatchAction(action()); });
    act(() => { result.current.dispatchAction(action()); });
    expect(result.current.actions).toHaveLength(1);
    // after 2s the throttle window closes — same fly_to is accepted again
    act(() => { vi.advanceTimersByTime(2000); });
    act(() => { result.current.dispatchAction(action()); });
    expect(result.current.actions).toHaveLength(2);
  });

  it('does NOT throttle a fly_to to a different destination', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    const { result } = renderCtx();
    act(() => { result.current.dispatchAction(makeAction({ params: { center: [116, 39], zoom: 12 } })); });
    act(() => { result.current.dispatchAction(makeAction({ params: { center: [117, 40], zoom: 12 } })); });
    expect(result.current.actions).toHaveLength(2);
  });

  describe('camera coalesce', () => {
    it('supersedes a still-QUEUED camera action when a newer camera command arrives', () => {
      const { result } = renderCtx();
      const sink = vi.fn();
      act(() => { result.current.registerAckSink(sink); });

      // A non-camera action occupies the running head; the first camera action
      // queues behind it. The second camera command supersedes the queued one.
      act(() => { result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-head' })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'fly_to', action_id: 'ma-first', params: { center: [116, 39], zoom: 12 } })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'set_map_view', action_id: 'ma-second', params: { zoom: 13 } })); });

      expect(result.current.actions.map((a) => a.action_id)).toEqual(['ma-head', 'ma-second']);
      // superseded ack with the design reason
      expect(sink).toHaveBeenCalledWith(expect.objectContaining({
        action_id: 'ma-first',
        command: 'fly_to',
        status: 'superseded',
        error: 'newer_camera_command',
      }));
      // ring records the superseded terminal (ma-second is still queued, not run)
      expect(result.current.completedActions.map((c) => c.status)).toEqual(['superseded']);
    });

    it('covers zoom_to_bbox and set_map_view as camera commands', () => {
      const { result } = renderCtx();
      act(() => { result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-head' })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'set_map_view', action_id: 'ma-a', params: { zoom: 11 } })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'zoom_to_bbox', action_id: 'ma-b', params: { bbox: [116, 39, 117, 40] } })); });
      expect(result.current.actions.map((a) => a.action_id)).toEqual(['ma-head', 'ma-b']);
    });

    it('never supersedes the running head (queue index 0)', () => {
      const { result } = renderCtx();
      // head is a non-camera command already "running"; a camera action is queued behind it
      act(() => { result.current.dispatchAction(makeAction({ command: 'add_layer', action_id: 'ma-head' })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'fly_to', action_id: 'ma-queued', params: { center: [116, 39], zoom: 12 } })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'fly_to', action_id: 'ma-new', params: { center: [117, 40], zoom: 13 } })) });

      const ids = result.current.actions.map((a) => a.action_id);
      // queued camera superseded; running head + new camera remain
      expect(ids).toEqual(['ma-head', 'ma-new']);
    });

    it('does not supersede queued non-camera actions', () => {
      const { result } = renderCtx();
      act(() => { result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-head' })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'fly_to', action_id: 'ma-a', params: { center: [116, 39], zoom: 12 } })); });
      act(() => { result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-marker' })); });
      expect(result.current.actions.map((a) => a.action_id)).toEqual(['ma-head', 'ma-a', 'ma-marker']);
    });
  });

  describe('MAX_PENDING_ACTIONS=32 overflow', () => {
    it('drops the oldest QUEUED action (never the running head) and counts it', () => {
      const { result } = renderCtx();
      act(() => {
        // head + 31 queued = 32 pending
        for (let i = 0; i < 32; i++) {
          result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: `ma-${i}` }));
        }
      });
      expect(result.current.actions).toHaveLength(32);
      expect(result.current.droppedCount).toBe(0);

      act(() => {
        result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-overflow' }));
      });

      expect(result.current.actions).toHaveLength(32);
      // head preserved, newest appended, oldest queued (ma-1) dropped
      expect(result.current.actions[0].action_id).toBe('ma-0');
      expect(result.current.actions[31].action_id).toBe('ma-overflow');
      expect(result.current.actions.map((a) => a.action_id)).not.toContain('ma-1');
      expect(result.current.droppedCount).toBe(1);
    });
  });

  describe('clearActions (session switch)', () => {
    it('marks every pending action cancelled(session_switch), acks them, and empties the queue', () => {
      const { result } = renderCtx();
      const sink = vi.fn();
      act(() => { result.current.registerAckSink(sink); });
      act(() => {
        result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-1' }));
        result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: 'ma-2' }));
      });

      act(() => { result.current.clearActions(); });

      expect(result.current.actions).toHaveLength(0);
      expect(sink).toHaveBeenCalledTimes(2);
      expect(sink).toHaveBeenCalledWith(expect.objectContaining({
        action_id: 'ma-1', status: 'cancelled', error: 'session_switch',
      }));
      expect(sink).toHaveBeenCalledWith(expect.objectContaining({
        action_id: 'ma-2', status: 'cancelled', error: 'session_switch',
      }));
    });

    it('resets droppedCount (session-scoped)', () => {
      const { result } = renderCtx();
      // fill to overflow once
      act(() => {
        for (let i = 0; i < 33; i++) {
          result.current.dispatchAction(makeAction({ command: 'add_marker', action_id: `ma-${i}` }));
        }
      });
      expect(result.current.droppedCount).toBe(1);
      act(() => { result.current.clearActions(); });
      expect(result.current.droppedCount).toBe(0);
    });
  });

  describe('ack sink + completedActions ring', () => {
    it('reportTerminal fans out to registered sinks with the full ack shape', () => {
      const { result } = renderCtx();
      const sink = vi.fn();
      act(() => { result.current.registerAckSink(sink); });
      const action = makeAction({
        action_id: 'ma-ack',
        command: 'fly_to',
        correlation: { session_id: 's1', turn_id: 't1' },
      });
      act(() => { result.current.dispatchAction(action); });
      act(() => {
        result.current.reportTerminal(action, 'succeeded', {
          error: undefined,
          actual: { center: [117, 40], zoom: 13 },
          startedAt: '2026-01-01T00:00:00.000Z',
          finishedAt: '2026-01-01T00:00:00.100Z',
          durationMs: 100,
        });
      });

      const expectedAck: MapActionAck = {
        action_id: 'ma-ack',
        command: 'fly_to',
        status: 'succeeded',
        error: undefined,
        started_at: '2026-01-01T00:00:00.000Z',
        finished_at: '2026-01-01T00:00:00.100Z',
        duration_ms: 100,
        correlation: { session_id: 's1', turn_id: 't1' },
        requested: { center: [116, 39], zoom: 12 },
        actual: { center: [117, 40], zoom: 13 },
      };
      expect(sink).toHaveBeenCalledWith(expectedAck);
      expect(result.current.completedActions).toHaveLength(1);
      expect(result.current.completedActions[0]).toMatchObject({
        action_id: 'ma-ack',
        status: 'succeeded',
        actual: { center: [117, 40], zoom: 13 },
      });
    });

    it('first terminal wins per action_id (mirrors backend idempotency)', () => {
      const { result } = renderCtx();
      const sink = vi.fn();
      act(() => { result.current.registerAckSink(sink); });
      const action = makeAction({ action_id: 'ma-dup' });
      act(() => { result.current.dispatchAction(action); });
      act(() => { result.current.reportTerminal(action, 'succeeded'); });
      act(() => { result.current.reportTerminal(action, 'failed', { error: 'late' }); });
      expect(sink).toHaveBeenCalledTimes(1);
      expect(result.current.completedActions).toHaveLength(1);
      expect(result.current.completedActions[0].status).toBe('succeeded');
    });

    it('keeps the ring bounded at 100 (oldest evicted)', () => {
      const { result } = renderCtx();
      for (let i = 0; i < 105; i++) {
        const action = makeAction({ command: 'add_marker', action_id: `ma-ring-${i}` });
        act(() => { result.current.dispatchAction(action); });
        act(() => { result.current.reportTerminal(action, 'succeeded'); });
      }
      expect(result.current.completedActions).toHaveLength(100);
      expect(result.current.completedActions[0].action_id).toBe('ma-ring-5');
      expect(result.current.completedActions[99].action_id).toBe('ma-ring-104');
    });

    it('unsubscribe stops future acks reaching the sink', () => {
      const { result } = renderCtx();
      const sink = vi.fn();
      let off = () => {};
      act(() => { off = result.current.registerAckSink(sink); });
      const action = makeAction({ action_id: 'ma-u1' });
      act(() => { result.current.dispatchAction(action); });
      act(() => { result.current.reportTerminal(action, 'succeeded'); });
      expect(sink).toHaveBeenCalledTimes(1);

      act(() => { off(); });
      const action2 = makeAction({ action_id: 'ma-u2' });
      act(() => { result.current.dispatchAction(action2); });
      act(() => { result.current.reportTerminal(action2, 'succeeded'); });
      expect(sink).toHaveBeenCalledTimes(1);
    });
  });

  it('runningActionId is null when the queue is empty', () => {
    const { result } = renderCtx();
    expect(result.current.runningActionId).toBeNull();
  });

  it('popAction with a mismatched action_id does NOT pop (per-action settle guard)', () => {
    const { result } = renderCtx();
    const a = makeAction({ action_id: 'ma-head' });
    const b = makeAction({ action_id: 'ma-next', command: 'add_layer' });
    act(() => { result.current.dispatchAction(a); });
    act(() => { result.current.dispatchAction(b); });
    // A stale settle for a non-head action must not drop the real head.
    act(() => { result.current.popAction('ma-next'); });
    expect(result.current.actions.map((x) => x.action_id)).toEqual(['ma-head', 'ma-next']);
    // The matching id pops exactly one action.
    act(() => { result.current.popAction('ma-head'); });
    expect(result.current.actions.map((x) => x.action_id)).toEqual(['ma-next']);
  });

  it('200 camera enqueues coalesce: pending queue stays bounded by head + 1 (deterministic work-count evidence)', () => {
    const { result } = renderCtx();
    const sink = vi.fn();
    act(() => { result.current.registerAckSink(sink); });
    // 200 burst camera commands at DIFFERENT targets (bypasses the identical-fly_to throttle).
    for (let i = 0; i < 200; i++) {
      act(() => {
        result.current.dispatchAction(makeAction({ action_id: `ma-cam-${i}`, params: { center: [116 + i / 100, 39], zoom: 12 + i / 100 } }));
      });
    }
    // Head (running) + at most one pending camera action — every earlier one was
    // superseded, not queued. Work done is O(1) pending state, not O(200).
    expect(result.current.actions.length).toBeLessThanOrEqual(2);
    // Superseded actions were acked with a terminal state (cam-1..cam-198;
    // cam-199 stays queued as the pending successor — the head is never superseded).
    const superseded = sink.mock.calls.map((c) => c[0]).filter((a: MapActionAck) => a.status === 'superseded');
    expect(superseded.length).toBe(198);
  });
});
