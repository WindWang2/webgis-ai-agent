import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/transport', () => ({
  apiFetch: apiFetchMock,
}));

import {
  PagedRevisionConflict,
  fetchCatalogFeaturePage,
  fetchFeaturePage,
  iterateAllFeatures,
} from './paged-features';

function pageResponse(ids: number[], opts: { next?: string | null; hasMore?: boolean; revision?: number | null } = {}) {
  return {
    type: 'FeatureCollection',
    features: ids.map((i) => ({ type: 'Feature', id: i, geometry: null, properties: { i } })),
    pagination: { next_cursor: opts.next ?? null, has_more: opts.hasMore ?? false, returned: ids.length },
    revision: opts.revision ?? 3,
    feature_count: 10_000,
    bbox_mode: null,
  };
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe('fetchFeaturePage', () => {
  it('builds the window query (session/limit/cursor/bbox/fields/v) and maps the envelope', async () => {
    apiFetchMock.mockResolvedValueOnce(pageResponse([1, 2], { next: 'c2', hasMore: true }));
    const page = await fetchFeaturePage({
      sessionId: 'session-aaaaaaaaaaaaaaaa',
      refId: 'ref-1',
      revision: 3,
      limit: 100,
      cursor: 'c1',
      bbox: [104, 30, 106, 32],
      fields: ['i', 'name'],
    });
    const url = apiFetchMock.mock.calls[0][0] as string;
    expect(url).toContain('/api/v1/layers/data/ref-1/features?');
    expect(url).toContain('session_id=session-aaaaaaaaaaaaaaaa');
    expect(url).toContain('limit=100');
    expect(url).toContain('cursor=c1');
    expect(url).toContain('bbox=104%2C30%2C106%2C32');
    expect(url).toContain('fields=i%2Cname');
    expect(url).toContain('v=3');
    expect(page.features).toHaveLength(2);
    expect(page.nextCursor).toBe('c2');
    expect(page.hasMore).toBe(true);
    expect(page.revision).toBe(3);
    expect(page.featureCount).toBe(10_000);
  });

  it('maps 409 revision conflict to PagedRevisionConflict with the current revision', async () => {
    const err = new Error('conflict') as Error & Record<string, unknown>;
    err.status = 409;
    err.body = { detail: { error: 'revision_conflict', current_revision: 7 } };
    apiFetchMock.mockRejectedValue(err);
    await expect(fetchFeaturePage({ sessionId: 's', refId: 'r' })).rejects.toBeInstanceOf(PagedRevisionConflict);
    try {
      await fetchFeaturePage({ sessionId: 's', refId: 'r' });
      expect.unreachable();
    } catch (e) {
      expect((e as PagedRevisionConflict).currentRevision).toBe(7);
    }
  });
});

describe('iterateAllFeatures', () => {
  it('pages through until the end, keeping order (no miss, no dup)', async () => {
    apiFetchMock
      .mockResolvedValueOnce(pageResponse([0, 1], { next: 'c2', hasMore: true }))
      .mockResolvedValueOnce(pageResponse([2, 3], { next: 'c3', hasMore: true }))
      .mockResolvedValueOnce(pageResponse([4], { hasMore: false }));
    const { features, truncatedByCap } = await iterateAllFeatures({
      sessionId: 's',
      refId: 'r',
      limit: 2,
    });
    expect(features.map((f) => f.id)).toEqual([0, 1, 2, 3, 4]);
    expect(truncatedByCap).toBe(false);
    expect(apiFetchMock).toHaveBeenCalledTimes(3);
    // 第三页携带上一页的 cursor
    expect(String(apiFetchMock.mock.calls[2][0])).toContain('cursor=c3');
  });

  it('stops at maxFeatures cap and marks truncatedByCap', async () => {
    apiFetchMock.mockImplementation(async (_url: string, opts?: { signal?: AbortSignal }) => {
      if (opts?.signal?.aborted) throw new Error('aborted');
      return pageResponse([9, 9], { next: 'c-next', hasMore: true });
    });
    const { features, truncatedByCap } = await iterateAllFeatures({
      sessionId: 's',
      refId: 'r',
      limit: 2,
      maxFeatures: 4,
    });
    expect(features).toHaveLength(4);
    expect(truncatedByCap).toBe(true);
  });

  it('stops checking after maxPages', async () => {
    apiFetchMock.mockImplementation(async () => pageResponse([8], { next: 'c', hasMore: true }));
    const { features, truncatedByCap } = await iterateAllFeatures({
      sessionId: 's',
      refId: 'r',
      maxPages: 3,
    });
    expect(features).toHaveLength(3);
    expect(truncatedByCap).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledTimes(3);
  });
});

describe('fetchCatalogFeaturePage', () => {
  it('passes keyset params through and maps the response', async () => {
    apiFetchMock.mockResolvedValueOnce({
      success: true,
      dataset_id: 'it',
      features: [{ type: 'Feature', id: 0 }],
      next_cursor: 'k2',
      has_more: true,
      fingerprint: 'fp-1',
    });
    const res = await fetchCatalogFeaturePage({ itemId: 'it', cursor: 'k1', orderBy: 'i ASC' });
    const url = apiFetchMock.mock.calls[0][0] as string;
    expect(url).toContain('/api/v1/data-fabric/catalog/it/features?');
    expect(url).toContain('cursor=k1');
    expect(url).toContain('order_by=i'); // URLSearchParams 把空格编码为 '+'
    expect(res.nextCursor).toBe('k2');
    expect(res.hasMore).toBe(true);
    expect(res.fingerprint).toBe('fp-1');
  });

  it('surfaces honest degradation (has_more=false) untouched', async () => {
    apiFetchMock.mockResolvedValueOnce({ features: [], next_cursor: null, has_more: false, fingerprint: null });
    const res = await fetchCatalogFeaturePage({ itemId: 'it' });
    expect(res.hasMore).toBe(false);
    expect(res.nextCursor).toBeNull();
  });
});
