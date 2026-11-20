import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const fetchProjectArtifacts = vi.fn();
const fetchArtifactLineage = vi.fn();
const fetchDataUsage = vi.fn();
const planDataGc = vi.fn();
const listWorkspaceSnapshots = vi.fn();

vi.mock('@/lib/api/project-assets', () => ({
  fetchProjectArtifacts: (...args: unknown[]) => fetchProjectArtifacts(...args),
  fetchDataUsage: (...args: unknown[]) => fetchDataUsage(...args),
  planDataGc: (...args: unknown[]) => planDataGc(...args),
  listWorkspaceSnapshots: (...args: unknown[]) => listWorkspaceSnapshots(...args),
}));
vi.mock('@/lib/api/project', () => ({
  fetchArtifactLineage: (...args: unknown[]) => fetchArtifactLineage(...args),
}));

import { useDataGc, useProjectArtifacts, useProjectSnapshots } from './use-project-assets';

const PAGE = { items: [], total: 0, limit: 50, offset: 0, has_more: false };

beforeEach(() => {
  vi.clearAllMocks();
  fetchProjectArtifacts.mockResolvedValue(PAGE);
  listWorkspaceSnapshots.mockResolvedValue({ project_id: 'p1', count: 0, bounded: 50, items: [] });
});

describe('project asset hook races', () => {
  it('drops lineage stuck in loading when another artifact aborts it', async () => {
    fetchArtifactLineage.mockImplementation((_id: string, opts?: { signal?: AbortSignal }) => {
      if (_id === 'a1') {
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () => {
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
          });
        });
      }
      return Promise.resolve({ artifact_id: 'a2', parents: [], consumers: [{ id: 'c' }] });
    });
    const { result } = renderHook(() => useProjectArtifacts('p1'));
    await waitFor(() => expect(fetchProjectArtifacts).toHaveBeenCalled());
    let first: Promise<void> = Promise.resolve();
    await act(async () => {
      first = result.current.loadLineage('a1');
    });
    expect(result.current.lineage.a1).toBe('loading');
    await act(async () => {
      await result.current.loadLineage('a2');
      await first;
    });
    expect(result.current.lineage.a1).not.toBe('loading');
    expect(result.current.lineage.a2).not.toBe('loading');
    expect(result.current.lineage.a2).not.toBe('error');
  });

  it('usage and plan do not abort each other', async () => {
    let usageSignal: AbortSignal | undefined;
    fetchDataUsage.mockImplementation((_id: string, opts?: { signal?: AbortSignal }) => {
      usageSignal = opts?.signal;
      return new Promise(() => {});
    });
    planDataGc.mockResolvedValue({ project_id: 'p1', plan_fingerprint: 'fp', candidates: [] });
    const { result } = renderHook(() => useDataGc('p1'));
    await act(async () => {
      void result.current.loadUsage();
    });
    await act(async () => {
      await result.current.runPlan();
    });
    expect(usageSignal?.aborted).toBe(false);
    expect(result.current.plan).toMatchObject({ plan_fingerprint: 'fp' });
  });

  it('clears snapshot verifications when the project changes and ignores a stale list', async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    listWorkspaceSnapshots.mockImplementationOnce(() => new Promise((resolve) => {
      resolveFirst = resolve;
    }));
    const { result, rerender } = renderHook(
      ({ projectId }: { projectId: string }) => useProjectSnapshots(projectId, 'sess'),
      { initialProps: { projectId: 'p1' } },
    );
    await waitFor(() => expect(listWorkspaceSnapshots).toHaveBeenCalled());
    rerender({ projectId: '' });
    await act(async () => {
      resolveFirst({ project_id: 'p1', count: 1, bounded: 50, items: [{ snapshot_id: 's', artifacts: 0, layers: 0, project_id: 'p1', home: 'project' }] });
    });
    expect(result.current.snapshots).toEqual([]);
  });
});
