import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import type { JobListResponse, JobView } from '@/lib/api/jobs';
import { useJobCenter } from './use-job-center';

// ── Mocks ────────────────────────────────────────────────────────────────
// 只 mock API 层：轮询调度、陈旧响应保护、取消 UX 都是被测逻辑本身。
const api = vi.hoisted(() => ({
  listJobs: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
}));

vi.mock('@/lib/api/jobs', () => ({
  listJobs: api.listJobs,
  cancelJob: api.cancelJob,
  retryJob: api.retryJob,
}));

// ── Fixtures ─────────────────────────────────────────────────────────────

function job(overrides: Partial<JobView> = {}): JobView {
  return {
    id: 'job-1',
    kind: 'analysis',
    name: 'NDVI 分析',
    status: 'running',
    progress: 40,
    message: '计算中',
    cancellable: true,
    retryable: false,
    active: true,
    attempt: 1,
    session_id: 'sess-a',
    project_id: null,
    agent_task_id: null,
    agent_step_id: null,
    background_job_ids: [],
    error: null,
    result_ref: null,
    step_count: 0,
    created_at: '2026-08-12T00:00:00Z',
    started_at: '2026-08-12T00:00:01Z',
    finished_at: null,
    cancel_requested_at: null,
    ...overrides,
  };
}

function listResponse(jobs: JobView[]): JobListResponse {
  const hasActive = jobs.some((j) => j.active);
  return { jobs, has_active: hasActive, poll_after_ms: hasActive ? 3000 : null };
}

// fake timers 下 testing-library 的 waitFor 不会推进定时器 —— 用显式 flush
// 冲刷 microtask + 定时器队列，测试因此完全确定，不依赖真实时间。
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    // hook 的一次拉取跨多个 await（请求 + setState + 排下一次轮询），
    // 多冲刷几轮 microtask 队列让状态完全落定。
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

let hidden = false;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  hidden = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  api.listJobs.mockResolvedValue(listResponse([]));
  api.cancelJob.mockResolvedValue({
    id: 'job-1',
    status: 'cancelling',
    cancel_requested: true,
    cancelling: true,
  });
  api.retryJob.mockResolvedValue({
    id: 'job-1',
    status: 'queued',
    retried: true,
    reason: 'requeued',
    attempt: 2,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

// ── 加载 / 恢复 ──────────────────────────────────────────────────────────

describe('useJobCenter — 恢复与加载', () => {
  it('挂载时拉取一次（浏览器刷新后恢复任务中心）', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));

    await flush();
    expect(result.current.jobs).toHaveLength(1);
    expect(api.listJobs).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'sess-a' }),
    );
    expect(result.current.hasActive).toBe(true);
  });

  it('没有 session 时不发请求（无归属证明）', async () => {
    renderHook(() => useJobCenter({ sessionId: null }));
    await flush();
    expect(api.listJobs).not.toHaveBeenCalled();
  });

  it('enabled=false 时不发请求（面板收起不打后端）', async () => {
    renderHook(() => useJobCenter({ sessionId: 'sess-a', enabled: false }));
    await flush();
    expect(api.listJobs).not.toHaveBeenCalled();
  });

  it('暴露请求错误', async () => {
    api.listJobs.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.error).toBe('boom');
  });
});

// ── 轮询生命周期（规范 §32） ─────────────────────────────────────────────

describe('useJobCenter — 轮询边界', () => {
  it('无活跃 job → 0 轮询', async () => {
    api.listJobs.mockResolvedValue(listResponse([job({ status: 'completed', active: false })]));
    renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(api.listJobs).toHaveBeenCalledTimes(1);

    await flush(30_000);
    expect(api.listJobs).toHaveBeenCalledTimes(1);
  });

  it('有活跃 job → 按间隔继续轮询', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    renderHook(() => useJobCenter({ sessionId: 'sess-a', pollIntervalMs: 1000 }));
    await flush();
    expect(api.listJobs).toHaveBeenCalledTimes(1);

    await flush(3500);
    expect(api.listJobs.mock.calls.length).toBeGreaterThan(1);
  });

  it('tab 隐藏时暂停轮询，重新可见时立刻补一次', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    renderHook(() => useJobCenter({ sessionId: 'sess-a', pollIntervalMs: 1000 }));
    await flush();
    expect(api.listJobs).toHaveBeenCalledTimes(1);

    hidden = true;
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const pausedAt = api.listJobs.mock.calls.length;
    await flush(10_000);
    expect(api.listJobs.mock.calls.length).toBe(pausedAt);

    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.listJobs.mock.calls.length).toBeGreaterThan(pausedAt);
  });

  it('连续失败达到上限后停止轮询（有界重试）', async () => {
    api.listJobs.mockRejectedValue(new Error('down'));
    renderHook(() => useJobCenter({ sessionId: 'sess-a', pollIntervalMs: 10 }));

    await flush(5_000);
    // 最多 MAX_CONSECUTIVE_ERRORS 次后不再排下一次
    expect(api.listJobs.mock.calls.length).toBeLessThanOrEqual(4);
  });

  it('卸载时 abort 在飞请求并停止定时器', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    const { unmount } = renderHook(() =>
      useJobCenter({ sessionId: 'sess-a', pollIntervalMs: 100 }),
    );
    await flush();
    expect(api.listJobs).toHaveBeenCalledTimes(1);

    const signal = api.listJobs.mock.calls[0][0].signal as AbortSignal;
    await act(async () => {
      unmount();
    });
    expect(signal.aborted).toBe(true);

    const afterUnmount = api.listJobs.mock.calls.length;
    await flush(5_000);
    expect(api.listJobs.mock.calls.length).toBe(afterUnmount);
  });
});

// ── 会话切换污染防护（Scenario 10） ──────────────────────────────────────

describe('useJobCenter — 会话切换', () => {
  it('切换 session 立刻清空旧任务', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useJobCenter({ sessionId: sid }),
      { initialProps: { sid: 'sess-a' } },
    );
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    api.listJobs.mockResolvedValue(listResponse([]));
    await act(async () => {
      rerender({ sid: 'sess-b' });
    });
    expect(result.current.jobs).toHaveLength(0);
  });

  it('旧 session 的迟到响应不得写进新 session 的 UI', async () => {
    // 第一次请求慢，切换 session 后才 resolve
    let resolveOld: (v: JobListResponse) => void = () => {};
    api.listJobs.mockImplementationOnce(
      () => new Promise<JobListResponse>((res) => { resolveOld = res; }),
    );

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useJobCenter({ sessionId: sid }),
      { initialProps: { sid: 'sess-a' } },
    );
    await flush();

    api.listJobs.mockResolvedValue(listResponse([]));
    await act(async () => {
      rerender({ sid: 'sess-b' });
    });

    await act(async () => {
      resolveOld(listResponse([job({ id: 'stale-job', session_id: 'sess-a' })]));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.jobs.find((j) => j.id === 'stale-job')).toBeUndefined();
  });

  it('切换 session 时 abort 旧请求', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    const { rerender } = renderHook(
      ({ sid }: { sid: string }) => useJobCenter({ sessionId: sid }),
      { initialProps: { sid: 'sess-a' } },
    );
    await flush();
    expect(api.listJobs).toHaveBeenCalled();
    const signal = api.listJobs.mock.calls[0][0].signal as AbortSignal;

    await act(async () => {
      rerender({ sid: 'sess-b' });
    });
    expect(signal.aborted).toBe(true);
  });
});

// ── 取消 UX（规范 §30） ──────────────────────────────────────────────────

describe('useJobCenter — 取消', () => {
  it('取消后立即进入「取消中」，而不是直接显示已取消', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    await act(async () => {
      await result.current.cancel('job-1');
    });

    expect(api.cancelJob).toHaveBeenCalledWith('job-1', expect.anything());
    expect(result.current.cancelling.has('job-1')).toBe(true);
    // 后端仍是 cancelling → UI 不得宣称已取消
    expect(result.current.jobs[0].status).not.toBe('cancelled');
  });

  it('后端确认终态后退出「取消中」', async () => {
    api.listJobs.mockResolvedValueOnce(listResponse([job()]));
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    api.listJobs.mockResolvedValue(
      listResponse([job({ status: 'cancelled', active: false, cancellable: false })]),
    );
    await act(async () => {
      await result.current.cancel('job-1');
    });

    await flush();
    expect(result.current.cancelling.has('job-1')).toBe(false);
    expect(result.current.jobs[0].status).toBe('cancelled');
  });

  it('取消失败时回滚乐观状态并暴露错误', async () => {
    api.listJobs.mockResolvedValue(listResponse([job()]));
    api.cancelJob.mockRejectedValue(new Error('cancel failed'));
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    await act(async () => {
      await result.current.cancel('job-1');
    });

    expect(result.current.cancelling.has('job-1')).toBe(false);
    expect(result.current.error).toBe('cancel failed');
    expect(result.current.jobs[0].status).toBe('running');
  });

  it('重复取消是幂等的（后端 cancel_requested=false 不算错误）', async () => {
    api.listJobs.mockResolvedValue(listResponse([job({ status: 'cancelling' })]));
    api.cancelJob.mockResolvedValue({
      id: 'job-1',
      status: 'cancelling',
      cancel_requested: false,
      cancelling: true,
    });
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    await act(async () => {
      await result.current.cancel('job-1');
      await result.current.cancel('job-1');
    });
    expect(result.current.error).toBeNull();
  });
});

// ── 重试 ────────────────────────────────────────────────────────────────

describe('useJobCenter — 重试', () => {
  it('重试成功后刷新列表', async () => {
    api.listJobs.mockResolvedValue(
      listResponse([job({ status: 'failed', active: false, retryable: true })]),
    );
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    await act(async () => {
      await result.current.retry('job-1');
    });
    expect(api.retryJob).toHaveBeenCalledWith('job-1', expect.anything());
    expect(result.current.error).toBeNull();
  });

  it('后端拒绝重试时展示原因（取消不得重试）', async () => {
    api.listJobs.mockResolvedValue(
      listResponse([job({ status: 'cancelled', active: false })]),
    );
    api.retryJob.mockResolvedValue({
      id: 'job-1',
      status: 'cancelled',
      retried: false,
      reason: 'cancelled_jobs_are_never_retried',
      attempt: 1,
    });
    const { result } = renderHook(() => useJobCenter({ sessionId: 'sess-a' }));
    await flush();
    expect(result.current.jobs).toHaveLength(1);

    await act(async () => {
      await result.current.retry('job-1');
    });
    expect(result.current.error).toContain('cancelled_jobs_are_never_retried');
  });
});
