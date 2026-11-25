import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { useChatStore } from '@/lib/store/useChatStore';
import { useToastStore } from '@/components/ui/toast';

/**
 * 编辑提交后的数据层增量刷新（refreshDatasetLayer）的
 * `requestRefFC(...).then(...)` 链必须接 rejection：调度器结算 reject 或
 * then 体内 store 写入抛错都不得变成 unhandled rejection（对照 add-layer
 * 链路的兜底 catch）。
 */

const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  aiStatus: 'idle',
  onEventCallback: null as ((event: {
    event: string;
    data: Record<string, unknown>;
  }) => void) | null,
}));

const requestRefFCMock = vi.hoisted(() => vi.fn());

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
vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
  isApiError: (err: unknown) => err instanceof Error && err.name === 'ApiError',
}));
vi.mock('@/lib/data-plane/ref-service', () => ({ requestRefFC: requestRefFCMock }));

function emitFeatureEditCommit() {
  act(() => {
    bridgeMock.onEventCallback?.({
      event: 'step_result',
      data: {
        task_id: 't1',
        step_id: 's1',
        tool: 'vector_feature_edit_commit',
        result: {
          success: true,
          plan_id: 'plan-1',
          ref_id: 'ref:edit',
          content_revision_after: 3,
        },
      },
    });
  });
}

async function flushFetch() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  // Node 的 unhandledRejection 在 macrotask 边界才判定。
  await new Promise((r) => setTimeout(r, 20));
}

beforeEach(() => {
  vi.clearAllMocks();
  useHudStore.setState({
    layers: [{ id: 'l1', _refId: 'ref:edit', name: 'L', type: 'vector' }] as never,
    results: [],
  });
  useToastStore.setState({ toasts: [] });
  useChatStore.setState({ messages: [], streamingToken: '' });
  renderHook(() =>
    useSSEStream({
      sessionId: 'sid-edit',
      setSessionId: vi.fn(),
      sessionIdRef: { current: 'sid-edit' },
      dispatchAction: vi.fn(),
      getMapSnapshot: () => null,
      userLocation: null,
      sessionTokenRef: { current: null },
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

it('refreshDatasetLayer：调度器 reject 被接管（无 unhandled rejection）', async () => {
  const seen: unknown[] = [];
  const onUnhandled = (reason: unknown) => { seen.push(reason); };
  process.on('unhandledRejection', onUnhandled);
  try {
    requestRefFCMock.mockRejectedValueOnce(new Error('scheduler-exploded'));
    emitFeatureEditCommit();
    await flushFetch();
    expect(requestRefFCMock).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(0);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});

it('refreshDatasetLayer：then 体内抛错被接管（无 unhandled rejection）', async () => {
  const seen: unknown[] = [];
  const onUnhandled = (reason: unknown) => { seen.push(reason); };
  process.on('unhandledRejection', onUnhandled);
  try {
    requestRefFCMock.mockResolvedValueOnce({
      get fc(): never {
        throw new Error('boom-inside-then');
      },
    });
    emitFeatureEditCommit();
    await flushFetch();
    expect(requestRefFCMock).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(0);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});
