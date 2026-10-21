import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { useChatStore } from '@/lib/store/useChatStore';
import { useToastStore } from '@/components/ui/toast';

/**
 * #1746-2：SSE add-layer 的 `requestRefFC(...).then(...)` 链必须接 rejection。
 *
 * then 体内是 toast/store 写入（reportLayerFetchFailure / markRefSourceFailed /
 * useHudStore 写入）—— 任一环节抛错都会把链变成 unhandled rejection，控制台
 * 无痕迹。同仓库另一处完全相同调用（map-state-restore.ts
 * restoreSessionMapLayers）早已带兜底 catch，此处补齐同一防御姿势。
 */

const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  aiStatus: 'idle',
  onEventCallback: null as ((event: {
    event: string;
    data: Record<string, unknown>;
  }) => void) | null,
}));

const apiFetchMock = vi.hoisted(() => vi.fn());
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
  apiFetch: apiFetchMock,
  isApiError: (err: unknown) => err instanceof Error && err.name === 'ApiError',
}));
vi.mock('@/lib/data-plane/ref-service', () => ({ requestRefFC: requestRefFCMock }));

function emitStepResult() {
  act(() => {
    bridgeMock.onEventCallback?.({
      event: 'step_result',
      data: {
        task_id: 't1',
        step_id: 's1',
        tool: 'buffer_analysis',
        geojson_ref: 'ref:add-layer-catch',
        result: { success: true, summary: 'ok' },
      },
    });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  useHudStore.setState({ layers: [], results: [] });
  useToastStore.setState({ toasts: [] });
  useChatStore.setState({ messages: [], streamingToken: '' });
  renderHook(() =>
    useSSEStream({
      sessionId: 'sid-catch',
      setSessionId: vi.fn(),
      sessionIdRef: { current: 'sid-catch' },
      dispatchAction: vi.fn(),
      getMapSnapshot: () => null,
      userLocation: null,
      sessionTokenRef: { current: null },
    }),
  );
});

async function flushFetch() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  // Node 的 unhandledRejection 在 macrotask 边界才判定。
  await new Promise((r) => setTimeout(r, 20));
}

afterEach(() => {
  vi.useRealTimers();
});

it('add-layer：then 体内抛错被接管（无 unhandled rejection，且上报成失败 toast）', async () => {
  const seen: unknown[] = [];
  const onUnhandled = (reason: unknown) => {
    seen.push(reason);
  };
  process.on('unhandledRejection', onUnhandled);

  try {
    // res.status 的 getter 抛错 → .then 体内同步抛出 → 链 reject。
    requestRefFCMock.mockResolvedValueOnce({
      get status(): string {
        throw new Error('boom-inside-then');
      },
    });

    emitStepResult();
    await flushFetch();

    expect(requestRefFCMock).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(0);
    expect(useToastStore.getState().toasts.some((t) => t.type === 'error')).toBe(true);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});

it('add-layer：requestRefFC 自身 reject 也被接管（无 unhandled rejection）', async () => {
  const seen: unknown[] = [];
  const onUnhandled = (reason: unknown) => {
    seen.push(reason);
  };
  process.on('unhandledRejection', onUnhandled);

  try {
    requestRefFCMock.mockRejectedValueOnce(new Error('scheduler-exploded'));

    emitStepResult();
    await flushFetch();

    expect(seen).toHaveLength(0);
    expect(useToastStore.getState().toasts.some((t) => t.type === 'error')).toBe(true);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});
