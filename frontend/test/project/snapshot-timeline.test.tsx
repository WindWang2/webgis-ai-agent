/**
 * SnapshotTimeline 交互测试（ADR-0143 P4）。
 * 覆盖：列表渲染 / 会话门控 / 核查报告 / restore verify+register /
 * 前端聚合 diff（契约：无后端 diff 端点）/ 删除两段确认。
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
  cloneWorkspaceSnapshot: vi.fn(),
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

import { SnapshotTimeline } from '@/components/sidebar/project/snapshot-timeline';
import { makeSnapshotList, makeVerification, makeRestoreResponse } from './fixtures';

beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  api.listWorkspaceSnapshots.mockResolvedValue(makeSnapshotList(3));
});

describe('SnapshotTimeline', () => {
  it('渲染时间线（标签/存活计数/上限提示）', async () => {
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    expect((await screen.findAllByText('交付前基线')).length).toBeGreaterThan(0);
    expect(screen.getByText(/快照 \(3/)).toBeInTheDocument();
    expect(screen.getAllByText(/产物 6/).length).toBeGreaterThan(0);
  });

  it('无会话上下文：保存/恢复禁用，列表可读', async () => {
    render(<SnapshotTimeline projectId="p1" sessionId={null} authed />);
    expect((await screen.findAllByText('交付前基线')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '保存快照' })).toBeDisabled();
    expect(screen.getByText(/保存\/恢复\/删除快照需要会话上下文/)).toBeInTheDocument();
  });

  it('保存快照：确认后调用 API（含 materialize）', async () => {
    api.saveWorkspaceSnapshot.mockResolvedValue({
      status: 'ok', project_id: 'p1', home: 'project', snapshot_id: 'snap-new',
      label: '测试快照', durable_pointers: 5, materialize_skipped: [], snapshot: {},
    });
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: '保存快照' }));
    fireEvent.change(screen.getByLabelText('标签（可选）'), { target: { value: '测试快照' } });
    fireEvent.change(screen.getByLabelText('物化策略'), { target: { value: 'all' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '全量物化可能耗时，确认？' }));
    await waitFor(() =>
      expect(api.saveWorkspaceSnapshot).toHaveBeenCalledWith('p1', {
        session_id: 's1', label: '测试快照', materialize: 'all',
      }),
    );
    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('快照已保存'), 'success'));
  });

  it('核查：展开验证报告（存活/缺失/integrity）', async () => {
    api.inspectWorkspaceSnapshot.mockResolvedValue(
      makeVerification({
        artifacts: { total: 6, live: 4, missing: ['art-x', 'art-y'] },
        integrity: { 'art-1': 'verified', 'art-9': 'digest_mismatch' },
      }),
    );
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /核查快照 交付前基线/ }));
    expect(await screen.findByText(/产物 4\/6 存活/)).toBeInTheDocument();
    expect(screen.getByText(/缺失产物 art-x/)).toBeInTheDocument();
    expect(screen.getByText(/art-9=digest_mismatch/)).toBeInTheDocument();
  });

  it('restore：verify 模式确认后调用且不写状态提示', async () => {
    api.restoreWorkspaceSnapshot.mockResolvedValue(makeRestoreResponse({ mode: 'verify' }));
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /恢复快照 交付前基线/ }));
    fireEvent.click(screen.getByRole('button', { name: '开始核查' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认核查？' }));
    await waitFor(() =>
      expect(api.restoreWorkspaceSnapshot).toHaveBeenCalledWith('p1', 'snap-1', {
        session_id: 's1', mode: 'verify',
      }),
    );
    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('未写入任何状态'), 'success'));
  });

  it('restore register：结果验证报告入缓存并 toast 成功', async () => {
    api.restoreWorkspaceSnapshot.mockResolvedValue(makeRestoreResponse({ mode: 'register' }));
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /恢复快照 交付前基线/ }));
    fireEvent.change(screen.getByLabelText('恢复模式'), { target: { value: 'register' } });
    fireEvent.click(screen.getByRole('button', { name: '执行恢复' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认覆盖当前变更？' }));
    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('已恢复'), 'success'));
  });

  it('聚合 diff：选 A/B 生成对比卡（前端聚合两份 verify 报告）', async () => {
    api.inspectWorkspaceSnapshot
      .mockResolvedValueOnce(makeVerification())
      .mockResolvedValueOnce(
        makeVerification({
          artifacts: { total: 8, live: 6, missing: ['art-new-missing'] },
          layers: { total: 6, live: 6, missing: [] },
        }),
      );
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    expect((await screen.findAllByText('交付前基线')).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('对比基准快照'), { target: { value: 'snap-1' } });
    fireEvent.change(screen.getByLabelText('对比目标快照'), { target: { value: 'snap-2' } });
    fireEvent.click(screen.getByRole('button', { name: '生成对比' }));
    expect(await screen.findByText(/产物 Δ/)).toBeInTheDocument();
    await waitFor(() => expect(api.inspectWorkspaceSnapshot).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/产物缺失变化：新增缺失 1/)).toBeInTheDocument();
    expect(screen.getByText(/图层 Δ/)).toBeInTheDocument();
  });

  it('删除两段确认后行移除', async () => {
    api.deleteWorkspaceSnapshot.mockResolvedValue({ status: 'deleted', snapshot_id: 'snap-1', home: 'project' });
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    const arms = await screen.findAllByRole('button', { name: '删除' });
    fireEvent.click(arms[0]);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getAllByRole('button', { name: '确认删除？' })[0]);
    await waitFor(() => expect(api.deleteWorkspaceSnapshot).toHaveBeenCalledWith('p1', 'snap-1', 's1'));
    await waitFor(() => expect(screen.queryByText('交付前基线')).not.toBeInTheDocument());
  });

  it('克隆快照：填目标会话后确认调用 clone API（spec P4）', async () => {
    api.cloneWorkspaceSnapshot.mockResolvedValue({ status: 'ok' });
    render(<SnapshotTimeline projectId="p1" sessionId="s1" authed />);
    const arms = await screen.findAllByRole('button', { name: /克隆快照 交付前基线/ });
    fireEvent.click(arms[0]);
    fireEvent.change(screen.getByLabelText(/目标会话 ID/), { target: { value: 'session-target' } });
    fireEvent.click(screen.getByRole('button', { name: '克隆' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认克隆？' }));
    await waitFor(() =>
      expect(api.cloneWorkspaceSnapshot).toHaveBeenCalledWith('p1', 'snap-1', {
        source_session_id: 's1',
        target_session_id: 'session-target',
      }),
    );
    await waitFor(() =>
      expect(toastStore.addToast).toHaveBeenCalledWith(expect.stringContaining('已克隆到会话'), 'success'),
    );
  });
});
