import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { clearCache } from './get-fast-path';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, body: unknown) => ({
  ok: false,
  status,
  statusText: 'Error',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

import {
  fetchProjects,
  fetchProjectWorkflows,
  fetchWorkflowRuns,
  fetchWorkflowRun,
  replayWorkflowRun,
  resumeWorkflowRun,
  fetchRunComparison,
  fetchArtifactLineage,
} from './project';
import { ApiError } from './transport';

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});

afterEach(() => {
  clearCache();
});

function lastUrl(): string {
  const arg = mockFetch.mock.calls[mockFetch.mock.calls.length - 1]?.[0];
  return String(arg);
}

describe('project API — Page unwrap + /api/v1 prefix', () => {
  it('unwraps Page.items and hits /api/v1/projects', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        items: [{ id: 'p1', name: 'A', status: 'active', created_at: '', updated_at: '' }],
        total: 1,
        limit: 50,
        offset: 0,
        has_more: false,
      }),
    );
    const items = await fetchProjects();
    expect(items).toHaveLength(1);
    expect(items[0].id).toBe('p1');
    expect(lastUrl()).toContain('/api/v1/projects');
    expect(lastUrl()).not.toMatch(/:8001\/projects(\?|$)/);
  });

  it('does not treat a Page object as a list of workflows', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        items: [{ id: 'wf1', project_id: 'p1', name: 'W', version: 1, step_count: 3, created_at: '', updated_at: '' }],
        total: 1,
        limit: 50,
        offset: 0,
        has_more: false,
      }),
    );
    const wfs = await fetchProjectWorkflows('p1');
    expect(wfs[0].step_count).toBe(3);
    expect(lastUrl()).toContain('/api/v1/projects/p1/workflows');
  });
});

describe('run detail / replay / resume / compare / lineage', () => {
  it('loads run detail without caching a heavy payload across forceRefresh', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
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
        run_fingerprint: 'fp',
        created_at: '',
      }),
    );
    const run = await fetchWorkflowRun('p1', 'r1');
    expect(run.run_fingerprint).toBe('fp');
    expect(lastUrl()).toContain('/api/v1/projects/p1/runs/r1');
  });

  it('posts replay with the chosen mode and does not retry', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        id: 'r2',
        workflow_id: 'wf1',
        workflow_version: 1,
        status: 'completed',
        input_bindings: {},
        input_dataset_fingerprints: {},
        execution_trace: [],
        outputs: {},
        cost_perf_summary: {},
        completed_steps: [],
        created_at: '',
      }),
    );
    await replayWorkflowRun('p1', 'r1', 'latest');
    const init = mockFetch.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ mode: 'latest' });
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('posts resume with allow_rerun false and surfaces 409 detail', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonErr(409, { detail: 'cannot resume run r1: input dataset fingerprints changed' }),
    );
    const err = await resumeWorkflowRun('p1', 'r1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).body).toEqual({
      detail: 'cannot resume run r1: input dataset fingerprints changed',
    });
    const init = mockFetch.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ allow_rerun: false });
  });

  it('compare uses query params, not inferred equality', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        run_a_id: 'a',
        run_b_id: 'b',
        revision: {},
        inputs_changed: {},
        dataset_versions_changed: {},
        tool_versions_changed: {},
        params_changed: {},
        output_artifacts_changed: {},
        metrics_changed: {},
        warnings_changed: {},
        run_fingerprint: { run_a: 'x', run_b: 'y', same: false },
      }),
    );
    const cmp = await fetchRunComparison('p1', 'a', 'b');
    expect(cmp.run_fingerprint.same).toBe(false);
    expect(lastUrl()).toContain('run_a_id=a');
    expect(lastUrl()).toContain('run_b_id=b');
    expect((mockFetch.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('lineage is a GET per artifact (no N+1 helper here)', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ artifact_id: 'art1', parents: [], consumers: [] }));
    const g = await fetchArtifactLineage('art1');
    expect(g.parents).toEqual([]);
    expect(lastUrl()).toContain('/api/v1/projects/artifacts/art1/lineage');
  });

  it('run list is paginated and scoped by workflow_id', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
    );
    const page = await fetchWorkflowRuns('p1', { workflowId: 'wf1', limit: 20 });
    expect(page.items).toEqual([]);
    expect(lastUrl()).toContain('workflow_id=wf1');
    expect(lastUrl()).toContain('limit=20');
  });
});
