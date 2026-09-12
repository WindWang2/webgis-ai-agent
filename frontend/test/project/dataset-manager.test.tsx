/**
 * DatasetManager 交互测试（ADR-0143 P2）。
 * 覆盖：列表渲染 / 登录门控 / attach 流程 / 两段确认解绑 / schema 捕获 /
 * 预览聚合（表格足迹）/ 加载失败态。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

const api = vi.hoisted(() => ({
  attachDataset: vi.fn(),
  auditSpatialQuality: vi.fn(),
  cloneArtifact: vi.fn(),
  deleteWorkspaceSnapshot: vi.fn(),
  detachDataset: vi.fn(),
  executeDataGc: vi.fn(),
  fetchCatalogItemPreview: vi.fn(),
  fetchDataUsage: vi.fn(),
  fetchProjectArtifacts: vi.fn(),
  fetchProjectDatasetPage: vi.fn(),
  inspectWorkspaceSnapshot: vi.fn(),
  listWorkspaceSnapshots: vi.fn(),
  pinArtifact: vi.fn(),
  planDataGc: vi.fn(),
  repairQuality: vi.fn(),
  restoreWorkspaceSnapshot: vi.fn(),
  saveWorkspaceSnapshot: vi.fn(),
  unpinArtifact: vi.fn(),
}));
const projectApi = vi.hoisted(() => ({
  fetchArtifactLineage: vi.fn(),
  fetchProjectDatasets: vi.fn(),
  fetchProjects: vi.fn(),
  fetchProjectWorkflows: vi.fn(),
  fetchWorkflowRuns: vi.fn(),
  fetchWorkflowRun: vi.fn(),
  fetchWorkflowRevisions: vi.fn(),
  fetchRunComparison: vi.fn(),
  compareRuns: vi.fn(),
  runWorkflow: vi.fn(),
  replayWorkflowRun: vi.fn(),
  resumeWorkflowRun: vi.fn(),
  createProject: vi.fn(),
  auditQuality: vi.fn(),
  invalidateProjectRunCaches: vi.fn(),
}));

vi.mock('@/lib/api/project-assets', () => api);
vi.mock('@/lib/api/project', () => projectApi);

import { DatasetManager } from '@/components/sidebar/project/dataset-manager';
import {
  makeAttachedDataset,
  makeDatasetPage,
  emptyPage,
} from './fixtures';


beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  api.fetchProjectDatasetPage.mockResolvedValue(makeDatasetPage(2));
  api.fetchCatalogItemPreview.mockResolvedValue({
    dataset_id: 'cat-100',
    features: [
      { type: 'Feature', geometry: { type: 'Point', coordinates: [116.4, 39.9] }, properties: { population: 88 } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [116.5, 39.8] }, properties: { population: 120 } },
    ],
    total_count: 2,
    schema_info: null,
    metadata: null,
  });
  api.attachDataset.mockResolvedValue(makeAttachedDataset({ id: 'ds-new', name: '新数据集' }));
  api.detachDataset.mockResolvedValue({ status: 'success', message: 'detached' });
});

describe('DatasetManager', () => {
  it('渲染数据集行与总数', async () => {
    render(<DatasetManager projectId="p1" authed />);
    expect(await screen.findByText('数据集 1')).toBeInTheDocument();
    expect(screen.getByText('数据集 2')).toBeInTheDocument();
    expect(screen.getByText('数据集 (2)')).toBeInTheDocument();
  });

  it('匿名时挂载按钮禁用，列表仍可读', async () => {
    render(<DatasetManager projectId="p1" authed={false} />);
    expect(await screen.findByText('数据集 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '挂载数据集' })).toBeDisabled();
    expect(screen.getByText(/挂载\/解绑需要登录账号/)).toBeInTheDocument();
  });

  it('attach 流程：表单提交调用 API 并捕获 schema、展示新行', async () => {
    render(<DatasetManager projectId="p1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: '挂载数据集' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新数据集' } });
    fireEvent.change(screen.getByLabelText(/来源引用/), { target: { value: 'cat-77' } });
    fireEvent.click(screen.getByRole('button', { name: '确认挂载' }));

    await waitFor(() => expect(api.attachDataset).toHaveBeenCalledWith('p1', {
      name: '新数据集',
      source_type: 'layer',
      source_ref: 'cat-77',
      crs: undefined,
    }));
    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalledWith('已挂载数据集 新数据集', 'success'));
    expect(await screen.findByText('新数据集')).toBeInTheDocument();
  });

  it('两段确认后解绑调用 API 且行移除', async () => {
    render(<DatasetManager projectId="p1" authed />);
    const arm = await screen.findAllByRole('button', { name: '解绑' });
    fireEvent.click(arm[0]);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认解绑？' }));
    await waitFor(() => expect(api.detachDataset).toHaveBeenCalledWith('p1', 'ds-1'));
    await waitFor(() => expect(screen.queryByText('数据集 1')).not.toBeInTheDocument());
    expect(toastStore.addToast).toHaveBeenCalledWith('已解绑数据集 数据集 1', 'success');
  });

  it('展开详情渲染预览（表格样例计数来自 catalog 聚合）', async () => {
    render(<DatasetManager projectId="p1" authed />);
    fireEvent.click(await screen.findByText('数据集 1'));
    expect(await screen.findByText(/样例 2 \/ 共 2 行/)).toBeInTheDocument();
  });

  it('加载失败展示错误面（错误消息透传）', async () => {
    api.fetchProjectDatasetPage.mockRejectedValue(new Error('boom'));
    render(<DatasetManager projectId="p1" authed />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it('空列表渲染空态', async () => {
    api.fetchProjectDatasetPage.mockResolvedValue(emptyPage());
    render(<DatasetManager projectId="p1" authed />);
    expect(await screen.findByText('暂无挂载数据集')).toBeInTheDocument();
  });
});
