/**
 * 验收旅程（§5 门禁，api-stub 全端点三态路由驱动 —— msw 等价模式，ADR-0142 D4）：
 *
 * 旅程 A：查看集群 → 发现 stuck run → 干预动作（确认对话框）→ 查看回执
 *         （+ 任务中心联动）；另覆盖 权限 403 / 空集群 三态。
 * 旅程 B：校验 plan → 提交执行 → 看进度瀑布。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';
import { OpsConsole } from './ops-console';
import { installApiStub, type ApiStub } from '@/test/fixtures/api-stub';
import { installOpsRoutes } from '@/test/fixtures/ops-routes';
import { useHudStore } from '@/lib/store/useHudStore';
import { clusterWorkersFixture, planInvalidErrorFixture, samplePlan } from '@/test/fixtures/geocompute-fixtures';

vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  const line = ({ data }: { data?: unknown }) =>
    React.createElement('div', { 'data-testid': 'core-line-chart', 'data-points': Array.isArray(data) ? data.length : 0 });
  return {
    ResponsiveContainer: passthrough, BarChart: passthrough, LineChart: line, PieChart: passthrough,
    ScatterChart: passthrough, CartesianGrid: passthrough, XAxis: passthrough, YAxis: passthrough,
    Tooltip: passthrough, Legend: passthrough, Bar: passthrough, Line: passthrough, Pie: passthrough,
    Cell: passthrough, AreaChart: passthrough, Area: passthrough, RadialBarChart: passthrough,
    RadialBar: passthrough, RadarChart: passthrough, Radar: passthrough, PolarGrid: passthrough,
    PolarAngleAxis: passthrough, PolarRadiusAxis: passthrough, Scatter: passthrough,
  };
});

let stub: ApiStub;

function installOkRoutes() {
  stub = installApiStub();
  installOpsRoutes(stub);
}

function eventsCalls(): string[] {
  return stub.calls.filter((c) => c.path.includes('/events')).map((c) => c.path);
}

beforeEach(() => {
  useHudStore.setState({ activeLeftTab: 'ops', leftPanelOpen: true });
});

afterEach(() => {
  stub.restore();
});

describe('验收旅程 A：集群 → stuck → 干预 → 回执', () => {
  it('正常态：概览 + workers + stuck 列表全部流动', async () => {
    installOkRoutes();
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    expect(screen.getByTestId('ops-cluster-dashboard')).toBeInTheDocument();
    // 概览卡数据流动
    await waitFor(() => expect(screen.getByTestId('tile-队列深度')).toHaveTextContent('5'));
    expect(screen.getByTestId('tile-在飞')).toHaveTextContent('12');
    // workers 表
    await waitFor(() => expect(screen.getByTestId('ops-workers')).toBeInTheDocument());
    expect(screen.getByTestId(`worker-row-${clusterWorkersFixture.workers[0].worker_id}`)).toBeInTheDocument();
    // stuck run 发现
    expect(await screen.findByTestId('stuck-row-run-stuck-001')).toBeInTheDocument();
  });

  it('干预：重派 → 确认对话框 → 回执 → 任务中心联动', async () => {
    installOkRoutes();
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    fireEvent.click(await screen.findByTestId('requeue-run-stuck-001'));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('确认重派该 run？');
    fireEvent.click(within(dialog).getByRole('button', { name: '重派' }));
    // 回执 live region（结果回执 = toast 语义 + 任务中心入口）
    const receipts = await screen.findByTestId('stuck-receipts');
    await waitFor(() => expect(receipts).toHaveTextContent(/已重新入队：run-stuck-001/));
    fireEvent.click(within(receipts).getByRole('button', { name: /任务中心/ }));
    await waitFor(() => expect(useHudStore.getState().activeLeftTab).toBe('tasks'));
  });

  it('409 并发干预 → 警告回执而非错误崩溃', async () => {
    installOkRoutes();
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    fireEvent.click(await screen.findByTestId('evict-run-stuck-002'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '驱逐' }));
    await waitFor(() =>
      expect(screen.getByTestId('stuck-receipts')).toHaveTextContent(/已被并发处理/),
    );
  });

  it('权限态：403 → 需要管理员权限诚实态（非死按钮）', async () => {
    installOkRoutes();
    stub.mode('error');
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    await waitFor(() => expect(screen.getAllByText('需要管理员权限').length).toBeGreaterThan(0));
  });

  it('空集群：诚实零值 + 无卡住 run', async () => {
    installOkRoutes();
    stub.mode('empty');
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('tile-队列深度')).toHaveTextContent('0'));
    expect(await screen.findByText('当前无卡住 run')).toBeInTheDocument();
  });
});

describe('验收旅程 B：校验 plan → 提交执行 → 进度瀑布', () => {
  it('编辑 → 校验通过 → 提交集群 → 瀑布流动', async () => {
    installOkRoutes();
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);

    // 切到计划视图
    fireEvent.click(screen.getByTestId('ops-view-plan'));
    const editor = await screen.findByLabelText('计划 JSON');
    fireEvent.change(editor, { target: { value: JSON.stringify(samplePlan) } });

    // 校验
    fireEvent.click(screen.getByTestId('plan-validate'));
    expect(await screen.findByTestId('plan-validation-ok')).toHaveTextContent('分波执行 3 波');

    // 提交集群执行 → 进度视图
    fireEvent.click(screen.getByTestId('plan-submit'));
    expect(await screen.findByTestId('plan-progress')).toHaveTextContent('run-cluster-400');

    // 瀑布：事件页推导 clip_a 区间
    await waitFor(() => expect(screen.getByTestId('ops-waterfall')).toBeInTheDocument());
    expect(screen.getByRole('img', { name: /clip_a/ })).toBeInTheDocument();

    // 游标请求从 after_id=0 起步
    expect(eventsCalls().length).toBeGreaterThan(0);
    expect(eventsCalls()[0]).toContain('after_id=0');

    // 取消入口在
    expect(screen.getByTestId('plan-cancel-run')).toBeInTheDocument();
  });

  it('校验失败：错误定位到分区条目（error 态覆盖 422）', async () => {
    installOkRoutes();
    stub.on('POST', '/api/v1/geocompute/plans/validate', {
      ok: () => ({ status: 422, body: planInvalidErrorFixture.body }),
      empty: () => ({ status: 422, body: planInvalidErrorFixture.body }),
      error: () => ({ status: 422, body: planInvalidErrorFixture.body }),
    });
    render(<OpsConsole sessionId="sess-1" ownerToken="tok" />);
    fireEvent.click(screen.getByTestId('ops-view-plan'));
    const editor = await screen.findByLabelText('计划 JSON');
    fireEvent.change(editor, { target: { value: JSON.stringify(samplePlan) } });
    fireEvent.click(screen.getByTestId('plan-validate'));
    const err = await screen.findByTestId('plan-validation-error');
    expect(err).toHaveTextContent('merge_c');
    expect(err).toHaveTextContent('unknown input node: buffer_x');
  });
});
