import { it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { devOnly } from '@/lib/utils/logger';

/**
 * LiveLayerFetch abort-noise contract: the layer-data fetch is aborted by our
 * own session-switch/unmount control flow, and transport rethrows caller
 * aborts as native AbortError. That is expected control flow — it must not
 * reach devOnly.error (console noise in every session switch). Genuine
 * failures still log.
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
  // Transport's real isApiError checks for ApiError; AbortError is not one.
  isApiError: (err: unknown) => err instanceof Error && err.name === 'ApiError',
}));

function emitStepResult() {
  act(() => {
    bridgeMock.onEventCallback?.({
      event: 'step_result',
      data: {
        task_id: 't1',
        step_id: 's1',
        tool: 'buffer_analysis',
        geojson_ref: 'ref:abort-test',
        result: { success: true, summary: 'ok' },
      },
    });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  useHudStore.setState({ layers: [], results: [] });
  useToastStore.setState({ toasts: [] });
  renderHook(() =>
    useSSEStream(
      'sid-abort',
      vi.fn(),
      { current: 'sid-abort' },
      vi.fn(),
      () => null,
      null,
      { current: null },
    ),
  );
});

async function flushFetch() {
  // Let the .then/.catch microtasks of the layer fetch settle.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

it('does not log an error when the layer fetch is aborted by us (AbortError)', async () => {
  const abortErr = new DOMException('The user aborted a request.', 'AbortError');
  apiFetchMock.mockRejectedValueOnce(abortErr);

  emitStepResult();
  await flushFetch();

  expect(apiFetchMock).toHaveBeenCalled();
  expect(devOnly.error).not.toHaveBeenCalled();
});

it('still logs genuine layer fetch failures', async () => {
  apiFetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

  emitStepResult();
  await flushFetch();

  expect(devOnly.error).toHaveBeenCalledWith(
    '[LiveLayerFetch] Failed to fetch geojson_ref:',
    expect.any(TypeError),
  );
  expect(useToastStore.getState().toasts.some((t) => t.type === 'error')).toBe(true);
});

it('logs ApiError and toasts instead of swallowing the failure', async () => {
  const apiErr = Object.assign(new Error('Layer data error: 403'), {
    name: 'ApiError',
    status: 403,
  });
  apiFetchMock.mockRejectedValueOnce(apiErr);

  emitStepResult();
  await flushFetch();

  expect(devOnly.error).toHaveBeenCalledWith(
    '[LiveLayerFetch] Failed to fetch geojson_ref:',
    apiErr,
  );
  const toast = useToastStore.getState().toasts.find((t) => t.type === 'error');
  expect(toast?.message).toMatch(/加载失败/);
});
