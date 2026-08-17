import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

const api = vi.hoisted(() => ({
  fetchProjects: vi.fn(),
  fetchProjectDatasets: vi.fn(),
  fetchProjectWorkflows: vi.fn(),
  fetchWorkflowRuns: vi.fn(),
  fetchWorkflowRevisions: vi.fn(),
  fetchWorkflowRun: vi.fn(),
  fetchArtifactLineage: vi.fn(),
  fetchRunComparison: vi.fn(),
  replayWorkflowRun: vi.fn(),
  resumeWorkflowRun: vi.fn(),
  runWorkflow: vi.fn(),
  createProject: vi.fn(),
  invalidateProjectRunCaches: vi.fn(),
}));

vi.mock('@/lib/api/project', () => api);

// #528: 项目写路径（创建/重新运行/回放/续跑）登录门控 —— 默认已登录使既有
// 写路径用例不变；匿名用例显式置空 mockUser。
let mockUser: { id: string; username: string } | null = { id: 'u1', username: 'ops' };
vi.mock('@/lib/auth/tokenStore', () => ({
  getAuthUser: () => mockUser,
  subscribeAuth: () => () => {},
}));

import { ProjectTab } from './project-tab';
import { ApiError } from '@/lib/api/transport';
import type { Project, WorkflowSummary } from '@/lib/api/project';

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    name: '测试项目',
    status: 'active',
    metadata_json: {},
    created_at: '2026-08-12T00:00:00Z',
    updated_at: '2026-08-12T00:00:00Z',
    ...overrides,
  };
}

function makeWorkflow(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    id: 'wf1',
    project_id: 'p1',
    name: '洪水风险分析',
    version: 1,
    step_count: 1,
    created_at: '2026-08-12T00:00:00Z',
    updated_at: '2026-08-12T00:00:00Z',
    ...overrides,
  };
}

function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: 'run1',
    workflow_id: 'wf1',
    workflow_version: 1,
    project_id: 'p1',
    workflow_revision_id: 'rev1',
    input_bindings: {},
    input_dataset_fingerprints: { ds1: 'fp-ds' },
    status: 'failed',
    execution_trace: [],
    outputs: {},
    error_message: 'step 2 exploded',
    cost_perf_summary: { elapsed_ms: 12 },
    completed_steps: ['s1'],
    run_fingerprint: 'run-fp',
    run_manifest: {
      workflow_revision_id: 'rev1',
      graph_fingerprint: 'graph-fp',
      steps: [
        { step_id: 's1', tool_name: 'buffer', tool_version: '1.0', status: 'completed' },
        { step_id: 's2', tool_name: 'clip', status: 'failed' },
      ],
      artifacts: [
        { id: 'art1', producing_step: 's1', artifact_type: 'geojson', crs: null, content_fingerprint: 'cfp' },
      ],
    },
    created_at: '2026-08-12T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUser = { id: 'u1', username: 'ops' };
  api.fetchProjects.mockResolvedValue([]);
  api.fetchProjectDatasets.mockResolvedValue([]);
  api.fetchProjectWorkflows.mockResolvedValue([]);
  api.fetchWorkflowRuns.mockResolvedValue(page([]));
  api.fetchWorkflowRevisions.mockResolvedValue(page([]));
  api.fetchWorkflowRun.mockResolvedValue(makeRun());
});

describe('ProjectTab — 加载 / 错误 / 空态', () => {
  it('(a) 首屏加载渲染 LoadingState「加载项目…」', () => {
    api.fetchProjects.mockImplementation(() => new Promise<Project[]>(() => {}));
    render(<ProjectTab />);
    expect(screen.getByText('加载项目…')).toBeInTheDocument();
    expect(screen.queryByText('暂无挂载数据集')).not.toBeInTheDocument();
  });

  it('(b) 项目列表加载失败渲染 InlineNotice 错误文本', async () => {
    api.fetchProjects.mockRejectedValue(new Error('项目列表加载失败'));
    render(<ProjectTab />);
    expect(await screen.findByText('项目列表加载失败')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('(c) 空数据集与空工作流渲染 EmptyState 文案', async () => {
    api.fetchProjects.mockResolvedValue([makeProject()]);
    render(<ProjectTab />);
    expect(await screen.findByText('暂无挂载数据集')).toBeInTheDocument();
    expect(screen.getByText('暂无已保存工作流')).toBeInTheDocument();
  });

  it('空数据集时不渲染数据集条目', async () => {
    api.fetchProjects.mockResolvedValue([makeProject()]);
    render(<ProjectTab />);
    await screen.findByText('暂无挂载数据集');
    expect(screen.queryByText('道路中心线')).not.toBeInTheDocument();
  });
});

describe('ProjectTab — 工作流重新运行走 toast 而非 alert', () => {
  let alertSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    api.fetchProjects.mockResolvedValue([makeProject()]);
    api.fetchProjectDatasets.mockResolvedValue([]);
    api.fetchProjectWorkflows.mockResolvedValue([makeWorkflow()]);
    api.runWorkflow.mockResolvedValue(makeRun({ id: 'run-new', status: 'completed', error_message: null }));
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    alertSpy.mockRestore();
  });

  it('(d) 两段确认后「重新运行」触发状态诚实的 toast，且不调用 window.alert', async () => {
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: '重新运行' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认重新运行？' }));

    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalled());
    expect(toastStore.addToast).toHaveBeenCalledWith('运行结束，后端状态：已完成', 'success');
    expect(api.runWorkflow).toHaveBeenCalledWith('p1', 'wf1');
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('运行失败时触发 error toast，且不调用 window.alert', async () => {
    api.runWorkflow.mockRejectedValue(new Error('后端拒绝'));
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: '重新运行' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认重新运行？' }));

    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalled());
    expect(toastStore.addToast).toHaveBeenCalledWith('后端拒绝', 'error');
    expect(alertSpy).not.toHaveBeenCalled();
  });
});

describe('ProjectTab — run inspector / recovery', () => {
  beforeEach(() => {
    api.fetchProjects.mockResolvedValue([makeProject()]);
    api.fetchProjectDatasets.mockResolvedValue([
      {
        id: 'ds1',
        project_id: 'p1',
        name: '道路中心线',
        source_type: 'upload',
        crs: null,
        quality_status: 'valid',
        created_at: '2026-08-12T00:00:00Z',
      },
    ]);
    api.fetchProjectWorkflows.mockResolvedValue([makeWorkflow()]);
    api.fetchWorkflowRuns.mockResolvedValue(
      page([
        { id: 'run1', workflow_id: 'wf1', workflow_version: 1, status: 'failed', created_at: '' },
        { id: 'run0', workflow_id: 'wf1', workflow_version: 1, status: 'completed', created_at: '' },
      ]),
    );
    api.fetchWorkflowRevisions.mockResolvedValue(
      page([{ id: 'rev1', workflow_id: 'wf1', revision_no: 1, graph_fingerprint: 'graph-fp', created_at: '' }]),
    );
    api.fetchWorkflowRun.mockResolvedValue(makeRun());
  });

  async function openRun() {
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: /洪水风险分析/ }));
    fireEvent.click(await screen.findByRole('button', { name: /run1/ }));
    expect(await screen.findByText('step 2 exploded')).toBeInTheDocument();
  }

  it('shows unknown CRS instead of fabricating EPSG:4326', async () => {
    render(<ProjectTab />);
    expect(await screen.findByText(/未知/)).toBeInTheDocument();
    expect(screen.queryByText(/EPSG:4326/)).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /洪水风险分析/ }));
    fireEvent.click(await screen.findByRole('button', { name: /run1/ }));
    expect(await screen.findByText(/CRS 未知/)).toBeInTheDocument();
    expect(screen.queryByText(/EPSG:4326/)).not.toBeInTheDocument();
  });

  it('does not fetch lineage until the user asks', async () => {
    await openRun();
    expect(api.fetchArtifactLineage).not.toHaveBeenCalled();
    api.fetchArtifactLineage.mockResolvedValue({ artifact_id: 'art1', parents: [], consumers: [] });
    fireEvent.click(screen.getByRole('button', { name: '查看血统' }));
    await waitFor(() => expect(api.fetchArtifactLineage).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('无血统')).toBeInTheDocument();
  });

  it('replay exact posts exact and quotes backend status', async () => {
    api.replayWorkflowRun.mockResolvedValue(makeRun({ id: 'run2', status: 'completed', error_message: null }));
    await openRun();
    fireEvent.click(screen.getByRole('button', { name: '精确回放' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认精确回放？' }));
    await waitFor(() => expect(api.replayWorkflowRun).toHaveBeenCalledWith('p1', 'run1', 'exact'));
    expect(toastStore.addToast).toHaveBeenCalledWith('回放结束，后端状态：已完成', 'success');
  });

  it('replay latest posts latest', async () => {
    api.replayWorkflowRun.mockResolvedValue(makeRun({ id: 'run2', status: 'failed' }));
    await openRun();
    fireEvent.click(screen.getByLabelText(/最新修订回放/));
    fireEvent.click(screen.getByRole('button', { name: '最新修订回放' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认最新修订回放？' }));
    await waitFor(() => expect(api.replayWorkflowRun).toHaveBeenCalledWith('p1', 'run1', 'latest'));
    expect(toastStore.addToast).toHaveBeenCalledWith('回放结束，后端状态：失败', 'error');
  });

  it('resume 409 surfaces the backend reason and does not toast success', async () => {
    api.resumeWorkflowRun.mockRejectedValue(
      new ApiError(409, 'Conflict', { detail: 'cannot resume run run1: input dataset fingerprints changed' }),
    );
    await openRun();
    fireEvent.click(screen.getByRole('button', { name: '尝试续跑' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认从已完成步骤续跑？' }));
    expect(await screen.findByText(/input dataset fingerprints changed/)).toBeInTheDocument();
    expect(toastStore.addToast).not.toHaveBeenCalledWith(expect.stringMatching(/成功/), expect.anything());
  });

  it('compare renders backend fingerprint.same and does not infer equality', async () => {
    api.fetchRunComparison.mockResolvedValue({
      run_a_id: 'run1',
      run_b_id: 'run0',
      revision: { graph_same: false, run_a_revision: 'rev1', run_b_revision: 'rev2' },
      inputs_changed: { diff_keys: [] },
      dataset_versions_changed: { diff_keys: ['ds1'] },
      tool_versions_changed: {},
      params_changed: {},
      output_artifacts_changed: {},
      metrics_changed: {},
      warnings_changed: {},
      run_fingerprint: { run_a: 'aaa', run_b: 'bbb', same: false },
    });
    await openRun();
    fireEvent.change(screen.getByLabelText('对比运行'), { target: { value: 'run0' } });
    fireEvent.click(screen.getByRole('button', { name: '对比' }));
    expect(await screen.findByText('后端判定运行指纹不相同')).toBeInTheDocument();
    expect(screen.getByText('数据集版本')).toBeInTheDocument();
    expect(screen.queryByText('后端判定运行指纹相同')).not.toBeInTheDocument();
  });

  it('back is keyboard accessible', async () => {
    await openRun();
    const back = screen.getByRole('button', { name: '返回运行列表' });
    back.focus();
    expect(back).toHaveFocus();
    fireEvent.click(back);
    expect(await screen.findByText('不可变修订')).toBeInTheDocument();
  });
});

// #528: 匿名用户的项目写路径门控 —— 后端 #501 已强制认证，前端补 #469 式
// 门控：禁用写控件并给出可见登录提示，而不是裸 401 toast。
describe('ProjectTab — 匿名登录门控 (#528)', () => {
  beforeEach(() => {
    mockUser = null;
    api.fetchProjects.mockResolvedValue([makeProject()]);
    api.fetchWorkflowRuns.mockResolvedValue(page([]));
    api.fetchWorkflowRevisions.mockResolvedValue(page([]));
    api.fetchProjectWorkflows.mockResolvedValue([makeWorkflow()]);
  });

  it('匿名时新建项目 toggle 禁用、登录提示可见；恢复登录后可用', async () => {
    api.fetchProjectDatasets.mockResolvedValue([]);
    render(<ProjectTab />);
    await screen.findByText('暂无挂载数据集');
    // 可见的登录引导（不是只有 title tooltip）
    expect(screen.getByText(/设置 → 账户 登录/)).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: '新建项目' });
    expect(toggle).toBeDisabled();
    // 禁用 toggle 打不开创建表单（创建按钮因此对匿名用户不可达）
    fireEvent.click(toggle);
    expect(screen.queryByRole('button', { name: '创建项目' })).not.toBeInTheDocument();
  });

  it('匿名时重新运行禁用，点击不触发 runWorkflow', async () => {
    api.fetchProjectDatasets.mockResolvedValue([]);
    render(<ProjectTab />);
    const rerun = await screen.findByRole('button', { name: '重新运行' });
    expect(rerun).toBeDisabled();
    fireEvent.click(rerun);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    expect(api.runWorkflow).not.toHaveBeenCalled();
  });

  it('匿名时 run inspector 内回放/续跑禁用（RunInspector → RecoveryActions 门控）', async () => {
    api.fetchProjectDatasets.mockResolvedValue([]);
    api.fetchWorkflowRuns.mockResolvedValue(
      page([{ id: 'run1', workflow_id: 'wf1', workflow_version: 1, status: 'failed', created_at: '' }]),
    );
    api.fetchWorkflowRun.mockResolvedValue(makeRun());
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: /洪水风险分析/ }));
    fireEvent.click(await screen.findByRole('button', { name: /run1/ }));
    const replay = await screen.findByRole('button', { name: '精确回放' });
    expect(replay).toBeDisabled();
    expect(screen.getByRole('button', { name: '尝试续跑' })).toBeDisabled();
    expect(api.replayWorkflowRun).not.toHaveBeenCalled();
    expect(api.resumeWorkflowRun).not.toHaveBeenCalled();
  });
});
