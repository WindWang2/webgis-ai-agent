/**
 * 扩展市场 API 测试 — 裸 dict 契约 / 查询参数 / 404（市场未启用）透传。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getMarketPackage, getMarketPackageVersion, listMarketPackages } from './extensions';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, statusText: string, body: unknown) => ({
  ok: false,
  status,
  statusText,
  headers: { get: () => null },
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listMarketPackages', () => {
  it('裸 dict 响应原样返回 + 查询参数齐全', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        total: 1,
        offset: 0,
        limit: 50,
        items: [
          {
            id: 'proj.gpkg',
            title: 'GeoPackage 扩展',
            description: 'd',
            publisher: 'pub1',
            status: 'active',
            deprecation_note: '',
            latest_version: '1.2.0',
            versions: ['1.2.0', '1.1.0'],
            tags: ['format'],
          },
        ],
      }),
    );
    const res = await listMarketPackages({ q: 'gpkg', tag: 'format', limit: 50 });
    expect(res.total).toBe(1);
    expect(res.items[0]?.latest_version).toBe('1.2.0');
    const url = String(mockFetch.mock.calls[0][0]);
    expect(url).toContain('/api/v1/extensions/marketplace/packages?');
    expect(url).toContain('q=gpkg');
    expect(url).toContain('tag=format');
    expect(url).toContain('limit=50');
    expect(url).toContain('offset=0');
  });
});

describe('getMarketPackageVersion', () => {
  it('返回版本记录全字段（digest/permissions/sbom_digest/dependencies）', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        package_id: 'proj.gpkg',
        version: '1.2.0',
        digest: 'a'.repeat(64),
        size_bytes: 2048,
        publisher: 'pub1',
        key_id: 'k1',
        signature: {},
        fingerprint: 'b'.repeat(64),
        sbom_digest: 'c'.repeat(64),
        permissions: ['net.fetch'],
        api_version: '1.0.0',
        min_core_version: '0.1.0',
        dependencies: [{ id: 'lib.x', version: '2.0', optional: false }],
        yanked: false,
        created_at: '2026-09-01T00:00:00Z',
        download: '/api/v1/extensions/marketplace/packages/proj.gpkg/versions/1.2.0/download',
      }),
    );
    const res = await getMarketPackageVersion('proj.gpkg', '1.2.0');
    expect(res.permissions).toEqual(['net.fetch']);
    expect(res.dependencies).toHaveLength(1);
    expect(res.download).toContain('/download');
    const url = String(mockFetch.mock.calls[0][0]);
    expect(url).toContain('/packages/proj.gpkg/versions/1.2.0');
  });
});

describe('市场未启用（404 语义）', () => {
  it('列表 404 抛 ApiError(status=404) —— UI 据此渲染"未启用"空态', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(404, 'Not Found', { detail: 'extension marketplace is not configured' }));
    await expect(listMarketPackages()).rejects.toMatchObject({
      status: 404,
    });
    expect(mockFetch.mock.calls[0]![0]).toContain('/marketplace/packages');
  });
});

describe('getMarketPackage', () => {
  it('对 id 做 URL 编码', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        id: 'a b', title: 't', description: '', publisher: 'p', status: 'active',
        deprecation_note: '', latest_version: null, versions: [], tags: [],
      }),
    );
    await getMarketPackage('a b');
    expect(String(mockFetch.mock.calls[0][0])).toContain('/packages/a%20b');
  });
});
