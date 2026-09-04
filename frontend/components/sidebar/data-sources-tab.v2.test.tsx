import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor, cleanup } from '@testing-library/react';

/**
 * ADR-0094（Data Fabric V2）— 数据工作区（Data Workspace）前端契约测试。
 *
 * 覆盖 Wave J 的六个关键行为：
 *   1. explain 面板：计划行 monospace 块 + pushdown ✓/✗ 徽标 + 数据集指纹；
 *   2. QuerySpec 组装：字段投影 / where / limit / 聚合（aggregate + group_by +
 *      result_mode='statistics'）精确落入请求体；
 *   3. statistics 模式：聚合行表格 + 执行证据（指纹 / 行数 / 下推命中），
 *      且不渲染要素网格（无服务端分页组）；
 *   4. cursor 服务端分页：next_cursor 携带进下一次查询；
 *   5. 目录可用状态：「已下线」徽标 + 可用状态筛选（客户端过滤）；
 *   6. 目录同步 diff：新增/更新/不变/下线 通知 + warnings + toast。
 *
 * 与 data-sources-tab.test.tsx 相同的 mock 配方：selector 风格 store mock +
 * 整包 mock @/lib/api/data-fabric；组件在 mock 注册之后导入。
 */

// Selector-style store mocks（同 layers-tab.test.tsx 模式）：vi.mock 工厂只在
// render 时惰性读取这些常量，hoisting 安全。
const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => any) => selector(toastStore),
}));

const hudStore = { addLayer: vi.fn(), updateLayer: vi.fn(), theme: 'dark' };
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof hudStore) => any) => selector(hudStore),
}));

// 整包 mock：dataFabricApi 的 14 个方法全部替换（ADR-0094 V2 完整面）。
vi.mock('@/lib/api/data-fabric', () => ({
  dataFabricApi: {
    listDataSources: vi.fn(),
    listSpatialCatalog: vi.fn(),
    createDataSource: vi.fn(),
    getDataSource: vi.fn(),
    deleteDataSource: vi.fn(),
    probeDataSource: vi.fn(),
    syncDataSourceCatalog: vi.fn(),
    getCatalogItem: vi.fn(),
    getCatalogItemDescriptor: vi.fn(),
    previewCatalogItem: vi.fn(),
    queryCatalogItem: vi.fn(),
    explainCatalogItem: vi.fn(),
    materializeCatalogItem: vi.fn(),
    fetchRefGeoJSON: vi.fn(),
  },
}));

// 在 mock 注册之后再导入，确保组件拿到的是 mock 实现。
import { DataSourcesTab } from './data-sources-tab';
import { dataFabricApi } from '@/lib/api/data-fabric';
import type {
  DatasetDescriptor,
  DataSource,
} from '@/lib/api/data-fabric';

type CatalogResponse = Awaited<ReturnType<typeof dataFabricApi.listSpatialCatalog>>;
type CatalogItemFixture = CatalogResponse['items'][number];

function emptyCatalog(): CatalogResponse {
  return { total: 0, limit: 50, offset: 0, items: [] };
}

function makeCatalogItem(
  id: string,
  title: string,
  extra?: Partial<CatalogItemFixture>
): CatalogItemFixture {
  return {
    id,
    source_id: 's1',
    name: title,
    title,
    description: `${title} 描述`,
    feature_type: 'vector',
    ...extra,
  };
}

/** Descriptor 夹具：3 个投影字段 + 中等规模（< 5000，不触发大集警告）。 */
function makeDescriptor(id: string): DatasetDescriptor {
  return {
    id,
    title: '学校分布',
    source_type: 'ogc',
    geometry_type: 'Point',
    srs: 'EPSG:4326',
    feature_count: 1200,
    fields: [
      { name: 'type', type: 'text' },
      { name: 'students', type: 'integer' },
      { name: 'name', type: 'text' },
    ],
  };
}

function makeSource(): DataSource {
  return {
    id: 's1',
    name: 'OGC 源',
    source_type: 'ogc',
    endpoint_url: 'https://geo.example.com/ogc',
    status: 'healthy',
    capabilities: [],
    connection_profile: {},
  };
}

/**
 * 渲染 → 等待目录加载 → 点击「数据集」检查按钮 → 等待契约摘要出现
 * （Descriptor 已解析，字段复选框可交互）。
 */
async function renderWithInspectedItem() {
  vi.mocked(dataFabricApi.listSpatialCatalog).mockResolvedValue({
    total: 1,
    limit: 50,
    offset: 0,
    items: [makeCatalogItem('cat1', '学校分布')],
  });
  vi.mocked(dataFabricApi.getCatalogItemDescriptor).mockResolvedValue(makeDescriptor('cat1'));
  render(<DataSourcesTab />);
  const inspect = await screen.findByRole('button', { name: '数据集' });
  await act(async () => {
    fireEvent.click(inspect);
  });
  await screen.findByText('要素总数');
}

describe('DataSourcesTab — ADR-0094 Data Workspace（Wave J）', () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.mocked(dataFabricApi.listDataSources).mockResolvedValue({ sources: [] });
    vi.mocked(dataFabricApi.listSpatialCatalog).mockResolvedValue(emptyCatalog());
  });

  it('explain 面板展示计划行、pushdown 徽标与数据集指纹，且以默认 spec 请求', async () => {
    vi.mocked(dataFabricApi.explainCatalogItem).mockResolvedValue({
      status: 'success',
      dataset_id: 'cat1',
      dataset_fingerprint: 'fp-abc',
      explain: ['Seq Scan on schools', '  Filter: (type = ...)'],
      plan: {
        pushed_spatial: true,
        pushed_filters: ['type'],
        pushed_projection: false,
        pushed_aggregation: false,
        estimated_rows: 42,
        pagination_strategy: 'cursor',
        result_mode: 'features',
      },
    });
    await renderWithInspectedItem();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '解释计划' }));
    });

    // 计划行 monospace 块。
    const pre = await screen.findByLabelText('查询计划详情');
    expect(pre.textContent).toContain('Seq Scan on schools');

    // pushdown ✓/✗ 徽标（aria-label 携带完整语义）。
    expect(screen.getByLabelText('bbox下推已启用')).toBeInTheDocument();
    expect(screen.getByLabelText('filter下推已启用')).toBeInTheDocument();
    expect(screen.getByLabelText('projection下推未启用')).toBeInTheDocument();
    expect(screen.getByLabelText('aggregation下推未启用')).toBeInTheDocument();

    // 数据集指纹 + 估算行数。
    expect(screen.getByText('fp-abc')).toBeInTheDocument();
    expect(screen.getByText(/估算 42 行/)).toBeInTheDocument();

    // 默认 spec 恰为 { limit: 100 }（limitText 默认 '100'，其余未设置）。
    expect(vi.mocked(dataFabricApi.explainCatalogItem)).toHaveBeenCalledWith('cat1', {
      limit: 100,
    });
  });

  it('QuerySpec 组装：字段投影 / where / limit 与 aggregate + group_by + statistics 精确入请求体', async () => {
    const query = () => vi.mocked(dataFabricApi.queryCatalogItem);

    // (a) 投影 + where + limit
    vi.mocked(dataFabricApi.queryCatalogItem).mockResolvedValue({
      dataset_id: 'cat1',
      features: [{ name: 's' }],
      total_count: 1,
    });
    await renderWithInspectedItem();

    fireEvent.click(screen.getByLabelText('选择字段 type'));
    fireEvent.click(screen.getByLabelText('选择字段 students'));
    fireEvent.change(screen.getByPlaceholderText("type = 'school' AND students > 500"), {
      target: { value: "type = 'school' AND students > 500" },
    });
    fireEvent.change(screen.getByLabelText('行数上限'), { target: { value: '50' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '执行查询' }));
    });

    await waitFor(() =>
      expect(query()).toHaveBeenCalledWith('cat1', {
        fields: ['type', 'students'],
        where: "type = 'school' AND students > 500",
        limit: 50,
      })
    );
    const firstCall = query().mock.calls[0][1];
    expect(firstCall.bbox).toBeUndefined();
    expect(firstCall.order_by).toBeUndefined();
    expect(firstCall.aggregate).toBeUndefined();
    expect(firstCall.result_mode).toBeUndefined();

    // (b) 聚合 → aggregate + group_by + result_mode='statistics'（默认 limit 100）
    cleanup();
    await renderWithInspectedItem();

    fireEvent.change(screen.getByLabelText('聚合函数'), { target: { value: 'sum' } });
    fireEvent.change(screen.getByLabelText('聚合字段'), { target: { value: 'students' } });
    fireEvent.change(screen.getByLabelText('分组字段（group by）'), {
      target: { value: 'type' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '执行查询' }));
    });

    await waitFor(() =>
      expect(query()).toHaveBeenLastCalledWith('cat1', {
        aggregate: [{ func: 'sum', field: 'students' }],
        group_by: ['type'],
        result_mode: 'statistics',
        limit: 100,
      })
    );
    const aggCall = query().mock.calls[query().mock.calls.length - 1][1];
    expect(aggCall.fields).toBeUndefined();
    expect(aggCall.where).toBeUndefined();
    expect(aggCall.bbox).toBeUndefined();
  });

  it('statistics 模式渲染聚合表格与执行证据，且不渲染要素网格 / 服务端分页', async () => {
    vi.mocked(dataFabricApi.queryCatalogItem).mockResolvedValue({
      dataset_id: 'cat1',
      features: [],
      result_mode: 'statistics',
      data: [
        { type: 'school', n: 5 },
        { type: 'park', n: 2 },
      ],
      returned_count: 2,
      metadata: {
        query_evidence: {
          query_fingerprint: 'qf-999',
          rows_fetched: 2,
          rows_returned: 2,
          pushdowns: { aggregate: true },
        },
      },
    });
    await renderWithInspectedItem();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '执行查询' }));
    });

    const table = await screen.findByLabelText('聚合结果');
    expect(table.textContent).toContain('type');
    expect(table.textContent).toContain('n');
    expect(table.textContent).toContain('school');
    expect(table.textContent).toContain('5');
    expect(table.textContent).toContain('park');
    expect(table.textContent).toContain('2');

    // 执行证据：查询指纹 + 下推命中。
    expect(screen.getByText('qf-999')).toBeInTheDocument();
    expect(screen.getByText('aggregate')).toBeInTheDocument();

    // 统计模式不渲染要素网格 → 全页唯一 table；无服务端分页组。
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.queryByRole('group', { name: '服务端分页' })).not.toBeInTheDocument();
  });

  it('cursor 服务端分页：next_cursor 携带进下一次查询', async () => {
    const query = () => vi.mocked(dataFabricApi.queryCatalogItem);
    vi.mocked(dataFabricApi.queryCatalogItem).mockResolvedValue({
      dataset_id: 'cat1',
      features: [
        { name: 's1' },
        { name: 's2' },
      ],
      result_mode: 'features',
      has_more: true,
      next_cursor: 'cursor-page-2',
      total_matching: 200,
      returned_count: 2,
      metadata: {
        query_plan: { pagination_strategy: 'cursor' },
      },
    });
    await renderWithInspectedItem();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '执行查询' }));
    });

    // 游标模式：唯一的「下一页（游标）」按钮（表格内置分页的 aria-label 恰为「下一页」）。
    const next = await screen.findByRole('button', { name: /下一页（游标）/ });
    await act(async () => {
      fireEvent.click(next);
    });

    await waitFor(() => expect(query()).toHaveBeenCalledTimes(2));
    expect(query().mock.calls[1][0]).toBe('cat1');
    expect(query().mock.calls[1][1]).toEqual({ limit: 100, cursor: 'cursor-page-2' });
  });

  it('目录可用状态：「已下线」徽标 + 可用状态筛选隐藏 unavailable 条目', async () => {
    vi.mocked(dataFabricApi.listSpatialCatalog).mockResolvedValue({
      total: 2,
      limit: 50,
      offset: 0,
      items: [
        makeCatalogItem('a1', '可用集'),
        makeCatalogItem('b1', '旧集', { availability: 'unavailable' }),
      ],
    });
    render(<DataSourcesTab />);

    await screen.findByText('可用集');
    expect(screen.getByText('旧集')).toBeInTheDocument();

    // 「已下线」徽标（span）恰有一个 —— 工具条的同名 chip 是 button，需排除。
    const badges = screen.getAllByText('已下线').filter((el) => el.tagName !== 'BUTTON');
    expect(badges).toHaveLength(1);

    // 客户端过滤：点「可用」chip 后 unavailable 条目隐藏（无二次请求，无需 fake timers）。
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '可用' }));
    });
    expect(screen.queryByText('旧集')).not.toBeInTheDocument();
    expect(screen.getByText('可用集')).toBeInTheDocument();
  });

  it('目录同步 diff：新增/更新/不变/下线 通知 + warnings + toast', async () => {
    vi.mocked(dataFabricApi.listDataSources).mockResolvedValue({ sources: [makeSource()] });
    vi.mocked(dataFabricApi.syncDataSourceCatalog).mockResolvedValue({
      success: true,
      synced_count: 6,
      diff: { added: 2, updated: 1, unchanged: 3, removed: 1 },
      warnings: ['表 x 权限不足'],
    });
    render(<DataSourcesTab />);

    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /数据源/ }));
    });
    await screen.findByText('OGC 源');

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '同步' }));
    });

    // 同步通知（variant=warning → role="status"）。
    const notice = await screen.findByRole('status');
    expect(notice.textContent).toMatch(/新增 2 · 更新 1 · 不变 3 · 下线 1/);
    expect(notice.textContent).toContain('「OGC 源」');
    expect(screen.getByText('表 x 权限不足')).toBeInTheDocument();

    // toast 文案携带 diff 摘要。
    const toastCalls = toastStore.addToast.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(toastCalls.some((t) => t.includes('目录同步完成：新增 2'))).toBe(true);
  });
});
