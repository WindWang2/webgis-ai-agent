/**
 * Templates API client — Page envelope handling (issue #464).
 *
 * The backend GET /api/v1/templates returns the Page envelope
 * {items, total, limit, offset, has_more} (app/api/routes/templates.py).
 * templatesApi.list must normalize BOTH the envelope and a legacy bare-array
 * response into a page object so gallery consumers can never receive a
 * non-array `items`.
 */
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

import { templatesApi } from './templates';

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});

afterEach(() => {
  clearCache();
});

const SUMMARY = { id: 'tmpl_a', kind: 'basemap', name: 'A', is_builtin: true, version: 1 };

describe('templatesApi.list — Page envelope (issue #464)', () => {
  it('normalizes the backend Page envelope into a page object', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ items: [SUMMARY], total: 120, limit: 50, offset: 0, has_more: true })
    );
    const res = await templatesApi.list();
    expect(res.items).toHaveLength(1);
    expect(res.items[0]?.id).toBe('tmpl_a');
    expect(res.total).toBe(120);
    expect(res.limit).toBe(50);
    expect(res.offset).toBe(0);
    expect(res.has_more).toBe(true);
  });

  it('tolerates a legacy bare-array response (defensive both-shapes handling)', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk([SUMMARY, { ...SUMMARY, id: 'tmpl_b' }]));
    const res = await templatesApi.list();
    expect(Array.isArray(res.items)).toBe(true);
    expect(res.items).toHaveLength(2);
    expect(res.total).toBe(2);
    expect(res.has_more).toBe(false);
  });

  it('guards a malformed envelope (items not an array) to empty items', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ items: 'nope', total: 5, has_more: true }));
    const res = await templatesApi.list();
    expect(res.items).toEqual([]);
    expect(res.total).toBe(5);
  });

  it('passes pagination params through to the query string', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ items: [], total: 0, limit: 50, offset: 50, has_more: false }));
    await templatesApi.list({ kind: 'basemap', q: 'dark', limit: 50, offset: 50 });
    const url = String(mockFetch.mock.calls[mockFetch.mock.calls.length - 1]?.[0]);
    expect(url).toContain('/api/v1/templates');
    expect(url).toContain('kind=basemap');
    expect(url).toContain('q=dark');
    expect(url).toContain('limit=50');
    expect(url).toContain('offset=50');
  });
});
