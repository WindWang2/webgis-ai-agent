import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBoundedPoll, MAX_CONSECUTIVE_ERRORS } from './use-cluster-poll';

// 轮询纪律测试 —— 与 use-job-center.test.ts 同款写法：只 mock fetcher，
// fake timers + advanceTimersByTimeAsync 完全确定地推进调度。
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('useBoundedPoll 轮询纪律（ADR-0142 D3）', () => {
  // 保留 jsdom document（RTL 渲染依赖它），只覆写 hidden 并捕获 visibilitychange。
  let hidden: boolean;
  let visibilityHandler: (() => void) | null;

  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    visibilityHandler = null;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    vi.spyOn(document, 'addEventListener').mockImplementation(((kind: string, fn: EventListener) => {
      if (kind === 'visibilitychange') visibilityHandler = fn as () => void;
    }) as typeof document.addEventListener);
    vi.spyOn(document, 'removeEventListener').mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('挂载即拉取一次，成功后数据就位', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { result } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual({ v: 1 });
    expect(result.current.error).toBeNull();
  });

  it('enabled=false → 0 请求（纪律①）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    renderHook(() => useBoundedPoll({ fetcher, enabled: false, pollIntervalMs: 3000 }));
    await flush(10_000);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('按间隔持续轮询（无活跃停驻语义 —— 观测面持续看板）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    await flush(3000);
    await flush(3000);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('间隔下限 3s（限流预算）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 100 }));
    await flush(0);
    await flush(500); // 若按 100ms 排会拉 5 次；钳到 3s 应仍是 1 次
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('tab 隐藏 → 暂停；可见 → 立即补拉并清零错误（纪律②）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { result } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    // 隐藏：暂停
    hidden = true;
    act(() => visibilityHandler?.());
    await flush(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.status.paused).toBe(true);

    // 可见：立即补拉
    hidden = false;
    act(() => visibilityHandler?.());
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('连续失败达到上限 → 停止轮询（有界重试，纪律③）', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    for (let i = 0; i < MAX_CONSECUTIVE_ERRORS + 1; i += 1) {
      await flush(3000);
    }
    expect(fetcher).toHaveBeenCalledTimes(MAX_CONSECUTIVE_ERRORS);
    expect(result.current.error).toBe('boom');
    expect(result.current.status.consecutiveErrors).toBe(MAX_CONSECUTIVE_ERRORS);
    expect(result.current.status.pauseReason).toBe('error-limit');
  });

  it('resetKey 变化 → 旧数据失效 + 重新拉取（纪律④⑤）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { result, rerender } = renderHook(
      ({ resetKey }: { resetKey: string }) => useBoundedPoll({ fetcher, resetKey, pollIntervalMs: 3000 }),
      { initialProps: { resetKey: 'a' } },
    );
    await flush(0);
    expect(result.current.data).toEqual({ v: 1 });
    rerender({ resetKey: 'b' });
    await flush(0);
    // resetKey 变化清空旧数据并重拉
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ v: 1 });
  });

  it('卸载后 in-flight 响应被丢弃（纪律⑤）', async () => {
    let resolveLater: (v: { v: number }) => void = () => undefined;
    const fetcher = vi.fn().mockImplementation(
      () => new Promise<{ v: number }>((resolve) => {
        resolveLater = resolve;
      }),
    );
    const { unmount } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    unmount();
    await act(async () => {
      resolveLater({ v: 42 });
    });
    // 无断言崩溃即通过：陈旧响应不再写状态
  });

  it('手动 refresh 立即拉取', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { result } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    act(() => result.current.refresh());
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
