import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchProjectDatasetPage: vi.fn(),
  attachDataset: vi.fn(),
  detachDataset: vi.fn(),
}));

vi.mock('@/lib/api/project-assets', () => api);
vi.mock('@/lib/api/project', () => ({ fetchArtifactLineage: vi.fn() }));

import { useProjectDatasets } from './use-project-assets';

const PAGE = { items: [], total: 0, limit: 50, offset: 0, has_more: false };

function attached(id: string, schema: Record<string, unknown> = { a: 'string' }) {
  return {
    id,
    project_id: 'p1',
    name: `D-${id}`,
    source_type: 'upload',
    schema_profile: schema,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchProjectDatasetPage.mockResolvedValue(PAGE);
});

describe('useProjectDatasets attach total', () => {
  it('does not bump total when attach returns an already-listed dataset', async () => {
    api.attachDataset.mockResolvedValue(attached('d1'));
    const { result } = renderHook(() => useProjectDatasets('p1'));
    await waitFor(() => expect(api.fetchProjectDatasetPage).toHaveBeenCalled());

    await act(async () => { await result.current.attach({} as never); });
    expect(result.current.datasets).toHaveLength(1);
    expect(result.current.total).toBe(1);

    // 重附同一 dataset：数组去重，total 不得再 +1。
    await act(async () => { await result.current.attach({} as never); });
    expect(result.current.datasets).toHaveLength(1);
    expect(result.current.total).toBe(1);
  });
});

describe('useProjectDatasets schemaByDataset lifecycle', () => {
  it('clears captured schema profiles when the project changes', async () => {
    api.attachDataset.mockResolvedValue(attached('d1'));
    const { result, rerender } = renderHook(
      ({ projectId }: { projectId: string }) => useProjectDatasets(projectId),
      { initialProps: { projectId: 'p1' } },
    );
    await waitFor(() => expect(api.fetchProjectDatasetPage).toHaveBeenCalledTimes(1));

    await act(async () => { await result.current.attach({} as never); });
    expect(result.current.schemaByDataset.d1).toEqual({ a: 'string' });

    rerender({ projectId: 'p2' });
    await waitFor(() => expect(api.fetchProjectDatasetPage).toHaveBeenCalledTimes(2));
    expect(result.current.schemaByDataset).toEqual({});
  });
});
