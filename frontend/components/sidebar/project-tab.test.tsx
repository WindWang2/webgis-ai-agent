import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Selector-style store mock（同 layers/data-sources 测试的既有模式）：vi.mock
// 工厂只在渲染时才惰性读取 toastStore，hoisting 安全。
const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => any) => selector(toastStore),
}));

vi.mock('@/lib/api/project', () => ({
  fetchProjects: vi.fn(),
  createProject: vi.fn(),
  fetchProjectDatasets: vi.fn(),
  fetchProjectWorkflows: vi.fn(),
  runWorkflow: vi.fn(),
}));

// Import AFTER the mocks are registered so the component picks them up.
import { ProjectTab } from './project-tab';
import { fetchProjects, fetchProjectDatasets, fetchProjectWorkflows, runWorkflow } from '@/lib/api/project';
import type { Project, Workflow } from '@/lib/api/project';

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

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'wf1',
    project_id: 'p1',
    name: '洪水风险分析',
    version: 1,
    graph_spec: { steps: [{ step_id: 's1', tool_name: 'buffer' }] },
    inputs_schema: {},
    created_at: '2026-08-12T00:00:00Z',
    updated_at: '2026-08-12T00:00:00Z',
    ...overrides,
  };
}

describe('ProjectTab — 加载 / 错误 / 空态', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchProjects).mockResolvedValue([]);
    vi.mocked(fetchProjectDatasets).mockResolvedValue([]);
    vi.mocked(fetchProjectWorkflows).mockResolvedValue([]);
  });

  it('(a) 首屏加载渲染 LoadingState「加载项目…」', () => {
    vi.mocked(fetchProjects).mockImplementation(() => new Promise<Project[]>(() => {}));
    render(<ProjectTab />);
    expect(screen.getByText('加载项目…')).toBeInTheDocument();
    expect(screen.queryByText('暂无挂载数据集')).not.toBeInTheDocument();
  });

  it('(b) 项目列表加载失败渲染 InlineNotice 错误文本', async () => {
    vi.mocked(fetchProjects).mockRejectedValue(new Error('项目列表加载失败'));
    render(<ProjectTab />);
    expect(await screen.findByText('项目列表加载失败')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('(c) 空数据集与空工作流渲染 EmptyState 文案', async () => {
    vi.mocked(fetchProjects).mockResolvedValue([makeProject()]);
    render(<ProjectTab />);
    expect(await screen.findByText('暂无挂载数据集')).toBeInTheDocument();
    expect(screen.getByText('暂无已保存工作流')).toBeInTheDocument();
  });

  it('空数据集时不渲染数据集条目', async () => {
    vi.mocked(fetchProjects).mockResolvedValue([makeProject()]);
    render(<ProjectTab />);
    await screen.findByText('暂无挂载数据集');
    expect(screen.queryByText('道路中心线')).not.toBeInTheDocument();
  });
});

describe('ProjectTab — 工作流重新运行走 toast 而非 alert', () => {
  let alertSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchProjects).mockResolvedValue([makeProject()]);
    vi.mocked(fetchProjectDatasets).mockResolvedValue([]);
    vi.mocked(fetchProjectWorkflows).mockResolvedValue([makeWorkflow()]);
    vi.mocked(runWorkflow).mockResolvedValue({
      id: 'run1',
      workflow_id: 'wf1',
      workflow_version: 1,
      input_bindings: {},
      status: 'queued',
      execution_trace: [],
      outputs: {},
      cost_perf_summary: {},
      created_at: '2026-08-12T00:00:00Z',
    });
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    alertSpy.mockRestore();
  });

  it('(d) 点击「重新运行」触发成功 toast，且不调用 window.alert', async () => {
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: '重新运行' }));

    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalled());
    expect(toastStore.addToast).toHaveBeenCalledWith('工作流已成功触发重新运行', 'success');
    expect(runWorkflow).toHaveBeenCalledWith('p1', 'wf1');
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('运行失败时触发 error toast，且不调用 window.alert', async () => {
    vi.mocked(runWorkflow).mockRejectedValue(new Error('后端拒绝'));
    render(<ProjectTab />);
    fireEvent.click(await screen.findByRole('button', { name: '重新运行' }));

    await waitFor(() => expect(toastStore.addToast).toHaveBeenCalled());
    expect(toastStore.addToast).toHaveBeenCalledWith('重新运行失败：后端拒绝', 'error');
    expect(alertSpy).not.toHaveBeenCalled();
  });
});
