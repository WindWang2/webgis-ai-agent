/**
 * DatasetPreview 双模式测试（ADR-0143 P2）。
 * 覆盖：表格模式（TabularDataGrid 聚合）/ 地图足迹 SVG / 无 source_ref 降级 /
 * 错误面 / 主地图按钮按需渲染。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchCatalogItemPreview: vi.fn(),
}));
vi.mock('@/lib/api/project-assets', () => api);

import { DatasetPreview, FootprintMap } from '@/components/sidebar/project/dataset-preview';

const previewPayload = {
  dataset_id: 'cat-100',
  features: [
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, properties: {} },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [0.5, 0.5] }, properties: {} },
  ],
  total_count: 2,
  schema_info: null,
  metadata: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchCatalogItemPreview.mockResolvedValue(previewPayload);
});

describe('DatasetPreview', () => {
  it('表格模式渲染样例计数', async () => {
    render(<DatasetPreview datasetId="ds-1" sourceRef="cat-100" />);
    expect(await screen.findByText(/样例 2 \/ 共 2 行/)).toBeInTheDocument();
    expect(api.fetchCatalogItemPreview).toHaveBeenCalledWith('cat-100', expect.anything());
  });

  it('地图模式渲染足迹 SVG（真实几何 + a11y 概要）', async () => {
    render(<DatasetPreview datasetId="ds-1" sourceRef="cat-100" />);
    fireEvent.click(await screen.findByRole('tab', { name: /地图/ }));
    const svg = await screen.findByRole('img', { name: /预览要素足迹图/ });
    void 0;
    expect(svg.getAttribute('aria-label')).toMatch(/5 个坐标点/);
    expect(svg.querySelector('polyline')).toBeInTheDocument();
    expect(svg.querySelector('circle')).toBeInTheDocument();
  });

  it('无 source_ref 降级为提示且不请求', async () => {
    render(<DatasetPreview datasetId="ds-1" sourceRef={null} />);
    expect(await screen.findByText(/没有可预览的来源引用/)).toBeInTheDocument();
    expect(api.fetchCatalogItemPreview).not.toHaveBeenCalled();
  });

  it('请求失败展示错误（如 502 源不可达）', async () => {
    api.fetchCatalogItemPreview.mockRejectedValue(new Error('502'));
    render(<DatasetPreview datasetId="ds-1" sourceRef="cat-100" />);
    expect(await screen.findByText(/502/)).toBeInTheDocument();
  });

  it('onOpenInMap 缺省时不渲染主地图按钮', async () => {
    render(<DatasetPreview datasetId="ds-1" sourceRef="cat-100" />);
    await screen.findByText(/样例 2/);
    expect(screen.queryByRole('button', { name: '主地图' })).not.toBeInTheDocument();
  });
});

describe('FootprintMap', () => {
  it('无几何时诚实提示', () => {
    render(<FootprintMap geometry={{ points: [], rings: [] }} />);
    expect(screen.getByText(/不含几何坐标/)).toBeInTheDocument();
  });
});
