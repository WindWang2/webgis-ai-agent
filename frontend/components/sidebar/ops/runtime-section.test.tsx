/**
 * 运行时分区测试（P1 —— runtime-inspector.tsx 复活验收，§5 门禁第一条）：
 * 「原本只被测试引用的组件出现在真实 UI 中且数据可流动」。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { RuntimeSection } from './runtime-section';
import {
  workflowInstanceRowsFixture,
  workflowInstanceFixture,
} from '@/test/fixtures/workflow-runtime-fixtures';

function stubFetch(routes: Array<{ method: string; path: string; status: number; body: unknown }>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    // 精确匹配优先，其次前缀匹配（instances 与 instances/{id} 共存）
    const route =
      routes.find((r) => r.method === method && url.pathname === r.path) ??
      routes.find((r) => r.method === method && url.pathname.startsWith(r.path));
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function baseRoutes() {
  return [
    {
      method: 'GET',
      path: '/api/v1/workflow-runtime/instances/wi-ops-1',
      status: 200,
      body: { success: true, instance: workflowInstanceFixture },
    },
    {
      method: 'GET',
      path: '/api/v1/workflow-runtime/instances',
      status: 200,
      body: { success: true, instances: workflowInstanceRowsFixture },
    },
    {
      method: 'GET',
      path: '/api/v1/workflow-runtime/instances/wi-ops-1/recompute-plan',
      status: 200,
      body: { success: true, instance_id: 'wi-ops-1', stale: 3, counts: {}, decisions: [], recompute: ['spatial_join'], reuse: ['build_topology'], explanations: ['sources refreshed'], changed_dimensions: ['data'] },
    },
  ];
}

describe('RuntimeSection —— runtime-inspector 复活（ADR-0142 D2）', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('实例列表渲染并可选中（数据流动入口）', async () => {
    stubFetch(baseRoutes());
    render(<RuntimeSection ownerToken="tok" />);
    const btn = await screen.findByTestId('instance-wi-ops-2');
    expect(btn).toBeInTheDocument();
    // 首个实例自动选中（rows 到达后的 effect 提交）
    await waitFor(() =>
      expect(screen.getByTestId('instance-wi-ops-1')).toHaveAttribute('aria-pressed', 'true'),
    );
  });

  it('§5 门禁：RuntimeInspector 在真实 UI 中出现且节点数据可见', async () => {
    stubFetch(baseRoutes());
    render(<RuntimeSection ownerToken="tok" />);
    // runtime-inspector.tsx 的专属 testid（wf-status / wf-node-*）出现在真实挂载树
    await waitFor(() => expect(screen.getByTestId('wf-status')).toHaveTextContent('running'));
    expect(screen.getByTestId('wf-node-spatial_join')).toBeInTheDocument();
    expect(screen.getByTestId('wf-node-validate_geom')).toBeInTheDocument();
    expect(screen.getByText(/重算：/)).toBeInTheDocument(); // explain.why_recomputed
  });

  it('FAILED 节点出现在干预区并可重试', async () => {
    const fn = stubFetch([
      ...baseRoutes(),
      {
        method: 'POST',
        path: '/api/v1/workflow-runtime/instances/wi-ops-1/nodes/validate_geom/retry',
        status: 200,
        body: { node: 'validate_geom', state: 'READY', attempts: 4, force: false },
      },
    ]);
    render(<RuntimeSection ownerToken="tok" />);
    const retryBtn = await screen.findByTestId('retry-validate_geom');
    fireEvent.click(retryBtn);
    await waitFor(() => {
      const calls = fn.mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.includes('/nodes/validate_geom/retry'))).toBe(true);
    });
  });

  it('取消实例走确认对话框', async () => {
    const fn = stubFetch([
      ...baseRoutes(),
      { method: 'POST', path: '/api/v1/workflow-runtime/instances/wi-ops-1/cancel', status: 200, body: { cancelled: true, status: 'cancelled' } },
    ]);
    render(<RuntimeSection ownerToken="tok" />);
    // 触发器（对话框未开时唯一同名按钮）
    fireEvent.click(await screen.findByRole('button', { name: '取消实例' }));
    // 对话框内确认按钮（同名，限定 dialog 范围）
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '取消实例' }));
    await waitFor(() => {
      const calls = fn.mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.endsWith('/instances/wi-ops-1/cancel'))).toBe(true);
    });
  });

  it('recompute-plan 视图切换显示重算/复用清单', async () => {
    stubFetch(baseRoutes());
    render(<RuntimeSection ownerToken="tok" />);
    // CI 全量单进程跑 367 文件时 runner 繁忙，默认 1s 等待窗偶发不够
    // （09d839d 轮实测失败、本地全量复跑通过）—— 放宽等待窗，断言不变。
    fireEvent.click(
      await screen.findByRole('button', { name: '重算计划' }, { timeout: 5000 })
    );
    const plan = await screen.findByTestId('recompute-plan', {}, { timeout: 5000 });
    expect(plan).toHaveTextContent(/stale 3/);
    expect(plan).toHaveTextContent('sources refreshed');
  });

  it('空实例列表 → 空态（不崩溃）', async () => {
    stubFetch([
      { method: 'GET', path: '/api/v1/workflow-runtime/instances', status: 200, body: { success: true, instances: [] } },
    ]);
    render(<RuntimeSection ownerToken="tok" />);
    expect(await screen.findByText('当前无运行时实例')).toBeInTheDocument();
    expect(screen.queryByTestId('wf-status')).not.toBeInTheDocument();
  });
});
