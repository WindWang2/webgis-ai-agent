import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  getInstance,
  listInstances,
  listPackages,
  retryNode,
  cancelNodes,
  applyChanges,
  WorkflowRuntimeApiError,
} from './workflow-runtime';

/** fetch stub：按 (method, path) 返回 {status, body}。 */
function stubFetch(routes: Array<{ method: string; path: string; status: number; body: unknown }>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    const route = routes.find(
      (r) => r.method === method && (r.path === url.pathname || url.pathname.startsWith(r.path)),
    );
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('workflow-runtime typed client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    stubFetch([
      {
        method: 'GET',
        path: '/api/v1/workflow-runtime/instances/wi-1',
        status: 200,
        body: {
          success: true,
          instance: {
            instance_id: 'wi-1', package_id: 'p', package_version: '1.0.0', package_fingerprint: 'f',
            status: 'running', revision: 1, cancel_requested: false, nodes: [], counts: {},
            decisions: [], pending_changes: [], error_code: null, error_detail: null,
            explain: { why_recomputed: [], why_reused: [], blocked: [] },
          },
        },
      },
      { method: 'GET', path: '/api/v1/workflow-runtime/instances', status: 200, body: { success: true, instances: [{ instance_id: 'wi-1', package_id: 'p', status: 'running' }] } },
      { method: 'GET', path: '/api/v1/workflow-runtime/packages', status: 200, body: { success: true, packages: [] } },
    ]);
  });

  it('GET /instances 解包 instances 数组', async () => {
    const rows = await listInstances();
    expect(rows).toHaveLength(1);
    expect(rows[0].instance_id).toBe('wi-1');
  });

  it('GET /packages 空列表 → 空数组（不抛错）', async () => {
    await expect(listPackages()).resolves.toEqual([]);
  });

  it('getInstance 返回 instance 投影（runtime-inspector fetcher 注入形状）', async () => {
    const inst = await getInstance('wi-1');
    expect(inst.instance_id).toBe('wi-1');
    expect(inst.explain).toBeDefined();
  });

  it('owner 隔离 404 {"detail":"not found"} → INSTANCE_NOT_FOUND', async () => {
    stubFetch([{ method: 'GET', path: '/api/v1/workflow-runtime/instances/wi-x', status: 404, body: { detail: 'not found' } }]);
    await expect(getInstance('wi-x')).rejects.toMatchObject({
      name: 'WorkflowRuntimeApiError',
      code: 'INSTANCE_NOT_FOUND',
      status: 404,
    });
  });

  it('detail="{CODE}: msg" 字符串解析为 typed 错误码（INSTANCE_BUSY 409）', async () => {
    stubFetch([{ method: 'POST', path: '/api/v1/workflow-runtime/instances/wi-1/nodes/n1/retry', status: 409, body: { detail: 'INSTANCE_BUSY: instance is running' } }]);
    const err = await retryNode('wi-1', 'n1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WorkflowRuntimeApiError);
    expect((err as WorkflowRuntimeApiError).code).toBe('INSTANCE_BUSY');
    expect((err as WorkflowRuntimeApiError).message).toBe('instance is running');
  });

  it('retryNode?force=true 透传查询参数', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/workflow-runtime/instances/wi-1/nodes/n1/retry', status: 200, body: { node: 'n1', state: 'READY', attempts: 3, force: true } },
    ]);
    const res = await retryNode('wi-1', 'n1', { force: true });
    expect(res.state).toBe('READY');
    const calledUrl = String(fn.mock.calls[0][0]);
    expect(calledUrl).toContain('retry?force=true');
  });

  it('cancelNodes body 含 include_descendants', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/workflow-runtime/instances/wi-1/nodes/cancel', status: 200, body: { requested: ['n1'], flagged: ['n1'] } },
    ]);
    await cancelNodes('wi-1', ['n1'], { includeDescendants: false });
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ node_ids: ['n1'], include_descendants: false });
  });

  it('applyChanges dry_run=true body 透传', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/workflow-runtime/instances/wi-1/changes', status: 200, body: { style_only: false, recompute: ['n1'] } },
    ]);
    await applyChanges('wi-1', [{ dimension: 'data', target_kind: 'dataset', target: 'd1', detail: 'refresh' }], { dryRun: true });
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body)).dry_run).toBe(true);
  });
});
