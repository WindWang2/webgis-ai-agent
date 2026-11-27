import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabularDataGrid } from './tabular-data-grid';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { QueryResult } from '@/lib/api/data-fabric';

describe('TabularDataGrid', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const sampleFeatures = [
    {
      id: 'f-1',
      name: '成都大熊猫繁育研究基地',
      category: '旅游景点',
      rating: 4.8,
      is_open: true,
      tags: ['动物园', '地标'],
    },
    {
      id: 'f-2',
      name: '锦里古街',
      category: '历史街区',
      rating: 4.5,
      is_open: true,
      tags: ['古街', '美食'],
    },
    {
      id: 'f-3',
      name: '青城山',
      category: '自然风景',
      rating: 4.9,
      is_open: false,
      tags: ['道教', '名山'],
    },
  ];

  const sampleGeoJSON: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: 'poi-1',
        geometry: { type: 'Point', coordinates: [104.0665, 30.5728] },
        properties: {
          name: '天府广场',
          level: 5,
          active: true,
        },
      },
      {
        type: 'Feature',
        id: 'poi-2',
        geometry: { type: 'Point', coordinates: [104.0815, 30.6558] },
        properties: {
          name: '春熙路',
          level: 4,
          active: false,
        },
      },
    ],
  };

  it('renders loading state when loading is true', () => {
    render(<TabularDataGrid loading={true} />);
    expect(screen.getByText(/正在加载数据集属性/)).toBeInTheDocument();
  });

  it('renders empty state when data is empty', () => {
    render(<TabularDataGrid data={[]} emptyTitle="暂无要素" emptyDescription="请选择有效数据集" />);
    expect(screen.getByText('暂无要素')).toBeInTheDocument();
    expect(screen.getByText('请选择有效数据集')).toBeInTheDocument();
  });

  it('renders flat tabular data and extracts schema columns automatically', () => {
    render(<TabularDataGrid data={sampleFeatures} />);

    // Headers
    expect(screen.getByRole('columnheader', { name: /id/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /name/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /category/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /rating/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /is_open/i })).toBeInTheDocument();

    // Rows
    expect(screen.getByText('成都大熊猫繁育研究基地')).toBeInTheDocument();
    expect(screen.getByText('锦里古街')).toBeInTheDocument();
    expect(screen.getByText('青城山')).toBeInTheDocument();
    expect(screen.getByText('4.8')).toBeInTheDocument();

    // Stats
    expect(screen.getByText(/共 3 条要素/)).toBeInTheDocument();
  });

  it('handles GeoJSON FeatureCollection input with geometry formatting', () => {
    render(<TabularDataGrid data={sampleGeoJSON} />);

    expect(screen.getByText('天府广场')).toBeInTheDocument();
    expect(screen.getByText('春熙路')).toBeInTheDocument();
    expect(screen.getByText('Point [104.0665, 30.5728]')).toBeInTheDocument();
    expect(screen.getByText('Point [104.0815, 30.6558]')).toBeInTheDocument();
  });

  it('supports column sorting: asc -> desc -> none (reset)', async () => {
    const user = userEvent.setup();
    render(<TabularDataGrid data={sampleFeatures} />);

    const ratingHeaderBtn = screen.getByRole('button', { name: /按 rating 排序/i });
    const ratingTh = screen.getByRole('columnheader', { name: /rating/i });

    // Initial state: no sort
    expect(ratingTh).toHaveAttribute('aria-sort', 'none');

    // Click 1: ascending (4.5, 4.8, 4.9)
    await user.click(ratingHeaderBtn);
    expect(ratingTh).toHaveAttribute('aria-sort', 'ascending');
    const cellsAfterAsc = screen.getAllByRole('cell');
    // First row should be 锦里古街 (4.5)
    expect(cellsAfterAsc.some((c) => c.textContent?.includes('锦里古街'))).toBe(true);

    // Click 2: descending (4.9, 4.8, 4.5)
    await user.click(ratingHeaderBtn);
    expect(ratingTh).toHaveAttribute('aria-sort', 'descending');

    // Click 3: reset to default order
    await user.click(ratingHeaderBtn);
    expect(ratingTh).toHaveAttribute('aria-sort', 'none');
  });

  it('filters rows in real-time with search box and displays matching counts', async () => {
    const user = userEvent.setup();
    render(<TabularDataGrid data={sampleFeatures} />);

    const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });
    expect(searchInput).toBeInTheDocument();

    // Search for "锦里"
    await user.type(searchInput, '锦里');
    expect(screen.getByText('锦里古街')).toBeInTheDocument();
    expect(screen.queryByText('青城山')).not.toBeInTheDocument();
    expect(screen.getByText(/匹配 1 \/ 3 条/)).toBeInTheDocument();

    // Clear search using clear button
    const clearBtn = screen.getByRole('button', { name: '清空搜索' });
    await user.click(clearBtn);
    expect(screen.getByText('青城山')).toBeInTheDocument();
    expect(screen.getByText(/共 3 条要素/)).toBeInTheDocument();
  });

  it('shows empty search state when no records match and provides reset action', async () => {
    const user = userEvent.setup();
    render(<TabularDataGrid data={sampleFeatures} />);

    const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });
    await user.type(searchInput, 'xyz123_nonexistent');

    expect(screen.getByText('未找到匹配记录')).toBeInTheDocument();
    const resetButtons = screen.getAllByRole('button', { name: '清空搜索' });
    await user.click(resetButtons[0]);

    expect(screen.getByText('成都大熊猫繁育研究基地')).toBeInTheDocument();
  });

  it('supports pagination controls and page size changing', async () => {
    const user = userEvent.setup();
    // Generate 25 items
    const manyRows = Array.from({ length: 25 }, (_, i) => ({
      id: `id-${i + 1}`,
      name: `测试要素 ${i + 1}`,
      value: i * 10,
    }));

    render(<TabularDataGrid data={manyRows} defaultPageSize={10} pageSizeOptions={[10, 25, 50]} />);

    // Page 1 should show items 1-10
    expect(screen.getByText('测试要素 1')).toBeInTheDocument();
    expect(screen.getByText('测试要素 10')).toBeInTheDocument();
    expect(screen.queryByText('测试要素 11')).not.toBeInTheDocument();
    expect(screen.getByText('1 / 3')).toBeInTheDocument();

    // Next page -> Page 2
    const nextBtn = screen.getByRole('button', { name: '下一页' });
    await user.click(nextBtn);
    expect(screen.getByText('测试要素 11')).toBeInTheDocument();
    expect(screen.getByText('2 / 3')).toBeInTheDocument();

    // Last page -> Page 3
    const lastBtn = screen.getByRole('button', { name: '最后一页' });
    await user.click(lastBtn);
    expect(screen.getByText('测试要素 25')).toBeInTheDocument();
    expect(screen.getByText('3 / 3')).toBeInTheDocument();

    // Change page size to 25 -> Should show all on Page 1
    const sizeSelect = screen.getByRole('combobox', { name: '每页显示条数' });
    await user.selectOptions(sizeSelect, '25');
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.getByText('测试要素 1')).toBeInTheDocument();
    expect(screen.getByText('测试要素 25')).toBeInTheDocument();
  });

  // 行复制剪贴板契约（成功 / 被拒 / API 缺失三条路径）的唯一定居点在文件末尾的
  // 「TabularDataGrid 行复制剪贴板接管（#1746-8）」describe，勿在别处重复 stub
  // navigator.clipboard。

  it('triggers onRowClick when a row is clicked', async () => {
    const user = userEvent.setup();
    const handleRowClick = vi.fn();
    render(<TabularDataGrid data={sampleFeatures} onRowClick={handleRowClick} />);

    const firstRowText = screen.getByText('成都大熊猫繁育研究基地');
    await user.click(firstRowText);

    expect(handleRowClick).toHaveBeenCalledTimes(1);
    expect(handleRowClick.mock.calls[0][0]).toMatchObject({
      name: '成都大熊猫繁育研究基地',
    });
    expect(handleRowClick.mock.calls[0][1]).toBe(0);
  });

  it('samples top 100 rows for schema inference on large datasets (FRONT-06)', () => {
    // 500 rows where only first 100 have `sampledField`, rows > 100 have `lateField`
    const rows = Array.from({ length: 500 }, (_, i) => ({
      id: `row-${i}`,
      val: i,
      ...(i < 100 ? { sampledField: `sample-${i}` } : { lateField: `late-${i}` }),
    }));

    render(<TabularDataGrid data={rows} />);
    // Column headers should include sampledField from top 100 rows
    expect(screen.getByRole('columnheader', { name: /sampledField/i })).toBeInTheDocument();
    // lateField which only exists past row 100 was not scanned in schema extraction
    expect(screen.queryByRole('columnheader', { name: /lateField/i })).toBeNull();
  });
});

describe('Spatial Explorer TabularDataGrid Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const complexSpatialResult: QueryResult = {
    dataset_id: 'cd-district-economy-2026',
    total_count: 5,
    features: [
      {
        id: 'cd-01',
        district: '锦江区',
        gdp_billion: 142.5,
        growth_rate: 0.065,
        is_core: true,
        geometry: { type: 'Polygon', coordinates: [[[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.6]]] },
      },
      {
        id: 'cd-02',
        district: '青羊区',
        gdp_billion: 156.8,
        growth_rate: 0.058,
        is_core: true,
        geometry: { type: 'Polygon', coordinates: [[[104.0, 30.6], [104.05, 30.6], [104.05, 30.68], [104.0, 30.6]]] },
      },
      {
        id: 'cd-03',
        district: '金牛区',
        gdp_billion: 158.2,
        growth_rate: 0.061,
        is_core: true,
        geometry: { type: 'Polygon', coordinates: [[[104.02, 30.68], [104.08, 30.68], [104.08, 30.75], [104.02, 30.68]]] },
      },
      {
        id: 'cd-04',
        district: '武侯区',
        gdp_billion: 139.1,
        growth_rate: 0.052,
        is_core: true,
        geometry: { type: 'Polygon', coordinates: [[[104.01, 30.61], [104.09, 30.61], [104.09, 30.67], [104.01, 30.61]]] },
      },
      {
        id: 'cd-05',
        district: '成华区',
        gdp_billion: 135.4,
        growth_rate: 0.068,
        is_core: false,
        geometry: { type: 'Polygon', coordinates: [[[104.09, 30.65], [104.16, 30.65], [104.16, 30.73], [104.09, 30.65]]] },
      },
    ],
  };

  it('correctly parses complex QueryResult features and extracts schema with geometry badges', () => {
    render(<TabularDataGrid data={complexSpatialResult} />);

    expect(screen.getByText('锦江区')).toBeInTheDocument();
    expect(screen.getByText('青羊区')).toBeInTheDocument();
    expect(screen.getByText('142.5')).toBeInTheDocument();
    expect(screen.getByText('156.8')).toBeInTheDocument();
    expect(screen.getAllByText(/Polygon/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/共 5 条要素/)).toBeInTheDocument();
  });

  it('performs end-to-end multi-column search and sorting workflow', async () => {
    const user = userEvent.setup();
    render(<TabularDataGrid data={complexSpatialResult} />);

    // Sort by gdp_billion ascending
    const gdpThBtn = screen.getByRole('button', { name: /按 gdp_billion 排序/i });
    await user.click(gdpThBtn);

    // Lowest GDP should appear first in table: 成华区 (135.4)
    const tableCells = screen.getAllByRole('cell');
    expect(tableCells.some((c) => c.textContent?.includes('成华区'))).toBe(true);

    // Search filter for "金牛"
    const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });
    await user.type(searchInput, '金牛');

    expect(screen.getByText('金牛区')).toBeInTheDocument();
    expect(screen.queryByText('成华区')).not.toBeInTheDocument();
    expect(screen.getByText(/匹配 1 \/ 5 条/)).toBeInTheDocument();
  });
});

/**
 * #1746-8：行复制按钮必须接管 clipboard 的失败路径。
 *
 * `navigator.clipboard.writeText(...)` 在权限被拒 / 文档失焦时 reject，在非
 * 安全上下文（或旧浏览器缺 API）时同步 TypeError —— 两条路径无人接管都会变成
 * unhandled rejection。
 */
describe('TabularDataGrid 行复制剪贴板接管（#1746-8）', () => {
  const rows: QueryResult = {
    dataset_id: 'cd-district-economy-2026',
    total_count: 2,
    features: [
      { id: 'cd-01', district: '锦江区', gdp_billion: 142.5 },
      { id: 'cd-02', district: '青羊区', gdp_billion: 156.8 },
    ],
  };

  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    } else {
      Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

  // 用 fireEvent 而非 userEvent：userEvent.setup() 会自装 clipboard stub 覆盖
  // 本用例的注入，无法观测组件的真实失败路径。
  function copyFirstRow() {
    fireEvent.click(screen.getByRole('button', { name: '复制第 1 行数据' }));
  }

  it('剪贴板被拒时不产生 unhandled rejection，且不显示「已复制」', async () => {
    const seen: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      seen.push(reason);
    };
    process.on('unhandledRejection', onUnhandled);

    try {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')) },
      });

      render(<TabularDataGrid data={rows} />);
      copyFirstRow();

      await new Promise((r) => setTimeout(r, 20));
      expect(seen).toHaveLength(0);
      expect(screen.queryByTitle('已复制行 JSON')).not.toBeInTheDocument();
    } finally {
      process.off('unhandledRejection', onUnhandled);
    }
  });

  it('navigator.clipboard 缺失时点击不抛（静默降级）', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    Reflect.deleteProperty(navigator, 'clipboard');

    render(<TabularDataGrid data={rows} />);

    expect(() => copyFirstRow()).not.toThrow();
    await new Promise((r) => setTimeout(r, 20));
    // 缺 API 是同步 TypeError：不得冒泡成 React 未捕获错误
    expect(errorSpy).not.toHaveBeenCalled();
    expect(screen.queryByTitle('已复制行 JSON')).not.toBeInTheDocument();
  });

  it('剪贴板可用时正常显示「已复制」回执', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });

    render(<TabularDataGrid data={rows} />);
    copyFirstRow();

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0][0]).toContain('锦江区');
    expect(await screen.findByTitle('已复制行 JSON')).toBeInTheDocument();
  });
});
