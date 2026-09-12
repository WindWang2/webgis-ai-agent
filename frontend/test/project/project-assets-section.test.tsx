/**
 * ProjectAssetsSection 测试（ADR-0143 P7）。
 * 覆盖：tab 切换（受控）/ 空项目提示 / 面板挂载正确性。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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

import { ProjectAssetsSection } from '@/components/sidebar/project/project-assets-section';
import { makeDatasetPage, makeArtifactPage, makeSnapshotList, makeDataUsage } from './fixtures';

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchProjectDatasetPage.mockResolvedValue(makeDatasetPage(1));
  api.fetchProjectArtifacts.mockResolvedValue(makeArtifactPage(1));
  api.listWorkspaceSnapshots.mockResolvedValue(makeSnapshotList(1));
  api.fetchDataUsage.mockResolvedValue(makeDataUsage());
});

function Section(props: Partial<Parameters<typeof ProjectAssetsSection>[0]> = {}) {
  return render(
    <ProjectAssetsSection
      projectId="p1"
      sessionId="s1"
      authed
      tab="datasets"
      onTabChange={vi.fn()}
      {...props}
    />,
  );
}

describe('ProjectAssetsSection', () => {
  it('渲染五个资产页签（append-only tab 条）', () => {
    Section();
    for (const label of ['数据集', '产物', '快照', '质量', '回收']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
  });

  it('tab 受控切换：数据集 → 回收（各面板按 tab 挂载）', () => {
    const onTabChange = vi.fn();
    const { rerender } = Section({ onTabChange });
    fireEvent.click(screen.getByRole('tab', { name: '回收' }));
    expect(onTabChange).toHaveBeenCalledWith('gc');
    rerender(
      <ProjectAssetsSection
        projectId="p1"
        sessionId="s1"
        authed
        tab="gc"
        onTabChange={onTabChange}
      />,
    );
    expect(screen.getByRole('tabpanel', { name: '回收' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /生成回收计划（dry-run）/ })).toBeInTheDocument();
  });

  it('空项目：提示先选项目', () => {
    Section({ projectId: '' });
    expect(screen.getByText(/选择项目后可管理/)).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('质量页签挂载 QualityPanel', () => {
    Section({ tab: 'quality' });
    expect(screen.getByRole('tabpanel', { name: '质量' })).toBeInTheDocument();
    expect(screen.getByText(/质量审计与修复/)).toBeInTheDocument();
  });
});
