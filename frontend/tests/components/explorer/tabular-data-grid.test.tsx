import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabularDataGrid } from '@/components/explorer/tabular-data-grid';
import type { QueryResult } from '@/lib/api/data-fabric';

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
