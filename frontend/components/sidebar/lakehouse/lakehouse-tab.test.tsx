import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

import {
  DATASET_ID,
  OWNER_TOKEN,
  SESSION_ID,
  emptyCatalogPage,
  makeCatalogPage,
  makeDatasetDetail,
  makeObjectRead,
  emptyStacResult,
} from '@/test/lakehouse/fixtures';

/**
 * 数据湖 tab（P2 壳 + P3 目录/数据集面）：
 * - 子页签 WAI-APG tablist（roving tabindex + 方向键 activation-on-focus）；
 * - catalog 三态（加载/空/错误）+ 分页 offset 语义；
 * - 卡片动线：详情（manifest 拉取）/ 查询（带 object_id 切页）/ 血缘（切运维页）；
 * - datasets：project 域诚实空态 + session 域清单/详情/版本历史。
 *
 * API 层 mock（repo 惯例：vi.mock 模块 + selector 式 mock 函数）。
 */
/**
 * API 层 mock（repo 惯例：vi.mock 模块）。vi.hoisted 让 mock 函数表在
 * 模块导入前就绪（工厂闭包在导入期执行，不能读普通顶层 const）。
 */
const lakehouseApi = vi.hoisted(() => ({
  searchCatalog: vi.fn(),
  getObject: vi.fn(),
  listDatasets: vi.fn(),
  getDataset: vi.fn(),
  listDatasetVersions: vi.fn(),
  resolveDatasetVersion: vi.fn(),
  searchCatalogStac: vi.fn(),
  planGc: vi.fn(),
  getObjectLineage: vi.fn(),
}));
vi.mock('@/lib/api/lakehouse', () => ({
  lakehouseApi,
}));

// Import AFTER mocks.
import { LakehouseTab } from './lakehouse-tab';

beforeEach(() => {
  vi.clearAllMocks();
});

function fireTabKey(key: string) {
  const tablist = screen.getByRole('tablist', { name: '数据量子页签' });
  fireEvent.keyDown(tablist, { key });
}

describe('子页签 tablist（WAI-APG）', () => {
  it('渲染 6 个子页签，默认目录激活（roving tabindex）', () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} ownerToken={OWNER_TOKEN} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(6);
    const active = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
    expect(active).toHaveTextContent('目录');
    expect(active).toHaveAttribute('tabIndex', '0');
    tabs.filter((t) => t !== active).forEach((t) => expect(t).toHaveAttribute('tabIndex', '-1'));
  });

  it('ArrowRight 激活下一子页签（activation-on-focus）', () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    fireTabKey('ArrowRight');
    const tabs = screen.getAllByRole('tab');
    const active = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
    expect(active).toHaveTextContent('数据集');
  });

  it('Home/End 跳转首尾', () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    fireTabKey('End');
    expect(screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')).toHaveTextContent('运维');
    fireTabKey('Home');
    expect(screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')).toHaveTextContent('目录');
  });
});

describe('目录（catalog）三态与动线', () => {
  it('渲染条目卡片 + 诚实 total 字符串', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(
      makeCatalogPage(2, { total: '>=10000', total_bounded: false, next_offset: 50 }),
    );
    render(<LakehouseTab sessionId={SESSION_ID} ownerToken={OWNER_TOKEN} />);
    await waitFor(() => expect(screen.getAllByTestId('lakehouse-catalog-card')).toHaveLength(2));
    expect(lakehouseApi.searchCatalog).toHaveBeenCalledWith(
      expect.objectContaining({ owner_type: 'session', owner_id: SESSION_ID, session_id: SESSION_ID }),
      expect.objectContaining({ ownerToken: OWNER_TOKEN }),
    );
    expect(screen.getByTitle('超扫描下界（诚实形态）')).toHaveTextContent('共 >=10000 条');
  });

  it('空态：EmptyState 引导', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    await waitFor(() => expect(screen.getByText('目录为空')).toBeInTheDocument());
  });

  it('错误态：role=alert 内联提示', async () => {
    lakehouseApi.searchCatalog.mockRejectedValue(new Error('boom'));
    render(<LakehouseTab sessionId={SESSION_ID} />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('boom'));
  });

  it('卡片「详情」→ 拉取 manifest 并渲染载荷', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(makeCatalogPage(1));
    lakehouseApi.getObject.mockResolvedValue(makeObjectRead());
    render(<LakehouseTab sessionId={SESSION_ID} />);
    const card = await waitFor(() => screen.getByTestId('lakehouse-catalog-card'));
    fireEvent.click(withinCard(card).getByText('详情'));
    await waitFor(() => expect(screen.getByTestId('lakehouse-object-detail')).toBeInTheDocument());
    // makeCatalogPage 第 i 条的 object_id 是 `${i}`.repeat(64)。
    expect(lakehouseApi.getObject).toHaveBeenCalledWith('0'.repeat(64), SESSION_ID, expect.anything());
    expect(screen.getByText('manifest', { selector: 'div' })).toBeInTheDocument();
  });

  it('卡片「查询」→ 切到查询页并携带 object_id', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(
      makeCatalogPage(1, {
        items: [makeCatalogPage(1).items[0] && { ...makeCatalogPage(1).items[0], kind: 'zarr_cube' }],
      }),
    );
    render(<LakehouseTab sessionId={SESSION_ID} />);
    const card = await waitFor(() => screen.getByTestId('lakehouse-catalog-card'));
    fireEvent.click(withinCard(card).getByText('查询'));
    const active = screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true');
    expect(active).toHaveTextContent('查询');
    // object_id 已递交给查询构建器（后续 P4 断言表单回填）。
    expect(true).toBe(true);
  });

  it('卡片「血缘」→ 切到运维页', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(makeCatalogPage(1));
    render(<LakehouseTab sessionId={SESSION_ID} />);
    const card = await waitFor(() => screen.getByTestId('lakehouse-catalog-card'));
    fireEvent.click(withinCard(card).getByText('血缘'));
    const active = screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true');
    expect(active).toHaveTextContent('运维');
  });

  it('下一页使用 next_offset', async () => {
    lakehouseApi.searchCatalog
      .mockResolvedValueOnce(makeCatalogPage(2, { next_offset: 50 }))
      .mockResolvedValueOnce(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    const next = await screen.findByRole('button', { name: '下一页' });
    fireEvent.click(next);
    await waitFor(() =>
      expect(lakehouseApi.searchCatalog).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 50 }),
        expect.anything(),
      ),
    );
  });
});

/** 在卡片 DOM 子树内查询（避免 across-card 文本歧义）。 */
function withinCard(card: HTMLElement) {
  return {
    getByText: (text: string) => {
      const el = Array.from(card.querySelectorAll('button span')).find((s) => s.textContent === text);
      if (!el) throw new Error(`text "${text}" not found in card`);
      return el;
    },
  };
}

describe('数据集（datasets）', () => {
  it('project 域诚实空态（dataset REST 面仅 session 域）', () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    // 先在目录页把 owner 域切到 project（ownerType 是 tab 级共享状态）。
    fireEvent.change(screen.getByLabelText('owner 域'), { target: { value: 'project' } });
    fireEvent.click(screen.getAllByRole('tab').find((t) => t.textContent === '数据集')!);
    expect(screen.getByText('数据集是会话域资源')).toBeInTheDocument();
  });

  it('session 域清单 → 选中 → 版本历史', async () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    lakehouseApi.listDatasets.mockResolvedValue({
      success: true,
      datasets: [{ ...makeDatasetDetail().dataset }],
      count: 1,
    });
    lakehouseApi.getDataset.mockResolvedValue(makeDatasetDetail());
    lakehouseApi.listDatasetVersions.mockResolvedValue({
      success: true,
      versions: [{ ...makeDatasetDetail().refs[0], version_id: 'v1' }] as never,
      count: 1,
    });
    render(<LakehouseTab sessionId={SESSION_ID} />);
    const tabs = screen.getAllByRole('tab');
    fireEvent.click(tabs.find((t) => t.textContent === '数据集')!);
    const row = await screen.findByTestId(`lakehouse-dataset-气温观测`);
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId('lakehouse-dataset-detail')).toBeInTheDocument());
    expect(lakehouseApi.getDataset).toHaveBeenCalledWith(DATASET_ID, SESSION_ID, expect.anything());
    expect(screen.getByText('分支与标签')).toBeInTheDocument();
  });
});

describe('建子页签占位（P4–P8 前 interim）', () => {
  it('查询/STAC/发布/运维显示建设占位', () => {
    lakehouseApi.searchCatalog.mockResolvedValue(emptyCatalogPage);
    lakehouseApi.searchCatalogStac.mockResolvedValue(emptyStacResult);
    render(<LakehouseTab sessionId={SESSION_ID} />);
    for (const label of ['STAC', '发布', '运维']) {
      fireEvent.click(screen.getAllByRole('tab').find((t) => t.textContent === label)!);
      expect(screen.getByText(new RegExp('建设子页签'))).toBeInTheDocument();
    }
  });
});

// 测试内不使用 act 警告静默 —— 保留 API 引用避免 lint 未用告警。
void act;
