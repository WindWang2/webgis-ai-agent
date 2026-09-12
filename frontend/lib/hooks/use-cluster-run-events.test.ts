import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useClusterRunEvents, MAX_BUFFERED_EVENTS } from './use-cluster-run-events';
import type { GeoComputeRunEvent } from '@/lib/api/geocompute';

// 游标通道纪律测试：mock geocompute 客户端，验证 after_id 推进、终态即停、
// retention 404 诚实态、有界缓冲、隐藏暂停。
const api = vi.hoisted(() => ({
  getRunEvents: vi.fn(),
}));

vi.mock('@/lib/api/geocompute', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/geocompute')>();
  return {
    ...actual,
    getRunEvents: api.getRunEvents,
  };
});

function ev(id: number, event: string, created_at = '2026-09-12T01:00:00Z'): GeoComputeRunEvent {
  return { id, run_id: 'run-1', event, node_id: null, worker_id: null, attempt: null, status: null, rows: null, bytes: null, error_code: null, created_at };
}

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('useClusterRunEvents 游标轮询（ADR-0142 D3）', () => {
  // 保留 jsdom document（RTL 渲染依赖它），只覆写 hidden。
  let hidden: boolean;

  beforeEach(() => {
    vi.useFakeTimers();
    api.getRunEvents.mockReset();
    hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    vi.spyOn(document, 'addEventListener').mockImplementation(() => undefined);
    vi.spyOn(document, 'removeEventListener').mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('首页取回后游标推进到页尾 id', async () => {
    api.getRunEvents.mockResolvedValue({ run_id: 'run-1', events: [ev(1, 'run_started'), ev(2, 'node_started')], after_id: 2, count: 2 });
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    expect(api.getRunEvents).toHaveBeenCalledWith('run-1', expect.objectContaining({ afterId: 0 }));
    expect(result.current.cursor).toBe(2);
    expect(result.current.events).toHaveLength(2);
    expect(result.current.channel).toBe('polling');
  });

  it('下一页从 after_id 续读（游标语义）', async () => {
    api.getRunEvents
      .mockResolvedValueOnce({ run_id: 'run-1', events: [ev(1, 'run_started')], after_id: 1, count: 1 })
      .mockResolvedValueOnce({ run_id: 'run-1', events: [ev(2, 'node_completed')], after_id: 2, count: 1 });
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    await flush(3000);
    expect(api.getRunEvents).toHaveBeenLastCalledWith('run-1', expect.objectContaining({ afterId: 1 }));
    expect(result.current.events).toHaveLength(2);
  });

  it('终态事件 → 停止轮询（channel=terminal）', async () => {
    api.getRunEvents.mockResolvedValue({ run_id: 'run-1', events: [ev(9, 'run_completed')], after_id: 9, count: 1 });
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    await flush(10_000);
    expect(api.getRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.channel).toBe('terminal');
  });

  it('retention 404 → notfound 诚实态并停止', async () => {
    api.getRunEvents.mockRejectedValue(new (await import('@/lib/api/geocompute')).RunEventsUnavailableError('not_found'));
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    await flush(10_000);
    expect(api.getRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.channel).toBe('notfound');
    expect(result.current.error).toContain('不可用');
  });

  it('缓冲有界：超过上限保留最近 MAX_BUFFERED_EVENTS 条', async () => {
    let id = 0;
    api.getRunEvents.mockImplementation(async (_runId: string, opts: { afterId?: number }) => {
      const events: GeoComputeRunEvent[] = [];
      for (let i = 0; i < 200; i += 1) {
        id += 1;
        events.push(ev(id, 'node_dispatched'));
      }
      return { run_id: 'run-1', events, after_id: opts.afterId! + 200, count: 200 };
    });
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    await flush(3000);
    await flush(3000);
    await flush(3000);
    expect(result.current.events.length).toBeLessThanOrEqual(MAX_BUFFERED_EVENTS);
    expect(result.current.events.length).toBe(MAX_BUFFERED_EVENTS);
  });

  it('runId=null → idle 且 0 请求', async () => {
    renderHook(() => useClusterRunEvents({ runId: null }));
    await flush(10_000);
    expect(api.getRunEvents).not.toHaveBeenCalled();
  });

  it('实时通道下的终态在窗口后半段到达仍会停（页内混合事件）', async () => {
    api.getRunEvents.mockResolvedValue({
      run_id: 'run-1',
      events: [ev(3, 'node_completed'), ev(4, 'node_dispatched'), ev(5, 'run_failed')],
      after_id: 5,
      count: 3,
    });
    const { result } = renderHook(() => useClusterRunEvents({ runId: 'run-1', pollIntervalMs: 3000 }));
    await flush(0);
    await flush(10_000);
    expect(result.current.channel).toBe('terminal');
    expect(api.getRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events).toHaveLength(3);
  });
});
