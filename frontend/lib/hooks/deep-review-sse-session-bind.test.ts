import { it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { useChatStore } from '@/lib/store/useChatStore';

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
vi.mock('@/lib/data-plane/ref-service', () => ({ requestRefFC: requestRefFCMock }));

function emitStepResult() {
  act(() => {
    bridgeMock.onEventCallback?.({
      event: 'step_result',
      data: {
        task_id: 't1',
        step_id: 's1',
        tool: 'buffer_analysis',
        geojson_ref: 'ref:bind-test',
        result: { success: true, summary: 'ok' },
      },
    });
  });
}

/**
 * FE-07 回归：sessionId undefined → assigned 是服务端为当前实时流补发
 * session id（useMapBridge 同款豁免），此前该转换会 abort 正在进行的
 * 图层 ref 拉取；只有真正的会话切换才应中止旧请求。
 */
describe('useSSEStream 会话绑定不中止在飞拉取（FE-07）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ layers: [], results: [] });
    // H03：useChatStore 是消息单一 owner（模块单例）——测试间清空避免串扰。
    useChatStore.setState({ messages: [], streamingToken: '' });
    requestRefFCMock.mockReturnValue(new Promise(() => undefined));
  });

  it('undefined→assigned 保留在飞 signal；真实切换才 abort', () => {
    const sidRef: { current: string | undefined } = { current: undefined };
    // options 对象随 rerender 重建，但 ref 字段必须保持同一对象引用。
    const sessionTokenRef: { current: string | null } = { current: null };
    const { rerender } = renderHook(
      ({ sid }: { sid: string | undefined }) =>
        useSSEStream({
          sessionId: sid,
          setSessionId: vi.fn(),
          sessionIdRef: sidRef,
          dispatchAction: vi.fn(),
          getMapSnapshot: () => null,
          userLocation: null,
          sessionTokenRef,
        }),
      { initialProps: { sid: undefined as string | undefined } },
    );

    emitStepResult();
    expect(requestRefFCMock).toHaveBeenCalledTimes(1);
    const signal = requestRefFCMock.mock.calls[0][0].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    // 服务端为当前实时流补发 session id：不是切换
    rerender({ sid: 'sid-bound' });
    expect(signal.aborted).toBe(false);

    // A → B 真实切换：旧会话在飞拉取中止
    rerender({ sid: 'sid-other' });
    expect(signal.aborted).toBe(true);
  });
});
