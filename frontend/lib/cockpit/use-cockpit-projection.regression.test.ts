/**
 * Review-P1 回归：review/SPATIAL_AGENT_OPERATIONS_COCKPIT_REVIEW.md。
 *
 * P1-1：revision 守卫基线必须在 resetKey（mission/session）切换时一并失效
 * —— 旧 mission 的高 revision 绝不能把新 mission 的低 revision 响应误判为
 * "迟到旧投影"，否则详情面板在切换后永久显示上一个 mission 的数据。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCockpitProjection } from './use-cockpit-projection';

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('useCockpitProjection — P1-1 回归（守卫基线随 resetKey 失效）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    vi.spyOn(document, 'addEventListener').mockImplementation(() => undefined);
    vi.spyOn(document, 'removeEventListener').mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  interface Detail { mission_id: string; revision: number; generated_at: number }

  it('mission A(rev42) → B(rev3)：B 的低 revision 投影必须生效', async () => {
    const fetcherA = vi.fn(async (): Promise<Detail> => ({
      mission_id: 'A', revision: 42, generated_at: 42000,
    }));
    const fetcherB = vi.fn(async (): Promise<Detail> => ({
      mission_id: 'B', revision: 3, generated_at: 3000,
    }));
    const { result, rerender } = renderHook(
      ({ useB }: { useB: boolean }) =>
        useCockpitProjection(useB ? fetcherB : fetcherA, {
          resetKey: useB ? 'B' : 'A',
          revisionOf: (d: Detail) => d.revision,
          pollIntervalMs: 3000,
        }),
      { initialProps: { useB: false } },
    );
    await flush(0);
    expect(result.current.data?.mission_id).toBe('A');

    rerender({ useB: true });
    await flush(0);
    // 缺陷回归：守卫若不随 resetKey 失效，这里会拿到 A 的数据
    expect(fetcherB).toHaveBeenCalledTimes(1);
    expect(result.current.data?.mission_id).toBe('B');
    expect(result.current.data?.revision).toBe(3);

    // B 上更高的 revision 照常应用
    (fetcherB as ReturnType<typeof vi.fn>).mockImplementation(async (): Promise<Detail> => ({
      mission_id: 'B', revision: 4, generated_at: 4000,
    }));
    await flush(3000);
    expect(result.current.data?.revision).toBe(4);
  });

  it('session 视图同理：generated_at 回退基线也随 resetKey 重置', async () => {
    const fetcherS1 = vi.fn(async () => ({ generated_at: 99999, events: [1] }));
    const fetcherS2 = vi.fn(async () => ({ generated_at: 5, events: [2] }));
    const { result, rerender } = renderHook(
      ({ useTwo }: { useTwo: boolean }) =>
        useCockpitProjection(useTwo ? fetcherS2 : fetcherS1, {
          resetKey: useTwo ? 's2' : 's1',
          pollIntervalMs: 3000,
        }),
      { initialProps: { useTwo: false } },
    );
    await flush(0);
    expect(result.current.data?.events).toEqual([1]);
    rerender({ useTwo: true });
    await flush(0);
    expect(result.current.data?.events).toEqual([2]);
  });
});
