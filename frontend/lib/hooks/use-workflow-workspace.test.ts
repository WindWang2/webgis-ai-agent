import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchProjects: vi.fn(),
  fetchProjectDatasets: vi.fn(),
  fetchProjectWorkflows: vi.fn(),
  fetchWorkflowRuns: vi.fn(),
  fetchWorkflowRevisions: vi.fn(),
  fetchWorkflowRun: vi.fn(),
  fetchArtifactLineage: vi.fn(),
  fetchRunComparison: vi.fn(),
  replayWorkflowRun: vi.fn(),
  resumeWorkflowRun: vi.fn(),
  runWorkflow: vi.fn(),
  createProject: vi.fn(),
  invalidateProjectRunCaches: vi.fn(),
}));

vi.mock('@/lib/api/project', () => api);

import { ApiError } from '@/lib/api/transport';
import { RUN_POLL_INTERVAL_MS, useWorkflowWorkspace } from './use-workflow-workspace';

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

const project = {
  id: 'p1',
  name: 'P',
  status: 'active',
  created_at: '',
  updated_at: '',
};
const workflow = {
  id: 'wf1',
  project_id: 'p1',
  name: 'W',
  version: 1,
  step_count: 2,
  created_at: '',
  updated_at: '',
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: 'r1',
    workflow_id: 'wf1',
    workflow_version: 1,
    status: 'completed',
    input_bindings: {},
    input_dataset_fingerprints: {},
    execution_trace: [],
    outputs: {},
    cost_perf_summary: {},
    completed_steps: ['s1'],
    created_at: '',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchProjects.mockResolvedValue([project]);
  api.fetchProjectDatasets.mockResolvedValue([]);
  api.fetchProjectWorkflows.mockResolvedValue([workflow]);
  api.fetchWorkflowRuns.mockResolvedValue(page([{ id: 'r1', workflow_id: 'wf1', workflow_version: 1, status: 'completed', created_at: '' }]));
  api.fetchWorkflowRevisions.mockResolvedValue(page([]));
  api.fetchWorkflowRun.mockResolvedValue(detail());
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useWorkflowWorkspace — request discipline', () => {
  it('does not fetch runs until a workflow is opened', async () => {
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    await waitFor(() => expect(api.fetchProjectWorkflows).toHaveBeenCalled());
    expect(api.fetchWorkflowRuns).not.toHaveBeenCalled();
    expect(api.fetchWorkflowRun).not.toHaveBeenCalled();
    expect(api.fetchArtifactLineage).not.toHaveBeenCalled();
  });

  it('drops a stale project-detail response after switch', async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    api.fetchProjectDatasets.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    api.fetchProjectWorkflows.mockImplementationOnce(() => new Promise(() => {}));

    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.selectedProjectId).toBe('p1'));

    api.fetchProjects.mockResolvedValue([
      project,
      { id: 'p2', name: 'P2', status: 'active', created_at: '', updated_at: '' },
    ]);
    await act(async () => {
      result.current.selectProject('p2');
    });
    await waitFor(() => expect(api.fetchProjectDatasets).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveFirst([{ id: 'stale-ds', project_id: 'p1', name: 'STALE', source_type: 'x', crs: null, quality_status: 'ok', created_at: '' }]);
    });
    expect(result.current.datasets.find((d) => d.name === 'STALE')).toBeUndefined();
  });

  it('does not fetch lineage until requested', async () => {
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await waitFor(() => expect(api.fetchWorkflowRuns).toHaveBeenCalledTimes(1));
    await act(async () => {
      result.current.openRun('r1');
    });
    await waitFor(() => expect(result.current.runDetail?.id).toBe('r1'));
    expect(api.fetchArtifactLineage).not.toHaveBeenCalled();
    api.fetchArtifactLineage.mockResolvedValue({ artifact_id: 'a1', parents: [], consumers: [] });
    await act(async () => {
      await result.current.loadLineage('a1');
    });
    expect(api.fetchArtifactLineage).toHaveBeenCalledTimes(1);
  });
});

describe('useWorkflowWorkspace — recoverability truth', () => {
  it('locks a second replay while the first POST is in flight', async () => {
    let resolveReplay: (v: unknown) => void = () => {};
    api.replayWorkflowRun.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReplay = resolve;
        }),
    );
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await waitFor(() => expect(result.current.runDetail).not.toBeNull());

    let first: Promise<unknown> | undefined;
    let second: Promise<unknown> | undefined;
    await act(async () => {
      first = result.current.replay('exact');
      second = result.current.replay('exact');
    });
    expect(api.replayWorkflowRun).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveReplay(detail({ id: 'r2', status: 'completed' }));
      await first;
      await second;
    });
    expect(api.replayWorkflowRun).toHaveBeenCalledTimes(1);
    expect(await first).toMatchObject({ ok: true, run: { id: 'r2' }, applied: true });
    expect(await second).toEqual({ ok: false, error: null });
  });

  it('records a 409 resume as actionError and does not pretend success', async () => {
    api.resumeWorkflowRun.mockRejectedValueOnce(
      new ApiError(409, 'Conflict', { detail: 'cannot resume run r1: prior step outputs are no longer reconstructable' }),
    );
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await waitFor(() => expect(result.current.runDetail).not.toBeNull());

    let out: unknown;
    await act(async () => {
      out = await result.current.resume();
    });
    expect(out).toMatchObject({ ok: false, error: expect.stringContaining('no longer reconstructable') });
    expect(result.current.actionError).toContain('no longer reconstructable');
    expect(result.current.runDetail?.id).toBe('r1');
  });

  it('does not paint a late replay onto a run the user selected mid-flight', async () => {
    let resolveReplay: (v: unknown) => void = () => {};
    api.replayWorkflowRun.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReplay = resolve;
        }),
    );
    api.fetchWorkflowRun.mockImplementation(async (_p: string, runId: string) =>
      detail({ id: runId, status: 'completed' }),
    );
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await waitFor(() => expect(result.current.runDetail?.id).toBe('r1'));

    let pending: Promise<unknown> | undefined;
    await act(async () => {
      pending = result.current.replay('exact');
    });
    await act(async () => {
      result.current.openRun('run0');
    });
    await waitFor(() => expect(result.current.selectedRunId).toBe('run0'));
    await act(async () => {
      resolveReplay(detail({ id: 'r-late', status: 'failed', error_message: 'boom' }));
      await pending;
    });
    expect(result.current.runDetail?.id).toBe('run0');
    expect(result.current.runDetail?.status).toBe('completed');
    expect(await pending).toMatchObject({ ok: true, applied: false });
  });

  it('on timeout invalidates caches and does not report success', async () => {
    const { ApiTimeoutError } = await import('@/lib/api/transport');
    api.replayWorkflowRun.mockRejectedValueOnce(new ApiTimeoutError(120000));
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await waitFor(() => expect(result.current.runDetail).not.toBeNull());
    let out: unknown;
    await act(async () => {
      out = await result.current.replay('exact');
    });
    expect(out).toMatchObject({ ok: false, error: expect.stringContaining('超时') });
    expect(api.invalidateProjectRunCaches).toHaveBeenCalledWith('p1');
    expect(api.fetchWorkflowRuns).toHaveBeenCalled();
    expect(result.current.runDetail?.id).toBe('r1');
  });
});

describe('useWorkflowWorkspace — bounded poll', () => {
  it('polls a running run once per interval and stops when completed', async () => {
    vi.useFakeTimers();
    api.fetchWorkflowRun
      .mockResolvedValueOnce(detail({ status: 'running' }))
      .mockResolvedValueOnce(detail({ status: 'completed' }));

    const { result } = renderHook(() => useWorkflowWorkspace());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(2);
  });

  it('#389: keeps polling while status stays running (self-rescheduling tick)', async () => {
    vi.useFakeTimers();
    // The bug: the poll effect keyed on runDetail?.status never re-ran while
    // the status stayed 'running' — exactly one poll, then stuck forever.
    api.fetchWorkflowRun.mockResolvedValue(detail({ status: 'running' }));

    const { result } = renderHook(() => useWorkflowWorkspace());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(1);

    for (let i = 2; i <= 4; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
      });
      expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(i);
    }
  });
});

describe('useWorkflowWorkspace — hidden-tab polling resume (#549)', () => {
  let hidden = false;

  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    api.fetchWorkflowRun.mockResolvedValue(detail({ status: 'running' }));
  });

  it('resumes polling on visibilitychange after a hidden-tab poll stalled the scheduler', async () => {
    const { result } = renderHook(() => useWorkflowWorkspace());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(1);

    // Tab 隐藏：已排好的下一次轮询仍会完成一次（在飞定时器），但随后因
    // document.hidden 提前返回而不排下一次 —— 调度器停摆，不会继续打后端。
    hidden = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS * 5);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(2); // 停摆：隐藏期间 0 轮询

    // 重新可见：visibilitychange → 立即补拉（finally bump pollTick 重启调度）。
    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(3);

    // 恢复后按原间隔继续轮询（不会一次打爆后端）。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    });
    expect(api.fetchWorkflowRun).toHaveBeenCalledTimes(4);
  });

  it('does not resume on visibility when the run is no longer active', async () => {
    api.fetchWorkflowRun.mockResolvedValue(detail({ status: 'completed' }));
    const { result } = renderHook(() => useWorkflowWorkspace());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = api.fetchWorkflowRun.mock.calls.length;

    hidden = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    });
    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.fetchWorkflowRun.mock.calls.length).toBe(before);
  });
});

describe('useWorkflowWorkspace — compare binding', () => {
  it('clears a previous compare verdict when the peer changes', async () => {
    api.fetchRunComparison.mockResolvedValue({
      run_a_id: 'r1',
      run_b_id: 'run0',
      revision: {},
      inputs_changed: {},
      dataset_versions_changed: {},
      tool_versions_changed: {},
      params_changed: {},
      output_artifacts_changed: {},
      metrics_changed: {},
      warnings_changed: {},
      run_fingerprint: { run_a: 'x', run_b: 'x', same: true },
    });
    const { result } = renderHook(() => useWorkflowWorkspace());
    await waitFor(() => expect(result.current.workflows).toHaveLength(1));
    await act(async () => {
      result.current.openWorkflow('wf1');
    });
    await act(async () => {
      result.current.openRun('r1');
    });
    await act(async () => {
      result.current.setComparePeerId('run0');
    });
    await act(async () => {
      await result.current.openCompare();
    });
    expect(result.current.compare?.run_fingerprint.same).toBe(true);
    await act(async () => {
      result.current.setComparePeerId('other');
    });
    expect(result.current.compare).toBeNull();
  });
});
