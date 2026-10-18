/**
 * ADR-0224（review P2-6b）接线回归：SSE step_result 的 render_work 信封
 * 收纳 —— 顶层/嵌套 result 两种载荷形状、跨会话拒收、迟到旧代不覆盖。
 *
 * store 本身的防御式解析/代次门单测在 render-work.test.ts；此处锁定
 * use-sse-stream 的接线点（data?.render_work ?? data?.result?.render_work
 * + sessionIdRef 传递）真实生效。
 */
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { useChatStore } from '@/lib/store/useChatStore';
import {
  getLatestRenderWork,
  resetRenderWorkStore,
} from '@/lib/render-protocol/render-work';

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

function envelope(over: Record<string, unknown> = {}) {
  return {
    schema_version: 'render_work.v1',
    work_id: 'rw-7-aaaaaaaaaaaa',
    session_id: 'sess-sse',
    mapspec_revision: 7,
    mapspec_fingerprint: 'fp-sse',
    observation_target_seq: 3,
    data_dependencies: [],
    layer_work: [],
    component_work: [],
    invalidation: { data_changed: [], layers_added: [], layers_removed: [], components_changed: [] },
    required_capabilities: [],
    diagnostics: [],
    work_fingerprint: 'wf-sse',
    ...over,
  };
}

describe('useSSEStream render_work 信封收纳（ADR-0224）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ layers: [], results: [] });
    useChatStore.setState({ messages: [], streamingToken: '' });
    requestRefFCMock.mockReturnValue(new Promise(() => undefined));
    resetRenderWorkStore();
  });
  afterEach(() => resetRenderWorkStore());

  function renderFor(sid: string) {
    const sidRef: { current: string | undefined } = { current: sid };
    renderHook(() =>
      useSSEStream({
        sessionId: sid,
        setSessionId: vi.fn(),
        sessionIdRef: sidRef,
        dispatchAction: vi.fn(),
        registerToken: vi.fn(),
        rememberSessionToken: vi.fn(),
      } as never),
    );
    return sidRef;
  }

  function emit(data: Record<string, unknown>) {
    act(() => {
      bridgeMock.onEventCallback?.({ event: 'step_result', data });
    });
  }

  it('嵌套 result.render_work 形状收纳进 store', () => {
    renderFor('sess-sse');
    emit({
      task_id: 't1',
      step_id: 's1',
      tool: 'buffer_analysis',
      result: { success: true, render_work: envelope() },
    });
    expect(getLatestRenderWork('sess-sse')?.work_id).toBe('rw-7-aaaaaaaaaaaa');
  });

  it('顶层 render_work 形状同样收纳', () => {
    renderFor('sess-sse');
    emit({
      task_id: 't1',
      step_id: 's1',
      tool: 'buffer_analysis',
      render_work: envelope(),
    });
    expect(getLatestRenderWork('sess-sse')?.mapspec_revision).toBe(7);
  });

  it('跨会话信封拒收（session_id 不匹配）', () => {
    renderFor('sess-sse');
    emit({
      task_id: 't1',
      step_id: 's1',
      tool: 'buffer_analysis',
      result: { success: true, render_work: envelope({ session_id: 'sess-OTHER' }) },
    });
    expect(getLatestRenderWork('sess-sse')).toBeNull();
  });

  it('迟到旧代信封不覆盖新代', () => {
    renderFor('sess-sse');
    emit({
      task_id: 't1',
      step_id: 's1',
      tool: 'buffer_analysis',
      result: {
        success: true,
        render_work: envelope({ mapspec_revision: 9, work_id: 'rw-9-new' }),
      },
    });
    emit({
      task_id: 't2',
      step_id: 's2',
      tool: 'buffer_analysis',
      result: {
        success: true,
        render_work: envelope({ mapspec_revision: 8, work_id: 'rw-8-late' }),
      },
    });
    expect(getLatestRenderWork('sess-sse')?.work_id).toBe('rw-9-new');
  });

  it('坏形状载荷拒收且不炸主链（step_result 照常处理）', () => {
    renderFor('sess-sse');
    expect(() =>
      emit({
        task_id: 't1',
        step_id: 's1',
        tool: 'buffer_analysis',
        result: { success: true, render_work: { garbage: true } },
      }),
    ).not.toThrow();
    expect(getLatestRenderWork('sess-sse')).toBeNull();
  });
});
