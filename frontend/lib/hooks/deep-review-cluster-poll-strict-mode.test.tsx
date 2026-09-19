import { StrictMode, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBoundedPoll } from './use-cluster-poll';

/**
 * FE-08 回归：cleanup 把 mountedRef 置 false 后 StrictMode 重挂不复位，
 * 重挂后的 refresh / 轮询全被 `!mountedRef.current` 闸死。
 */
describe('useBoundedPoll StrictMode 重挂（FE-08）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('StrictMode 重挂后手动 refresh 仍能拉取', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <StrictMode>{children}</StrictMode>
    );
    const { result } = renderHook(
      () => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }),
      { wrapper },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = fetcher.mock.calls.length;

    act(() => result.current.refresh());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(fetcher.mock.calls.length).toBe(before + 1);
  });

  it('StrictMode 重挂后调度仍会继续轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <StrictMode>{children}</StrictMode>
    );
    renderHook(() => useBoundedPoll({ fetcher, pollIntervalMs: 3000 }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = fetcher.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(fetcher.mock.calls.length).toBe(before + 1);
  });
});
