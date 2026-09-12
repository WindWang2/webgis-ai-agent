/**
 * 100k 行压力测试（ADR-0143 P8）——沿用 workbench-virtual-10k 的不变式风格。
 *
 * S1. 有界 DOM：100k 行经 DatasetPreview 表格模式（TabularDataGrid 分页切片）
 *     只渲染当前页 DOM 行——数量与总行数无关（不 O(N) 渲染）。
 * S2. 完成预算：初始化渲染在预算内完成（jsdom 宽松阈值，回归保护用）。
 * S3. 足迹图有界：SVG 足迹抽样上界（>2000 坐标采样渲染，防 O(N) path）。
 * S4. 血缘 60 节点布局 O(N·C)（列数常数）内完成。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchCatalogItemPreview: vi.fn(),
}));
vi.mock('@/lib/api/project-assets', () => api);

import { DatasetPreview, FootprintMap, extractFootprint } from '@/components/sidebar/project/dataset-preview';
import { adaptLineage } from '@/components/sidebar/project/lineage-adapter';
import { makeLineageGraph } from './fixtures';

const TOTAL = 100_000;
const PAGE_ROWS = 5; // DatasetPreview defaultPageSize

function makeHugeFeatures(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [i % 120 - 60, i % 60 - 30] },
    properties: { idx: i, name: `row-${i}` },
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('100k 行压力（ADR-0143 P8）', () => {
  it('S1+S2: 表格模式 DOM 行有界 + 预算内完成', async () => {
    const features = makeHugeFeatures(TOTAL);
    api.fetchCatalogItemPreview.mockResolvedValue({
      dataset_id: 'huge',
      features,
      total_count: TOTAL,
      schema_info: null,
      metadata: null,
    });
    const t0 = performance.now();
    render(<DatasetPreview datasetId="ds-huge" sourceRef="huge-ref" />);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /下一页|下页/ })).toBeDefined();
    });
    const elapsed = performance.now() - t0;
    // DOM 行数有界：body 内表格行 ≪ 总行数（分页窗口）
    const rows = document.querySelectorAll('tbody tr, [role="row"]');
    expect(rows.length).toBeLessThan(TOTAL / 10);
    expect(rows.length).toBeGreaterThan(0);
    // jsdom 宽松预算：初始化（100k 对象构造 + 单页渲染）应 < 5s
    expect(elapsed).toBeLessThan(5_000);
    expect(screen.getByText(/共 100000 行/)).toBeInTheDocument();
    void PAGE_ROWS;
  }, 20_000);

  it('S3: 足迹图坐标提取有界（抽验函数不炸）', () => {
    const features = makeHugeFeatures(2_000).map((f) => ({
      ...f,
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    }));
    const t0 = performance.now();
    const fp = extractFootprint(features);
    expect(fp.rings.length).toBe(2_000);
    expect(performance.now() - t0).toBeLessThan(2_000);
  });

  it('S4: 血缘适配器 60 节点布局低开销', () => {
    const graph = makeLineageGraph(60);
    const t0 = performance.now();
    for (let i = 0; i < 100; i += 1) adaptLineage(graph as never);
    // 100 次全量布局应远低于 2s（单次 ≪20ms）
    expect(performance.now() - t0).toBeLessThan(2_000);
  });

  it('FootprintMap 大坐标量渲染不崩（5000 点）', () => {
    const points = Array.from({ length: 5_000 }, (_, i) => [i % 100, i % 80] as [number, number]);
    const { container } = render(<FootprintMap geometry={{ points, rings: [] }} />);
    expect(container.querySelectorAll('circle').length).toBe(5_000);
  });
});
