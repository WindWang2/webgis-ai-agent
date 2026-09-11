/**
 * DataGcPanel 交互测试（ADR-0143 P6）。
 * 覆盖：用量配额条 / dry-run 计划树 / 危险确认执行 + 回执 / 保护跳过清单。
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

import { DataGcPanel } from '@/components/sidebar/project/gc-panel';
import { makeDataUsage, makeGcPlan, makeGcExecuteResponse } from './fixtures';

beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  api.fetchDataUsage.mockResolvedValue(makeDataUsage());
});

describe('DataGcPanel', () => {
  it('渲染用量配额（进度条 + 配额徽章 + 宽限期）', async () => {
    render(<DataGcPanel projectId="p1" authed />);
    expect(await screen.findByText(/存储用量/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '存储配额使用率' })).toBeInTheDocument();
    expect(screen.getByText(/配额内/)).toBeInTheDocument();
    expect(screen.getByText(/宽限 72 小时/)).toBeInTheDocument();
    expect(screen.getByText(/即将到期 7 修订/)).toBeInTheDocument();
  });

  it('生成 dry-run 计划：候选树 + 观察期 + 保护计数', async () => {
    api.planDataGc.mockResolvedValue(makeGcPlan());
    render(<DataGcPanel projectId="p1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /生成回收计划（dry-run）/ }));
    expect((await screen.findAllByText(/候选修订/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/art-9 · r3 · 41天/)).toBeInTheDocument();
    expect(screen.getByText(/提升存储观察期 72 小时/)).toBeInTheDocument();
    expect(screen.getByText(/pinned=3/)).toBeInTheDocument();
    expect(screen.getByText(/危险操作/)).toBeInTheDocument();
  });

  it('执行：两段确认（confirm:true 硬编码）+ 回执 + 保护跳过', async () => {
    api.planDataGc.mockResolvedValue(makeGcPlan());
    api.executeDataGc.mockResolvedValue(makeGcExecuteResponse());
    render(<DataGcPanel projectId="p1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /生成回收计划（dry-run）/ }));
    const arm = await screen.findByRole('button', { name: '执行回收' });
    fireEvent.click(arm);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认永久删除以上候选？' }));
    await waitFor(() => expect(api.executeDataGc).toHaveBeenCalledWith('p1'));
    expect(await screen.findByText(/回收回执/)).toBeInTheDocument();
    expect(screen.getByText(/保护跳过（1）/)).toBeInTheDocument();
    expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('回收完成：释放'), 'success');
  });

  it('保留策略禁用时如实提示', async () => {
    api.planDataGc.mockResolvedValue(
      makeGcPlan({ retention: { ...makeGcPlan().retention, disabled: true } }),
    );
    render(<DataGcPanel projectId="p1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /生成回收计划（dry-run）/ }));
    expect(await screen.findByText(/保留策略已禁用/)).toBeInTheDocument();
    expect(screen.queryByText(/危险操作/)).not.toBeInTheDocument();
  });

  it('匿名禁用执行', async () => {
    render(<DataGcPanel projectId="p1" authed={false} />);
    expect(await screen.findByText(/回收操作需要登录账号/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /生成回收计划（dry-run）/ })).toBeDisabled();
  });
});
