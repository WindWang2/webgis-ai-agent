import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabularDataGrid } from './tabular-data-grid';
import type { GeoJSONFeatureCollection } from '@/lib/types';

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

  it('copies row data to clipboard on copy button click', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextSpy },
      writable: true,
      configurable: true,
    });

    render(<TabularDataGrid data={sampleFeatures} />);

    const copyRowBtn = screen.getByRole('button', { name: '复制第 1 行数据' });
    expect(copyRowBtn).toBeInTheDocument();

    fireEvent.click(copyRowBtn);

    expect(writeTextSpy).toHaveBeenCalledTimes(1);
    const copiedText = writeTextSpy.mock.calls[0][0];
    expect(copiedText).toContain('成都大熊猫繁育研究基地');
    expect(copiedText).toContain('旅游景点');
    expect(await screen.findByTitle('已复制行 JSON')).toBeInTheDocument();
  });

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
});
