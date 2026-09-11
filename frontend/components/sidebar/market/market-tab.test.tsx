/**
 * MarketTab 测试 — 列表/搜索/详情导航 + 诚实空态（404=市场未启用）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MarketTab } from './market-tab';

vi.mock('@/lib/api/authenticated-download', () => ({
  downloadWithAuth: vi.fn().mockResolvedValue(undefined),
}));

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
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

const summary = {
  id: 'proj.gpkg',
  title: 'GeoPackage 扩展',
  description: '读写 GeoPackage 数据源',
  publisher: 'pub1',
  status: 'active',
  deprecation_note: '',
  latest_version: '1.2.0',
  versions: ['1.2.0', '1.1.0'],
  tags: ['format'],
};

function route(response: () => ReturnType<typeof jsonOk | typeof jsonErr>) {
  mockFetch.mockImplementation(() => Promise.resolve(response()));
}

beforeEach(() => {
  vi.clearAllMocks();
  route(() => jsonOk({ total: 0, offset: 0, limit: 50, items: [] }));
});

describe('MarketTab', () => {
  it('空市场是诚实空态（不出现假数据）', async () => {
    render(<MarketTab />);
    expect(await screen.findByText('市场暂无扩展包')).toBeInTheDocument();
  });

  it('市场未启用（404）渲染"未启用"空态而非错误', async () => {
    route(() => jsonErr(404, { detail: 'extension marketplace is not configured' }));
    render(<MarketTab />);
    expect(await screen.findByText('扩展市场未启用')).toBeInTheDocument();
    expect(screen.getByText(/EXTENSION_REGISTRY_DIR/)).toBeInTheDocument();
  });

  it('列表渲染包卡片 → 点击进详情 → 版本/权限/依赖与诚实安装说明', async () => {
    const user = userEvent.setup();
    route(() =>
      mockFetch.mock.calls.length <= 1
        ? jsonOk({ total: 1, offset: 0, limit: 50, items: [summary] })
        : jsonOk({
            ...summary,
            versions: summary.versions,
          }),
    );
    // 更直接：按 URL 路由
    mockFetch.mockImplementation((url: string | URL) => {
      const u = String(url);
      if (u.includes('/versions/1.2.0') && !u.endsWith('/download')) {
        return Promise.resolve(
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
            permissions: ['net.fetch', 'fs.read'],
            api_version: '1.0.0',
            min_core_version: '0.1.0',
            dependencies: [{ id: 'lib.fiona', version: '1.9', optional: false }],
            yanked: false,
            created_at: '2026-09-01T00:00:00Z',
            download: '/api/v1/extensions/marketplace/packages/proj.gpkg/versions/1.2.0/download',
          }),
        );
      }
      if (u.includes('/packages/proj.gpkg')) {
        return Promise.resolve(jsonOk(summary));
      }
      return Promise.resolve(jsonOk({ total: 1, offset: 0, limit: 50, items: [summary] }));
    });

    render(<MarketTab />);
    await user.click(await screen.findByRole('button', { name: /GeoPackage 扩展/ }));

    expect(await screen.findByText('权限声明')).toBeInTheDocument();
    expect(screen.getByText('net.fetch')).toBeInTheDocument();
    expect(screen.getByText(/依赖（1 项）/)).toBeInTheDocument();
    expect(screen.getByText(/lib\.fiona/)).toBeInTheDocument();
    // 诚实安装说明与「无认证端点」说明在场；不渲染任何"安装"按钮
    expect(screen.getByText(/运维 CLI 管理/)).toBeInTheDocument();
    expect(screen.getByText(/认证 \/ 信任库状态后端未提供/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^安装$/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /下载 \.tar\.gz/ })).toBeInTheDocument();
  });

  it('后端 500 渲染 role=alert 错误', async () => {
    route(() => jsonErr(500, { detail: 'boom' }));
    render(<MarketTab />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });

  it('搜索框回车触发带 q 的请求', async () => {
    const user = userEvent.setup();
    render(<MarketTab />);
    await screen.findByText('市场暂无扩展包');
    fireEvent.change(screen.getByLabelText('搜索扩展包'), { target: { value: 'gpkg' } });
    await user.click(screen.getByRole('button', { name: '搜索' }));
    await waitFor(() => {
      const call = mockFetch.mock.calls.at(-1)![0] as string;
      expect(call).toContain('q=gpkg');
    });
  });
});
