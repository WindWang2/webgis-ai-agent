import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { JobView } from '@/lib/api/jobs';
import { TasksTab } from './tasks-tab';

// 只 mock 数据 hook：渲染、状态映射、取消 UX 是被测逻辑本身。
const hook = vi.hoisted(() => ({ useJobCenter: vi.fn() }));

vi.mock('@/lib/hooks/use-job-center', () => ({
  useJobCenter: hook.useJobCenter,
}));

const downloadWithAuth = vi.hoisted(() => ({ downloadWithAuth: vi.fn() }));
vi.mock('@/lib/api/authenticated-download', () => ({
  downloadWithAuth: downloadWithAuth.downloadWithAuth,
}));

function job(overrides: Partial<JobView> = {}): JobView {
  return {
    id: 'job-1',
    kind: 'analysis',
    name: 'NDVI 植被指数分析',
    status: 'running',
    progress: 42,
    message: '计算中',
    cancellable: true,
    retryable: false,
    active: true,
    attempt: 1,
    session_id: 'sess-a',
    project_id: null,
    agent_task_id: null,
    agent_step_id: null,
    background_job_ids: [],
    error: null,
    result_ref: null,
    step_count: 0,
    created_at: '2026-08-12T00:00:00Z',
    started_at: '2026-08-12T00:00:00Z',
    finished_at: null,
    cancel_requested_at: null,
    ...overrides,
  };
}

const cancel = vi.fn();
const retry = vi.fn();
const refresh = vi.fn();

function mockCenter(jobs: JobView[], cancelling: string[] = [], error: string | null = null) {
  hook.useJobCenter.mockReturnValue({
    jobs,
    loading: false,
    error,
    hasActive: jobs.some((j) => j.active),
    cancelling: new Set(cancelling),
    refresh,
    cancel,
    retry,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockCenter([]);
});

describe('TasksTab — 渲染', () => {
  it('空状态提示随 session 存在性变化', () => {
    const { rerender } = render(<TasksTab sessionId={null} />);
    expect(screen.getByText('开始一次对话后即可查看任务')).toBeInTheDocument();

    rerender(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('暂无后台任务')).toBeInTheDocument();
  });

  it('显示任务名、类型、进度与消息', () => {
    mockCenter([job()]);
    render(<TasksTab sessionId="sess-a" />);

    expect(screen.getByText('NDVI 植被指数分析')).toBeInTheDocument();
    expect(screen.getByText(/空间分析/)).toBeInTheDocument();
    expect(screen.getByText('计算中')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();

    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });

  it('progress=null 显示为不确定进度，而不是假百分比', () => {
    mockCenter([job({ progress: null, message: '排队中' })]);
    render(<TasksTab sessionId="sess-a" />);

    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.getByTestId('job-indeterminate-job-1')).toBeInTheDocument();
  });

  it('显示 attempt 与来源步骤（Turn → Step → Job 链）', () => {
    mockCenter([job({ attempt: 2, agent_step_id: 'step-3' })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText(/第 2 次尝试/)).toBeInTheDocument();
    expect(screen.getByText(/来自 step-3/)).toBeInTheDocument();
  });

  it('展示单行错误摘要', () => {
    mockCenter([
      job({ status: 'failed', active: false, cancellable: false, error: 'ValueError: invalid CRS' }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('ValueError: invalid CRS')).toBeInTheDocument();
  });

  it('活跃与已结束任务分组展示', () => {
    mockCenter([job({ id: 'a' }), job({ id: 'b', status: 'completed', active: false })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('已结束')).toBeInTheDocument();
    expect(screen.getByTestId('job-card-a')).toBeInTheDocument();
    expect(screen.getByTestId('job-card-b')).toBeInTheDocument();
  });

  it('展示错误横幅', () => {
    mockCenter([], [], '任务中心请求失败');
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('任务中心请求失败')).toBeInTheDocument();
  });
});

describe('TasksTab — 状态标签映射（规范 §29）', () => {
  // 状态标签来自 shared StatusBadge 的默认中文映射（UI V3 收敛后单一事实来源）
  const cases: Array<[JobView['status'], string, boolean]> = [
    ['pending', '等待中', true],
    ['queued', '排队中', true],
    ['running', '运行中', true],
    ['cancelling', '取消中', true],
    ['completed', '已完成', false],
    ['failed', '失败', false],
    ['cancelled', '已取消', false],
    ['stale', '已过期', false],
  ];

  it.each(cases)('%s → %s', (status, label, active) => {
    mockCenter([job({ status, active, cancellable: active && status !== 'cancelling' })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe('TasksTab — 取消 UX（规范 §30）', () => {
  it('点击取消调用 cancel', () => {
    mockCenter([job()]);
    render(<TasksTab sessionId="sess-a" />);
    fireEvent.click(screen.getByRole('button', { name: /取消 NDVI/ }));
    expect(cancel).toHaveBeenCalledWith('job-1');
  });

  it('取消进行中时显示「取消中」且按钮禁用，绝不直接显示已取消', () => {
    mockCenter([job()], ['job-1']);
    render(<TasksTab sessionId="sess-a" />);

    expect(screen.getByText('取消中')).toBeInTheDocument();
    expect(screen.queryByText('已取消')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /取消 NDVI/ })).toBeDisabled();
  });

  it('后端确认终态后才显示已取消', () => {
    mockCenter([job({ status: 'cancelled', active: false, cancellable: false })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('已取消')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /取消 NDVI/ })).not.toBeInTheDocument();
  });

  it('不可取消的任务不显示取消按钮', () => {
    mockCenter([job({ status: 'completed', active: false, cancellable: false })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.queryByRole('button', { name: /取消/ })).not.toBeInTheDocument();
  });
});

describe('TasksTab — 重试', () => {
  it('可重试任务显示重试按钮并调用 retry', () => {
    mockCenter([
      job({ status: 'failed', active: false, cancellable: false, retryable: true }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    fireEvent.click(screen.getByRole('button', { name: /重试 NDVI/ }));
    expect(retry).toHaveBeenCalledWith('job-1');
  });

  it('已取消任务不显示重试按钮（取消绝不重试）', () => {
    mockCenter([
      job({ status: 'cancelled', active: false, cancellable: false, retryable: false }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.queryByRole('button', { name: /重试/ })).not.toBeInTheDocument();
  });
});

describe('TasksTab — 结果与刷新', () => {
  it('终态任务展示结果文件名', () => {
    mockCenter([
      job({
        status: 'completed',
        active: false,
        cancellable: false,
        result_ref: '/data/analysis_results/NDVI_123.tif',
      }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('NDVI_123.tif')).toBeInTheDocument();
  });

  it('点击刷新调用 refresh', () => {
    mockCenter([]);
    render(<TasksTab sessionId="sess-a" />);
    fireEvent.click(screen.getByRole('button', { name: '刷新任务' }));
    expect(refresh).toHaveBeenCalled();
  });

  it('把 session 与 ownerToken 传给数据 hook', () => {
    mockCenter([]);
    render(<TasksTab sessionId="sess-a" ownerToken="tok-a" />);
    // enabled 跟随面板开合（context panel 收起时停止轮询），默认展开为 true
    expect(hook.useJobCenter).toHaveBeenCalledWith({
      sessionId: 'sess-a',
      ownerToken: 'tok-a',
      enabled: true,
    });
  });
});

// ── 批量图册（atlas_export）：页级状态与产物下载 ─────────────────────────
describe('TasksTab — 批量图册任务', () => {
  it('渲染 atlas_export 类型标签与逐页状态（成功/失败并存）', () => {
    mockCenter([
      job({
        id: 'job-atlas',
        kind: 'atlas_export',
        name: '批量图册导出 · 成都小学图册',
        status: 'failed',
        progress: 72,
        active: false,
        page_status: [
          { page_id: 'cover', title: '', page_number: 1, status: 'rendered', error: null },
          { page_id: 'cat_锦江区', title: '锦江区', page_number: 2, status: 'rendered', error: null },
          { page_id: 'cat_金牛区', title: '金牛区', page_number: 3, status: 'failed', error: 'atlas_page_skipped: page cat_金牛区: ValueError' },
        ],
      }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.getByText('批量图册')).toBeInTheDocument();
    const list = screen.getByTestId('atlas-pages-job-atlas');
    expect(list).toBeInTheDocument();
    expect(screen.getByText(/1\. /)).toBeInTheDocument();
    expect(screen.getByText(/2\. 锦江区/)).toBeInTheDocument();
    expect(screen.getByText(/3\. 金牛区/)).toBeInTheDocument();
    expect(screen.getAllByText('✓ 已渲染').length).toBe(2);
    expect(screen.getByText('✗ 失败')).toBeInTheDocument();
    // 失败页证据以 title 悬浮披露
    expect(screen.getByTitle('atlas_page_skipped: page cat_金牛区: ValueError')).toBeInTheDocument();
  });

  it('完成的图册任务提供 PDF 与来源清单下载（认证下载通道）', async () => {
    mockCenter([
      job({
        id: 'job-atlas-done',
        kind: 'atlas_export',
        name: '批量图册导出 · 图册',
        status: 'completed',
        progress: 100,
        active: false,
        cancellable: false,
        result_ref: 'atlas_9_1730000000.pdf',
        page_status: [
          { page_id: 'cover', title: '', page_number: 1, status: 'rendered', error: null },
        ],
      }),
    ]);
    render(<TasksTab sessionId="sess-a" />);
    fireEvent.click(screen.getByRole('button', { name: /下载图册 PDF/ }));
    expect(downloadWithAuth.downloadWithAuth).toHaveBeenCalledWith(
      '/api/v1/export/download/atlas_9_1730000000.pdf',
      { filename: 'atlas_9_1730000000.pdf' },
    );
    // 来源清单 = 同 stem 的 .manifest.json
    fireEvent.click(screen.getByRole('button', { name: /下载可复现来源清单/ }));
    expect(downloadWithAuth.downloadWithAuth).toHaveBeenCalledWith(
      '/api/v1/export/download/atlas_9_1730000000.manifest.json',
      { filename: 'atlas_9_1730000000.manifest.json' },
    );
  });

  it('非 atlas 任务不渲染页级状态区', () => {
    mockCenter([job({ kind: 'analysis' })]);
    render(<TasksTab sessionId="sess-a" />);
    expect(screen.queryByTestId('atlas-pages-job-1')).not.toBeInTheDocument();
  });
});
