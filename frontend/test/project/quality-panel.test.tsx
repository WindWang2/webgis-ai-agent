/**
 * QualityPanel 交互测试（ADR-0143 P5）。
 * 覆盖：数据集选择 → 前端聚合取要素 → 审计报告 / 截断披露 /
 * 修复两段确认 + 回执 / 预览失败面 / 无 source_ref 降级。
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

import { QualityPanel } from '@/components/sidebar/project/quality-panel';
import { makeDatasetPage, makeQualityReport, makeRepairResponse } from './fixtures';

const previewPayload = {
  dataset_id: 'cat-100',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [1, 1] }, properties: {} },
  ],
  total_count: 2,
  schema_info: null,
  metadata: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  api.fetchProjectDatasetPage.mockResolvedValue(makeDatasetPage(1));
  api.fetchCatalogItemPreview.mockResolvedValue(previewPayload);
});

async function selectFirstDataset() {
  fireEvent.change(await screen.findByLabelText('审计范围（数据集）'), {
    target: { value: 'ds-1' },
  });
}

describe('QualityPanel', () => {
  it('审计：经 catalog 预览聚合要素并展示报告', async () => {
    api.auditSpatialQuality.mockResolvedValue(makeQualityReport());
    render(<QualityPanel projectId="p1" authed />);
    await selectFirstDataset();
    fireEvent.click(screen.getByRole('button', { name: /运行审计/ }));
    await waitFor(() => expect(api.auditSpatialQuality).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ type: 'FeatureCollection' }),
      expect.objectContaining({ signal: expect.anything() }),
    ));
    expect((await screen.findAllByText(/self_intersection/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/规则命中（2）/)).toBeInTheDocument();
  });

  it('截断披露', async () => {
    api.auditSpatialQuality.mockResolvedValue(
      makeQualityReport({ truncated: true, truncated_count: 321 }),
    );
    render(<QualityPanel projectId="p1" authed />);
    await selectFirstDataset();
    fireEvent.click(screen.getByRole('button', { name: /运行审计/ }));
    expect(await screen.findByText(/结果已截断：另有 321 条/)).toBeInTheDocument();
  });

  it('修复：两段确认后执行并展示回执', async () => {
    api.auditSpatialQuality.mockResolvedValue(makeQualityReport());
    api.repairQuality.mockResolvedValue(makeRepairResponse());
    render(<QualityPanel projectId="p1" authed />);
    await selectFirstDataset();
    fireEvent.click(screen.getByRole('button', { name: /运行审计/ }));
    fireEvent.click(await screen.findByRole('button', { name: '执行修复' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: /确认执行修复/ }));
    await waitFor(() =>
      expect(api.repairQuality).toHaveBeenCalledWith('p1', expect.objectContaining({
        dataset_id: 'ds-1',
        source_ref: 'cat-100',
      }), expect.anything()),
    );
    expect(await screen.findByText(/修复回执/)).toBeInTheDocument();
    expect(screen.getAllByText(/1240/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1239/).length).toBeGreaterThan(0);
    expect(screen.getByText(/血缘: recorded/)).toBeInTheDocument();
    await waitFor(() =>
      expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('修复完成'), 'success'),
    );
  });

  it('预览失败（源不可达）如实降级', async () => {
    api.fetchCatalogItemPreview.mockRejectedValue(new Error('502 upstream'));
    render(<QualityPanel projectId="p1" authed />);
    await selectFirstDataset();
    fireEvent.click(screen.getByRole('button', { name: /运行审计/ }));
    expect(await screen.findByText(/502 upstream/)).toBeInTheDocument();
    expect(api.auditSpatialQuality).not.toHaveBeenCalled();
  });

  it('匿名禁用审计', async () => {
    render(<QualityPanel projectId="p1" authed={false} />);
    await screen.findByText(/审计与修复需要登录账号/);
    expect(screen.getByRole('button', { name: /运行审计/ })).toBeDisabled();
  });
});
