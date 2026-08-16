import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream, applyExplorerProgressToStore } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';

/**
 * #518 regression: explorer_progress had no producer into the chat stream and
 * deep_explore's independent progress stream was unreachable (owner never
 * registered). The frontend now:
 *   - normalizes explorer_progress events into the explorerTasks store via
 *     applyExplorerProgressToStore (shared by chat stream + independent
 *     stream), and
 *   - starts the independent /explorer/stream/{task_id} consumer when a
 *     step_result carries an explorer_task result.
 */

const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  aiStatus: 'idle',
  onEventCallback: null as ((event: {
    event: string;
    data: Record<string, unknown>;
  }) => void) | null,
}));

vi.mock('./useMapBridge', () => ({
  useMapBridge: (...args: unknown[]) => {
    bridgeMock.onEventCallback = args[2] as typeof bridgeMock.onEventCallback;
    return bridgeMock;
  },
}));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const streamExplorerProgressMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/explorer', () => ({
  streamExplorerProgress: (...args: unknown[]) => streamExplorerProgressMock(...args),
}));

const setSessionId = vi.fn();
const dispatchAction = vi.fn();
const getMapSnapshot = vi.fn(() => null);

function renderStream() {
  return renderHook(() =>
    useSSEStream(
      'sid-explorer',
      setSessionId,
      { current: 'sid-explorer' },
      dispatchAction,
      getMapSnapshot,
      null,
      { current: null },
    ),
  );
}

beforeEach(() => {
  bridgeMock.send.mockClear();
  bridgeMock.onEventCallback = null;
  streamExplorerProgressMock.mockReset();
  useHudStore.setState({ explorerTasks: [] });
});

describe('applyExplorerProgressToStore (normalization shared by chat + independent stream)', () => {
  it('inserts a task on first sight and updates it on progress events', () => {
    applyExplorerProgressToStore({
      stage: 'discover',
      task_id: 'exp-task-1',
      status: 'started',
      context: { progress: 5 },
    });
    let tasks = useHudStore.getState().explorerTasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].taskId).toBe('exp-task-1');
    expect(tasks[0].stage).toBe('discover');
    expect(tasks[0].status).toBe('discovering');

    applyExplorerProgressToStore({
      stage: 'geocode',
      task_id: 'exp-task-1',
      status: 'progress',
      context: { progress: 70 },
    });
    tasks = useHudStore.getState().explorerTasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].stage).toBe('geocode');
    expect(tasks[0].status).toBe('geocoding');
    expect(tasks[0].progress).toBe(70);
  });

  it('terminates on completed/failed and normalizes pending stage to discover', () => {
    applyExplorerProgressToStore({
      stage: 'validate',
      task_id: 'exp-task-2',
      status: 'completed',
      context: { progress: 100 },
    });
    const tasks = useHudStore.getState().explorerTasks;
    expect(tasks[0].status).toBe('completed');

    applyExplorerProgressToStore({
      stage: 'pending',
      task_id: 'exp-task-3',
      status: 'progress',
      context: {},
    });
    expect(useHudStore.getState().explorerTasks[1].stage).toBe('discover');
    expect(useHudStore.getState().explorerTasks[1].status).toBe('idle');
  });

  it('ignores malformed / empty events', () => {
    applyExplorerProgressToStore(null);
    applyExplorerProgressToStore(undefined);
    applyExplorerProgressToStore({});
    applyExplorerProgressToStore({ task_id: 42, stage: 'x', status: 'y' });
    expect(useHudStore.getState().explorerTasks).toHaveLength(0);
  });
});

describe('useSSEStream — independent explorer progress stream (#518)', () => {
  async function emitStepResult(overrides: Record<string, unknown>) {
    renderStream();
    await act(async () => {
      bridgeMock.onEventCallback?.({
        event: 'step_result',
        data: {
          session_id: 'sid-explorer',
          tool: 'deep_explore',
          result: { type: 'explorer_task', task_id: 'exp-sess-1-123', status: 'started' },
          ...overrides,
        },
      });
    });
  }

  it('opens the independent progress stream when deep_explore returns an explorer_task', async () => {
    streamExplorerProgressMock.mockImplementation(async function* () {
      yield { event: 'explorer_progress', data: { stage: 'fetch', task_id: 'exp-sess-1-123', status: 'progress', context: { progress: 30 } } };
      yield { event: 'explorer_progress', data: { stage: 'validate', task_id: 'exp-sess-1-123', status: 'completed', context: { progress: 100 } } };
    });

    await emitStepResult({});

    expect(streamExplorerProgressMock).toHaveBeenCalledWith('exp-sess-1-123', expect.anything());
    // 独立流的事件必须进入同一个 explorerTasks store
    const tasks = useHudStore.getState().explorerTasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].taskId).toBe('exp-sess-1-123');
    expect(tasks[0].status).toBe('completed');
  });

  it('does not open a duplicate stream for the same task_id', async () => {
    streamExplorerProgressMock.mockImplementation(async function* () {
      yield { event: 'explorer_progress', data: { stage: 'discover', task_id: 'exp-task-x', status: 'started', context: {} } };
    });

    renderStream();
    const step = {
      event: 'step_result',
      data: {
        session_id: 'sid-explorer',
        tool: 'deep_explore',
        result: { type: 'explorer_task', task_id: 'exp-task-x', status: 'started' },
      },
    };
    await act(async () => { bridgeMock.onEventCallback?.(step); });
    await act(async () => { bridgeMock.onEventCallback?.(step); }); // 同 task 第二次 step_result

    expect(streamExplorerProgressMock).toHaveBeenCalledTimes(1);
  });

  it('does not start a stream for non-explorer results', async () => {
    await emitStepResult({ result: { type: 'FeatureCollection', features: [] } });
    expect(streamExplorerProgressMock).not.toHaveBeenCalled();
  });
});
