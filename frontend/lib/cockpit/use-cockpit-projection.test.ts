/**
 * useCockpitProjection — revision 单调守卫 + 服务端节奏自适应。
 *
 * 与 use-cluster-poll.test.ts 同款写法：只 mock fetcher，fake timers +
 * advanceTimersByTimeAsync 完全确定地推进调度；可见性 API mock 掉。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  projectionRevision,
  useCockpitProjection,
} from './use-cockpit-projection';
import {
  MIN_COCKPIT_POLL_MS,
  MAX_COCKPIT_POLL_MS,
} from '@/lib/api/cockpit';

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('projectionRevision（纯函数守卫内核）', () => {
  it('比较 selector 值：缺失按 0', () => {
    expect(projectionRevision({ revision: 5 }, (d: { revision: number }) => d.revision)).toBe(5);
    expect(projectionRevision({}, (d: { revision: number }) => d.revision)).toBe(0);
  });

  it('回退 generated_at：无 selector 时用信封时间戳', () => {
    expect(projectionRevision({ generated_at: 123 })).toBe(123);
    expect(projectionRevision({})).toBe(0);
  });
});

describe('useCockpitProjection（hook 集成）', () => {
  let _visibilityHandlerRef: (() => void) | null;

  beforeEach(() => {
    vi.useFakeTimers();
    _visibilityHandlerRef = null;
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    });
    vi.spyOn(document, 'addEventListener').mockImplementation(((kind: string, fn: EventListener) => {
      if (kind === 'visibilitychange') _visibilityHandlerRef = fn as () => void;
    }) as typeof document.addEventListener);
    vi.spyOn(document, 'removeEventListener').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function makeEnvelope(revision: number, pollAfterMs: number | null = null) {
    return { revision, generated_at: revision * 1000, poll_after_ms: pollAfterMs };
  }

  it('先新后旧：迟到旧响应被丢弃，数据保持新版本', async () => {
    const seq = [makeEnvelope(5), makeEnvelope(3)];
    const fetcher = vi.fn(async () => seq.shift() ?? makeEnvelope(3));
    const { result } = renderHook(() =>
      useCockpitProjection(fetcher, {
        revisionOf: (d: { revision: number }) => d.revision,
        pollIntervalMs: 3000,
      }),
    );
    await flush(0);
    expect(result.current.data?.revision).toBe(5);
    // 下一轮轮询返回旧 revision → 守卫必须吞掉
    await flush(3000);
    expect(result.current.data?.revision).toBe(5);
    expect(result.current.data?.generated_at).toBe(5000);
  });

  it('同 revision 幂等应用（不丢数据）', async () => {
    const seq = [makeEnvelope(5), makeEnvelope(5)];
    const fetcher = vi.fn(async () => seq.shift() ?? makeEnvelope(5));
    const { result } = renderHook(() =>
      useCockpitProjection(fetcher, {
        revisionOf: (d: { revision: number }) => d.revision,
        pollIntervalMs: 3000,
      }),
    );
    await flush(0);
    expect(result.current.data?.revision).toBe(5);
    await flush(3000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data?.revision).toBe(5);
  });

  it('更高 revision 照常应用', async () => {
    const seq = [makeEnvelope(5), makeEnvelope(7)];
    const fetcher = vi.fn(async () => seq.shift() ?? makeEnvelope(7));
    const { result } = renderHook(() =>
      useCockpitProjection(fetcher, {
        revisionOf: (d: { revision: number }) => d.revision,
        pollIntervalMs: 3000,
      }),
    );
    await flush(0);
    await flush(3000);
    expect(result.current.data?.revision).toBe(7);
  });

  it('poll_after_ms 自适应：2000 钳到 MIN；60000 钳到 MAX', async () => {
    const seq = [makeEnvelope(1, 2000), makeEnvelope(2, 60000)];
    const fetcher = vi.fn(async () => seq.shift() ?? makeEnvelope(9, null));
    const { result } = renderHook(() =>
      useCockpitProjection(fetcher, {
        revisionOf: (d: { revision: number }) => d.revision,
        pollIntervalMs: 5000,
      }),
    );
    await flush(0);
    expect(result.current.appliedPollMs).toBe(MIN_COCKPIT_POLL_MS);
    // 第二轮（MIN=3000 后）返回 60000 → 钳到 MAX
    await flush(MIN_COCKPIT_POLL_MS);
    expect(result.current.appliedPollMs).toBe(MAX_COCKPIT_POLL_MS);
  });

  it('poll_after_ms 缺失 → 回退默认间隔', async () => {
    const fetcher = vi.fn(async () => makeEnvelope(1, null));
    const { result } = renderHook(() =>
      useCockpitProjection(fetcher, {
        revisionOf: (d: { revision: number }) => d.revision,
        pollIntervalMs: 5000,
      }),
    );
    await flush(0);
    expect(result.current.appliedPollMs).toBe(5000);
  });

  it('session 切换（resetKey 变化）：数据清空，切新 fetcher', async () => {
    const fetcherA = vi.fn(async () => makeEnvelope(1));
    const fetcherB = vi.fn(async () => makeEnvelope(2));
    const { result, rerender } = renderHook(
      ({ useB }: { useB: boolean }) =>
        useCockpitProjection(useB ? fetcherB : fetcherA, {
          resetKey: useB ? 's2' : 's1',
          revisionOf: (d: { revision: number }) => d.revision,
          pollIntervalMs: 3000,
        }),
      { initialProps: { useB: false } },
    );
    await flush(0);
    expect(result.current.data?.revision).toBe(1);
    rerender({ useB: true });
    await flush(0);
    expect(fetcherB).toHaveBeenCalledTimes(1);
    expect(result.current.data?.revision).toBe(2);
  });
});
