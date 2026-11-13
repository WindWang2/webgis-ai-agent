import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('./transport', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { cloneArtifact, detachDataset, fetchProjectArtifacts } from './project-assets';
import { clearCache } from './get-fast-path';

beforeEach(() => {
  apiFetch.mockReset();
  clearCache();
});

describe('project asset path encoding and clone cache', () => {
  it('encodes datasetId', async () => {
    apiFetch.mockResolvedValueOnce({ status: 'ok', message: 'gone' });
    await detachDataset('p/1', 'ds/2');
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/v1/projects/p%2F1/datasets/ds%2F2',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('invalidates the artifact list cache after clone', async () => {
    apiFetch.mockImplementation(async (path: string) => {
      if (String(path).includes('/clone')) {
        return { status: 'ok', artifact_id: 'c', source_artifact_id: 'a' };
      }
      return { items: [{ id: 'a', project_id: 'p1', name: 'A', artifact_type: 'geojson', crs: null, created_at: '' }], total: 1, limit: 50, offset: 0, has_more: false };
    });
    await fetchProjectArtifacts('p1', { limit: 50, offset: 0 });
    await fetchProjectArtifacts('p1', { limit: 50, offset: 0 });
    const before = apiFetch.mock.calls.length;
    await cloneArtifact('a/1', 'p1');
    await fetchProjectArtifacts('p1', { limit: 50, offset: 0 });
    expect(apiFetch.mock.calls.length).toBeGreaterThan(before);
    expect(String(apiFetch.mock.calls.find((call) => String(call[0]).includes('/clone'))?.[0])).toContain(
      '/artifacts/a%2F1/clone',
    );
  });
});
