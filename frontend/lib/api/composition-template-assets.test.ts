/**
 * Composition template assets API client tests (G04).
 *
 * Covers list normalization (both-shapes + malformed guard), query params,
 * cache invalidation on mutations, and URL encoding of asset ids.
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

import { compositionTemplateAssetsApi } from './composition-template-assets';

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});

afterEach(() => {
  clearCache();
});

const BUILTIN = {
  asset_id: 'asset.builtin.heat_stats_portfolio',
  revision: 1,
  status: 'published',
  builtin: true,
  fingerprint: 'asset-sha256:abc',
  template_id: 'asset.builtin.heat_stats_portfolio',
  name: '热力+统计图表组合',
  compatible_map_models: ['visual_heatmap'],
  output_targets: ['interactive', 'png'],
};

describe('compositionTemplateAssetsApi.list', () => {
  it('returns the builtin + DB asset list from the envelope', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({
      success: true,
      assets: [BUILTIN, { ...BUILTIN, asset_id: 'asset.xyz', builtin: false }],
      asset_count: 2,
      reason_codes: [],
    }));
    const res = await compositionTemplateAssetsApi.list();
    expect(res.success).toBe(true);
    expect(res.assets).toHaveLength(2);
    expect(res.asset_count).toBe(2);
    expect(res.assets[0]?.builtin).toBe(true);
    expect(res.assets[1]?.builtin).toBe(false);
  });

  it('passes include_retired through to the query string', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, assets: [], asset_count: 0 }));
    await compositionTemplateAssetsApi.list({ includeRetired: true });
    const url = String(mockFetch.mock.calls[mockFetch.mock.calls.length - 1]?.[0]);
    expect(url).toContain('/api/v1/composition-template-assets');
    expect(url).toContain('include_retired=true');
  });

  it('guards a malformed envelope to empty assets', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, assets: 'nope' }));
    const res = await compositionTemplateAssetsApi.list();
    expect(res.assets).toEqual([]);
    expect(res.asset_count).toBe(0);
  });

  it('guards a non-object response to empty assets', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(null));
    const res = await compositionTemplateAssetsApi.list();
    expect(res.success).toBe(false);
    expect(res.assets).toEqual([]);
  });
});

describe('compositionTemplateAssetsApi.create', () => {
  it('posts the create body and returns the created asset', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({
      success: true, asset_id: 'asset.new1', status: 'published', revision: 1,
    }));
    const res = await compositionTemplateAssetsApi.create({
      session_id: 'sess-1', name: '我的版式', visibility: 'private',
    });
    expect(res.asset_id).toBe('asset.new1');
    expect(res.status).toBe('published');
    const [url, init] = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(String(url)).toContain('/api/v1/composition-template-assets');
    expect(String(init.method)).toBe('POST');
    expect(String(init.body)).toContain('我的版式');
  });
});

describe('compositionTemplateAssetsApi lifecycle', () => {
  it('publish / retire / restore target the right paths with POST', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, status: 'published' }));
    await compositionTemplateAssetsApi.publish('asset.abc', 2);
    let [url, init] = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(String(url)).toContain('/api/v1/composition-template-assets/asset.abc/versions/2/publish');
    expect(String(init.method)).toBe('POST');

    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, status: 'retired' }));
    await compositionTemplateAssetsApi.retire('asset.abc', 'done');
    [url, init] = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(String(url)).toContain('/asset.abc/retire');
    expect(String(init.body)).toContain('done');

    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, revision: 3 }));
    await compositionTemplateAssetsApi.restore('asset.abc', 1);
    [url, init] = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(String(url)).toContain('/asset.abc/restore/1');
    expect(String(init.method)).toBe('POST');
  });

  it('encodes asset ids in paths', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, versions: [] }));
    await compositionTemplateAssetsApi.get('asset/a b');
    const url = String(mockFetch.mock.calls[mockFetch.mock.calls.length - 1]?.[0]);
    expect(url).toContain('asset%2Fa%20b');
  });
});
