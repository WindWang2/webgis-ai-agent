import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryConsole } from './query-console';
import { useQueryConsoleStore } from '@/lib/hooks/use-query-console';

// 仓库网络 mock 惯例：模块级 mock api 客户端（P0 勘察 §5：msw 未引入）。
vi.mock('@/lib/api/data-fabric', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/lib/api/data-fabric')>();
  return {
    ...orig,
    dataFabricApi: {
      ...orig.dataFabricApi,
      listSpatialCatalog: vi.fn().mockResolvedValue({
        total: 1,
        limit: 30,
        offset: 0,
        items: [
          {
            id: 'cat-1',
            source_id: 'src-1',
            name: 'rivers',
            title: '全国水系',
            geometry_type: 'line',
            availability: 'available',
          },
        ],
      }),
      getCatalogItem: vi.fn().mockResolvedValue({
        id: 'cat-1',
        source_id: 'src-1',
        name: 'rivers',
        title: '全国水系',
        geometry_type: 'line',
        availability: 'available',
      }),
      queryCatalogItem: vi.fn().mockResolvedValue({
        dataset_id: 'cat-1',
        query_spec: {},
        features: [{ type: 'Feature', properties: { name: 'a' }, geometry: null }],
        returned_count: 1,
        total_matching: 1,
        truncated: false,
        execution_time_seconds: 0.042,
        metadata: {
          query_plan: {
            execution_mode: 'pushdown',
            pushed_filters: ['name ILIKE A%'],
            local_filters: [],
            estimated_rows: 1,
          },
        },
      }),
      explainCatalogItem: vi.fn().mockResolvedValue({
        status: 'ok',
        dataset_id: 'cat-1',
        explain: ['Seq Scan on rivers', 'Filter: (name ILIKE A%)'],
        plan: {},
        capabilities: [],
        dataset: { geometry_type: 'line', srs: 'EPSG:4326', feature_count: 10 },
      }),
      materializeCatalogItem: vi.fn().mockResolvedValue({
        ref_id: 'ref:df-1',
        feature_count: 1,
      }),
      fetchRefGeoJSON: vi.fn().mockResolvedValue({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', properties: { name: 'a' }, geometry: null }],
      }),
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  useQueryConsoleStore.getState().close();
  localStorage.clear();
});

describe('QueryConsole', () => {
  it('目标选择 → 编辑 → 执行 → pushdown 披露诚实展示', async () => {
    render(<QueryConsole sessionId="sess-1" ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());

    // 左栏目录列表出现（防抖 300ms）
    const targetBtn = await screen.findByRole('button', { name: /全国水系/ });
    fireEvent.click(targetBtn);
    await screen.findByTestId('console-target');

    // 写过滤条件并执行
    const where = screen.getByTestId('console-where');
    fireEvent.change(where, { target: { value: "name ILIKE 'A%'" } });
    fireEvent.click(screen.getByTestId('console-run'));

    const disclosure = await screen.findByTestId('console-disclosure');
    expect(disclosure).toHaveTextContent('返回 1 行');
    expect(screen.getByTestId('console-execution-mode')).toHaveTextContent('已下推执行');
    expect(disclosure).toHaveTextContent('下推条件：name ILIKE A%');
    expect(screen.getByTestId('console-result')).toBeInTheDocument();
  });

  it('危险语句被守卫拦截：执行按钮禁用 + 警示可见，不发请求', async () => {
    const { dataFabricApi } = await import('@/lib/api/data-fabric');
    render(<QueryConsole sessionId="sess-1" ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());
    fireEvent.click(await screen.findByRole('button', { name: /全国水系/ }));
    await screen.findByTestId('console-target');

    fireEvent.change(screen.getByTestId('console-where'), { target: { value: 'DELETE FROM t' } });
    expect(await screen.findByTestId('console-guard')).toHaveTextContent('只读检索');
    expect(screen.getByTestId('console-run')).toBeDisabled();

    fireEvent.click(screen.getByTestId('console-run'));
    expect(dataFabricApi.queryCatalogItem).not.toHaveBeenCalled();
  });

  it('explain 展示查询计划行', async () => {
    render(<QueryConsole sessionId="sess-1" ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());
    fireEvent.click(await screen.findByRole('button', { name: /全国水系/ }));
    await screen.findByTestId('console-target');
    fireEvent.click(screen.getByTestId('console-explain'));
    const box = await screen.findByTestId('console-explain-result');
    expect(box).toHaveTextContent('Seq Scan on rivers');
  });

  it('结果上图：无会话时拦截提示，不发 materialize', async () => {
    const { dataFabricApi } = await import('@/lib/api/data-fabric');
    render(<QueryConsole sessionId={null} ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());
    fireEvent.click(await screen.findByRole('button', { name: /全国水系/ }));
    await screen.findByTestId('console-target');
    fireEvent.change(screen.getByTestId('console-where'), { target: { value: 'a = 1' } });
    fireEvent.click(screen.getByTestId('console-run'));
    await screen.findByTestId('console-disclosure');

    const toMap = screen.getByTestId('console-to-map');
    await waitFor(() => expect(toMap).not.toBeDisabled());
    fireEvent.click(toMap);
    await waitFor(() => expect(toMap).not.toBeDisabled());
    expect(dataFabricApi.materializeCatalogItem).not.toHaveBeenCalled();
  });

  it('结果上图：有会话时走 materialize → 加层 → 水合', async () => {
    const { dataFabricApi } = await import('@/lib/api/data-fabric');
    render(<QueryConsole sessionId="sess-1" ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());
    fireEvent.click(await screen.findByRole('button', { name: /全国水系/ }));
    await screen.findByTestId('console-target');
    fireEvent.change(screen.getByTestId('console-where'), { target: { value: 'a = 1' } });
    fireEvent.click(screen.getByTestId('console-run'));
    await screen.findByTestId('console-disclosure');

    const toMap = screen.getByTestId('console-to-map');
    await waitFor(() => expect(toMap).not.toBeDisabled());
    fireEvent.click(toMap);
    await waitFor(() => expect(dataFabricApi.materializeCatalogItem).toHaveBeenCalled());
    expect(dataFabricApi.fetchRefGeoJSON).toHaveBeenCalled();
  });

  it('查询历史落盘：执行后写入 localStorage', async () => {
    render(<QueryConsole sessionId="sess-1" ownerToken={null} />);
    act(() => useQueryConsoleStore.getState().openWith());
    fireEvent.click(await screen.findByRole('button', { name: /全国水系/ }));
    await screen.findByTestId('console-target');
    fireEvent.change(screen.getByTestId('console-where'), { target: { value: 'a = 1' } });
    fireEvent.click(screen.getByTestId('console-run'));
    await screen.findByTestId('console-disclosure');
    await waitFor(() =>
      expect(localStorage.setItem).toHaveBeenCalledWith(
        'geoagent-query-console-history-v1',
        expect.stringContaining('a = 1'),
      ),
    );
  });
});
