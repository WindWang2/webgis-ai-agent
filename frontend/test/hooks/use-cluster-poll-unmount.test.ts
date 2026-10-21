/**
 * 卸载纪律⑤的可观测回归（#1748）。
 *
 * 为什么单独立一个文件：lib/hooks/use-cluster-poll.test.ts 里那条同名用例
 * （「卸载后 in-flight 响应被丢弃（纪律⑤）」）没有任何 expect，源码注释自承
 * 「无断言崩溃即通过」。React 19 下 setState-after-unmount 是静默 no-op，
 * 那条用例对它声称要守的回归零检测力。
 *
 * 这里把同一场景改成可观测断言（三条，缺一不可）：
 *   1. 卸载时在飞请求被 abort —— 纪律⑤的真正机制（use-cluster-poll.ts:184），
 *      AbortSignal 可直接观测；
 *   2. 卸载后陈旧响应兑现 → 不发生任何 setState —— 包一层 React.useState 的
 *      setter，只记录卸载窗口内的调用（React 19 把这些调用静默吞掉，不包
 *      setter 就完全观测不到）；
 *   3. 卸载后不得再发起轮询 / refresh 不得再拉取 —— fetcher 调用数冻结。
 *
 * 与被测源码的差异仅为观测手段，不断言源码之外的行为。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';

/** vi.mock 工厂在文件顶部提升执行，记录状态必须经 vi.hoisted 提前建好。 */
const unmountProbe = vi.hoisted(() => ({
  /** 卸载之后发生的 setState 调用（参数值）。空数组 = 卸载后零写入。 */
  writes: [] as unknown[],
  recording: false,
}));

// React 19 的 ESM namespace 不可配置，vi.spyOn(React, 'useState') 直接报
// "Module namespace is not configurable" —— 只能整模块 mock：透传其余导出，
// 只把 useState 的 setter 包一层记录器。react-dom 的 hook 调用走内部
// dispatcher、不经过这个导出，所以只影响用户代码层 —— 正是要观测的那层。
vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>();
  return {
    ...actual,
    useState: (initial: never) => {
      const [state, setState] = actual.useState(initial);
      const wrapped = ((value: unknown) => {
        if (unmountProbe.recording) unmountProbe.writes.push(value);
        return setState(value as never);
      }) as unknown as typeof setState;
      return [state, wrapped] as ReturnType<typeof actual.useState>;
    },
  };
});

/** fake timers + advanceTimersByTimeAsync：与 use-cluster-poll.test.ts 同款调度推进。 */
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('useBoundedPoll 卸载纪律⑤（#1748 可观测化）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    unmountProbe.writes.length = 0;
    unmountProbe.recording = false;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('卸载 → 在飞请求被 abort（纪律⑤的机制）', async () => {
    // 永不兑现的 in-flight 请求：本用例只观测 abort，不观测兑现后的行为
    // （那由下一条用例负责）。
    const fetcher = vi.fn().mockImplementation(() => new Promise<{ v: number }>(() => undefined));
    const { unmount } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    const signal = fetcher.mock.calls[0][0] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await act(async () => {
      unmount();
    });
    expect(signal.aborted, '卸载时必须 abort 在飞请求').toBe(true);
  });

  it('卸载后陈旧响应兑现 → 零 setState 写入、零新轮询', async () => {
    let resolveLater: (v: { v: number }) => void = () => undefined;
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise<{ v: number }>((resolve) => {
          resolveLater = resolve;
        }),
    );
    const { unmount } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      unmount();
    });

    // 记录窗口：卸载之后。陈旧响应此刻才兑现。
    unmountProbe.recording = true;
    await act(async () => {
      resolveLater({ v: 42 });
    });
    await flush(30_000);

    expect(unmountProbe.writes, '卸载后陈旧响应不得写状态（setData/setError/setPollTick…）').toEqual([]);
    expect(fetcher, '卸载后不得再发起轮询').toHaveBeenCalledTimes(1);
  });

  it('卸载后 refresh 不再触发拉取（mountedRef 闸门）', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { result, unmount } = renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }));
    await flush(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      unmount();
    });
    // 卸载前捕获的 refresh 闭包仍然可用；mountedRef 闸门必须把它挡掉。
    unmountProbe.recording = true;
    act(() => result.current.refresh());
    await flush(30_000);

    expect(fetcher, '卸载后 refresh 不得再拉取').toHaveBeenCalledTimes(1);
    expect(unmountProbe.writes, '卸载后不得写状态').toEqual([]);
  });
});
