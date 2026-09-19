import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from '@testing-library/react';
import { useBoundedPoll, type UseBoundedPollResult } from './use-cluster-poll';

interface Payload {
  v: number;
}

function Harness({
  fetcher,
  onResult,
}: {
  fetcher: (signal: AbortSignal) => Promise<Payload>;
  onResult: (result: UseBoundedPollResult<Payload>) => void;
}) {
  onResult(useBoundedPoll<Payload>({ fetcher, pollIntervalMs: 3000 }));
  return null;
}

/**
 * FE-08 回归：cleanup 把 mountedRef 置 false 后，StrictMode 的 effect 重放
 * （mount → cleanup → mount）必须复位 mountedRef，否则重挂后的 refresh /
 * 轮询全被 `!mountedRef.current` 闸死。
 *
 * 用 createRoot 直挂：RTL render/renderHook 的同步 act 不会触发 React 19
 * 的 StrictMode effect 重放，直挂才可以。
 */
describe('useBoundedPoll StrictMode effect 重放（FE-08）', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  async function mountStrict(
    fetcher: (signal: AbortSignal) => Promise<Payload>,
  ): Promise<{ latest: () => UseBoundedPollResult<Payload> }> {
    let current: UseBoundedPollResult<Payload> | null = null;
    await act(async () => {
      root.render(
        <StrictMode>
          <Harness fetcher={fetcher} onResult={(r) => { current = r; }} />
        </StrictMode>,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    return { latest: () => current as UseBoundedPollResult<Payload> };
  }

  it('effect 重放后手动 refresh 仍能拉取', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    const { latest } = await mountStrict(fetcher);
    const before = fetcher.mock.calls.length;

    await act(async () => {
      latest().refresh();
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(fetcher.mock.calls.length).toBe(before + 1);
  });

  it('effect 重放后调度仍会继续轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue({ v: 1 });
    await mountStrict(fetcher);
    const before = fetcher.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(fetcher.mock.calls.length).toBe(before + 1);
  });
});
