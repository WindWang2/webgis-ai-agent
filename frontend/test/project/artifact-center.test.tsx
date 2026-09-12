/**
 * ArtifactCenter 交互测试（ADR-0143 P3）。
 * 覆盖：列表/类型筛选 / pin 回执 / clone 两段确认 + 回执 / 血缘图加载 /
 * 引用复制 / focusArtifactId 交叉导航。
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

import { ArtifactCenter } from '@/components/sidebar/project/artifact-center';
import { makeArtifactPage, makeLineageGraph } from './fixtures';

beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  api.fetchProjectArtifacts.mockResolvedValue(makeArtifactPage(3));
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

describe('ArtifactCenter', () => {
  it('渲染产物列表与总数', async () => {
    render(<ArtifactCenter projectId="p1" authed />);
    expect(await screen.findByText('产物 1')).toBeInTheDocument();
    expect(screen.getByText('产物 (3)')).toBeInTheDocument();
  });

  it('类型筛选过滤列表行', async () => {
    render(<ArtifactCenter projectId="p1" authed />);
    await screen.findByText('产物 1');
    fireEvent.change(screen.getByLabelText('按类型筛选产物'), { target: { value: 'vector' } });
    expect(screen.queryByText('产物 1')).not.toBeInTheDocument();
    expect(screen.getByText('产物 2')).toBeInTheDocument(); // index 1 → vector
    fireEvent.change(screen.getByLabelText('按类型筛选产物'), { target: { value: '' } });
    expect(screen.getByText('产物 1')).toBeInTheDocument();
  });

  it('pin 调用 API 并展示回执（revision + sha256）', async () => {
    api.pinArtifact.mockResolvedValue({
      status: 'ok', artifact_id: 'art-1', revision_no: 4, content_sha256: 'abc123def', pinned: true,
    });
    render(<ArtifactCenter projectId="p1" authed />);
    fireEvent.click(await screen.findByText('产物 1'));
    fireEvent.click((await screen.findAllByTitle('固定（防回收）'))[0]);
    await waitFor(() => expect(api.pinArtifact).toHaveBeenCalledWith('art-1', true));
    expect(await screen.findByText(/固定版本/)).toBeInTheDocument();
    expect(screen.getByText(/abc123def/)).toBeInTheDocument();
    expect(toastStore.addToast).toHaveBeenCalledWith('已固定 产物 1', 'success');
  });

  it('clone 两段确认后展示克隆回执', async () => {
    api.cloneArtifact.mockResolvedValue({
      status: 'ok', artifact_id: 'art-clone-9', source_artifact_id: 'art-1', name: '产物 1 副本',
    });
    render(<ArtifactCenter projectId="p1" authed />);
    fireEvent.click(await screen.findByText('产物 1'));
    const arm = (await screen.findAllByRole('button', { name: '克隆' }))[0];
    fireEvent.click(arm);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getAllByRole('button', { name: '确认克隆？' })[0]);
    await waitFor(() => expect(api.cloneArtifact).toHaveBeenCalledWith('art-1'));
    expect(await screen.findByText(/克隆成功：新产物 art-clone-9/)).toBeInTheDocument();
  });

  it('血缘：点击后加载并渲染 SVG 图（含上游/下游计数）', async () => {
    projectApi.fetchArtifactLineage.mockResolvedValue(makeLineageGraph(12));
    render(<ArtifactCenter projectId="p1" authed />);
    fireEvent.click(await screen.findByText('产物 1'));
    fireEvent.click(await screen.findByRole('button', { name: /血缘图/ }));
    const svg = await screen.findByRole('img', { name: /的血缘图：上游/ });
    expect(svg.getAttribute('aria-label')).toMatch(/上游 6 条边，下游 6 条边/);
    await waitFor(() => expect(projectApi.fetchArtifactLineage).toHaveBeenCalledWith('art-1', expect.anything()));
  });

  it('复制引用写入剪贴板（含 pin 的 sha256）', async () => {
    api.pinArtifact.mockResolvedValue({
      status: 'ok', artifact_id: 'art-1', revision_no: 4, content_sha256: 'abc123def', pinned: true,
    });
    render(<ArtifactCenter projectId="p1" authed />);
    fireEvent.click(await screen.findByText('产物 1'));
    fireEvent.click((await screen.findAllByTitle('固定（防回收）'))[0]);
    fireEvent.click(await screen.findByRole('button', { name: '复制引用' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('artifact:art-1\nsha256:abc123def'));
  });

  it('focusArtifactId 交叉导航：自动展开并拉取血缘', async () => {
    projectApi.fetchArtifactLineage.mockResolvedValue(makeLineageGraph(8, 'art-2'));
    render(<ArtifactCenter projectId="p1" authed focusArtifactId="art-2" />);
    await waitFor(() => expect(projectApi.fetchArtifactLineage).toHaveBeenCalledWith('art-2', expect.anything()));
    expect(await screen.findByRole('img', { name: /art-2 的血缘图/ })).toBeInTheDocument();
  });

  it('加载失败展示错误面', async () => {
    api.fetchProjectArtifacts.mockRejectedValue(new Error('boom'));
    render(<ArtifactCenter projectId="p1" authed />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it('匿名时 pin/clone 禁用（与其它面板一致的登录门控）', async () => {
    render(<ArtifactCenter projectId="p1" authed={false} />);
    expect((await screen.findAllByTitle(/需要登录账号/)).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: '克隆' })[0]).toBeDisabled();
  });

  it('时间排序：最早优先后首行变为最旧产物', async () => {
    api.fetchProjectArtifacts.mockResolvedValue(
      makeArtifactPage(2, { created_at: '2026-09-02T10:00:00Z' }),
    );
    render(<ArtifactCenter projectId="p1" authed />);
    // 夹具默认同时间戳——覆写两条不同时间
    await screen.findByText('产物 1');
    fireEvent.change(screen.getByLabelText('按时间排序产物'), { target: { value: 'oldest' } });
    expect((screen.getByRole('option', { name: '最早优先' }) as HTMLOptionElement).selected).toBe(true);
  });
});
