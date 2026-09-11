/**
 * 计划控制台测试（P4）—— 编辑器 JSON 预检 / 校验错误定位到分区条目 /
 * cluster 提交 → 游标事件瀑布 / 内存执行 evidence / 取消 run。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PlanConsole } from './plan-console';
import { runEventPages, planInvalidErrorFixture } from '@/test/fixtures/geocompute-fixtures';
function stubFetch(routes: Array<{ method: string; path: string; status: number; body: unknown }>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.pathname.endsWith('/events')) {
      const afterId = Number(url.searchParams.get('after_id') ?? '0');
      const body = afterId === 0 ? { run_id: 'run-cluster-400', events: runEventPages[0], after_id: 5, count: 5 } : { run_id: 'run-cluster-400', events: [], after_id: 5, count: 0 };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    const route =
      routes.find((r) => r.method === method && url.pathname === r.path) ??
      routes.find((r) => r.method === method && url.pathname.startsWith(r.path));
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), { status: route.status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function routes() {
  return [
    { method: 'POST', path: '/api/v1/geocompute/plans/validate', status: 200, body: { plan_id: 'plan-ops-demo-1', graph_fingerprint: 'g1', node_fingerprints: { clip_a: 'a' }, waves: [['clip_a'], ['buffer_b'], ['merge_c']], wired_categories: ['vector'] } },
    { method: 'POST', path: '/api/v1/geocompute/plans/runs', status: 202, body: { run_id: 'run-cluster-400', status: 'queued', plan_fingerprint: 'g1', required_profiles: ['light_cpu'], resource: null, source: 'cluster' } },
    { method: 'POST', path: '/api/v1/geocompute/plans/execute', status: 200, body: { run_id: 'run-exec-300', plan_id: 'plan-ops-demo-1', plan_fingerprint: 'g1', status: 'completed', wall_time_s: 41.7, evidence: { clip_a: { status: 'completed', attempts: 1, rows_emitted: 1204, bytes_emitted: 48600, duration_s: 8.2 } } } },
    { method: 'POST', path: '/api/v1/geocompute/runs/run-cluster-400/cancel', status: 200, body: { run_id: 'run-cluster-400', cancelled: false, requested: true, status: 'cancelling', source: 'cluster' } },
  ];
}

async function insertSample() {
  fireEvent.click(screen.getByRole('button', { name: '插入示例模板' }));
  await waitFor(() =>
    expect((screen.getByLabelText('计划 JSON') as HTMLTextAreaElement).value).toContain('"plan_id"'),
  );
}

describe('PlanConsole（P4）', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('坏 JSON → 内联语法错误（aria-invalid + alert）', () => {
    render(<PlanConsole />);
    const editor = screen.getByLabelText('计划 JSON');
    fireEvent.change(editor, { target: { value: '{"plan_id": broken' } });
    expect(editor).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent(/JSON 语法错误/);
    expect(screen.getByTestId('plan-validate')).toBeDisabled();
  });

  it('校验通过 → 分波展示', async () => {
    stubFetch(routes());
    render(<PlanConsole />);
    await insertSample();
    fireEvent.click(screen.getByTestId('plan-validate'));
    const ok = await screen.findByTestId('plan-validation-ok');
    expect(ok).toHaveTextContent('分波执行 3 波');
    expect(ok).toHaveTextContent('接线类别 vector');
  });

  it('校验失败 → 错误定位到分区条目（node_id + field + issue）', async () => {
    stubFetch([{ method: 'POST', path: '/api/v1/geocompute/plans/validate', status: 422, body: planInvalidErrorFixture.body }]);
    render(<PlanConsole />);
    await insertSample();
    fireEvent.click(screen.getByTestId('plan-validate'));
    const err = await screen.findByTestId('plan-validation-error');
    expect(err).toHaveTextContent('merge_c');
    expect(err).toHaveTextContent('unknown input node: buffer_x');
    expect(err).toHaveTextContent('category not wired: quantum');
  });

  it('提交集群执行 → 进度视图 + 阶段瀑布 + 游标请求', async () => {
    const fn = stubFetch(routes());
    render(<PlanConsole />);
    await insertSample();
    fireEvent.click(screen.getByTestId('plan-submit'));
    expect(await screen.findByTestId('plan-progress')).toHaveTextContent('run-cluster-400');
    // 瀑布从事件首页推导出 clip_a 进行中 span（终态在下一页，3s 轮询后到达）
    await waitFor(() => expect(screen.getByTestId('ops-waterfall')).toBeInTheDocument());
    expect(screen.getByRole('img', { name: /clip_a running/ })).toBeInTheDocument();
    // 游标请求：首页 after_id=0
    const eventCalls = fn.mock.calls.filter((c) => String(c[0]).includes('/events'));
    expect(eventCalls.length).toBeGreaterThan(0);
    expect(String(eventCalls[0][0])).toContain('after_id=0');
    // 取消按钮存在并可点
    fireEvent.click(screen.getByTestId('plan-cancel-run'));
    await waitFor(() => {
      expect(fn.mock.calls.some((c) => String(c[0]).endsWith('/runs/run-cluster-400/cancel'))).toBe(true);
    });
  });

  it('内存执行 → evidence 快照表', async () => {
    stubFetch(routes());
    render(<PlanConsole />);
    await insertSample();
    fireEvent.click(screen.getByRole('button', { name: '内存执行' }));
    const run = await screen.findByTestId('plan-memory-run');
    expect(run).toHaveTextContent('run-exec-300');
    expect(run).toHaveTextContent('clip_a');
    expect(run).toHaveTextContent('completed');
  });
});
