import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  validatePlan,
  submitClusterRun,
  getClusterMetrics,
  getClusterWorkers,
  getStuckRuns,
  resetStuckRun,
  listClusterRuns,
  cancelClusterRun,
  GeoComputeApiError,
  ClusterUnavailableError,
  type ClusterMetrics,
} from './geocompute';
import { clusterMetricsFixture } from '@/test/fixtures/geocompute-fixtures';
import { samplePlan } from '@/test/fixtures/geocompute-fixtures';

function stubFetch(routes: Array<{ method: string; path: string; status: number; body: unknown; headers?: Record<string, string> }>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    const route = routes.find(
      (r) => r.method === method && (url.pathname === r.path || url.pathname.startsWith(r.path)),
    );
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { 'Content-Type': 'application/json', ...(route.headers ?? {}) },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('geocompute V9 运维面 typed client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('POST /plans/validate 返回分波与指纹', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/geocompute/plans/validate', status: 200, body: { plan_id: 'p1', graph_fingerprint: 'g1', node_fingerprints: {}, waves: [['a']], wired_categories: ['vector'] } },
    ]);
    const res = await validatePlan(samplePlan);
    expect(res.waves).toHaveLength(1);
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body)).plan_id).toBe('plan-ops-demo-1');
  });

  it('POST /plans/runs（cluster 202）body 携带 priority/resource', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/geocompute/plans/runs', status: 202, body: { run_id: 'r1', status: 'queued', plan_fingerprint: 'g1', required_profiles: ['light_cpu'], resource: null, source: 'cluster' } },
    ]);
    const res = await submitClusterRun(samplePlan, { priority: 10, resource: { min_cpu: 2 } });
    expect(res.source).toBe('cluster');
    const init = fn.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body));
    expect(body.priority).toBe(10);
    expect(body.resource).toEqual({ min_cpu: 2 });
  });

  it('GET /cluster/metrics 返回 ADR-0133 五类指标形状', async () => {
    stubFetch([{ method: 'GET', path: '/api/v1/geocompute/cluster/metrics', status: 200, body: clusterMetricsFixture }]);
    const m: ClusterMetrics = await getClusterMetrics();
    expect(m.transfer.bytes_total).toBeGreaterThan(0);
    expect(m.lineage.node_completed).toBeGreaterThan(0);
    expect(m.quarantine).toHaveLength(2);
    expect(m.spill.rehydrate_hits).toBeDefined();
  });

  it('403 require_admin → GeoComputeApiError ADMIN_REQUIRED（D7 权限态）', async () => {
    stubFetch([{ method: 'GET', path: '/api/v1/geocompute/cluster/metrics', status: 403, body: { detail: 'Admin privileges required' } }]);
    const err = await getClusterMetrics().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(GeoComputeApiError);
    expect((err as GeoComputeApiError).code).toBe('ADMIN_REQUIRED');
  });

  it('503 控制面不可用 → ClusterUnavailableError', async () => {
    stubFetch([
      { method: 'GET', path: '/api/v1/geocompute/cluster/workers', status: 503, body: { code: 'CLUSTER_UNAVAILABLE', message: 'down' } },
      { method: 'GET', path: '/api/v1/geocompute/cluster/runs/stuck', status: 503, body: { code: 'CLUSTER_UNAVAILABLE', message: 'down' } },
    ]);
    await expect(getClusterWorkers()).rejects.toBeInstanceOf(ClusterUnavailableError);
    await expect(getStuckRuns()).rejects.toBeInstanceOf(ClusterUnavailableError);
  });

  it('POST /cluster/runs/{id}/reset 返回 requeued 回执', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/geocompute/cluster/runs/run-stuck-001/reset', status: 200, body: { run_id: 'run-stuck-001', outcome: 'requeued', status: 'queued', attempts: 4 } },
    ]);
    const res = await resetStuckRun('run-stuck-001', { reason: 'ops' });
    expect(res.outcome).toBe('requeued');
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body)).reason).toBe('ops');
  });

  it('409 RUN_NOT_RESETABLE 折叠为 typed 错误', async () => {
    stubFetch([
      { method: 'POST', path: '/api/v1/geocompute/cluster/runs/run-x/reset', status: 409, body: { code: 'RUN_NOT_RESETABLE', message: 'run is terminal/queued' } },
    ]);
    await expect(resetStuckRun('run-x')).rejects.toMatchObject({ code: 'RUN_NOT_RESETABLE' });
  });

  it('GET /runs?status=… 列表投影', async () => {
    const fn = stubFetch([
      { method: 'GET', path: '/api/v1/geocompute/runs', status: 200, body: { runs: [], terminal_snapshots: [], limit: 50, offset: 0 } },
    ]);
    await listClusterRuns({ status: ['queued', 'running'], limit: 20 });
    const calledUrl = String(fn.mock.calls[0][0]);
    expect(calledUrl).toContain('status=queued%2Crunning');
    expect(calledUrl).toContain('limit=20');
  });

  it('POST /runs/{id}/cancel cluster 形状返回 requested', async () => {
    stubFetch([
      { method: 'POST', path: '/api/v1/geocompute/runs/run-1/cancel', status: 200, body: { run_id: 'run-1', cancelled: false, requested: true, status: 'cancelling', source: 'cluster' } },
    ]);
    const res = await cancelClusterRun('run-1');
    expect(res.requested).toBe(true);
  });
});
