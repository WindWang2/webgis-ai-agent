/**
 * ops 控制台全端点三态路由注册器（ADR-0142 D4 —— §5 门禁「每端点三态」的
 * 单一事实点）。
 *
 * 为 geocompute + workflow-runtime + 健康面的每个端点注册 ok/empty/error
 * 三态 handler。error 态默认 403 admin-required（cluster 面 require_admin），
 * 可用 override 覆盖个别端点。
 */
import type { ApiStub } from './api-stub';
import { stateful } from './api-stub';
import {
  clusterMetricsFixture,
  emptyClusterMetricsFixture,
  clusterWorkersFixture,
  emptyClusterWorkersFixture,
  stuckRunsFixture,
  emptyStuckRunsFixture,
  runsListFixture,
  emptyRunsListFixture,
  resetRunOkFixture,
  adminRequiredErrorFixture,
  clusterUnavailableErrorFixture,
  planInvalidErrorFixture,
  planValidationFixture,
  clusterSubmitFixture,
  executionRunFixture,
  runEventPages,
} from './geocompute-fixtures';
import {
  workflowInstanceRowsFixture,
  emptyWorkflowInstancesFixture,
  workflowInstanceFixture,
  emptyWorkflowInstanceFixture,
  workflowPackagesFixture,
  emptyWorkflowPackagesFixture,
  recomputePlanFixture,
  debugBundleFixture,
  instanceBusyErrorFixture,
} from './workflow-runtime-fixtures';
import {
  healthBasicFixture,
  readyOkFixture,
  detailedStatusOkFixture,
  versionInfoFixture,
  activeJobsFixture,
  emptyJobsFixture,
} from './health-fixtures';

/** 注册 ops 控制台消费的全部端点（每端点 ok/empty/error 三态）。 */
export function installOpsRoutes(stub: ApiStub): ApiStub {
  const adminError = adminRequiredErrorFixture;
  return (
    stub
    /* ---------- geocompute ---------- */
    .on('GET', '/api/v1/geocompute/cluster/metrics', stateful({ ok: clusterMetricsFixture, empty: emptyClusterMetricsFixture, error: adminError }))
    .on('GET', '/api/v1/geocompute/cluster/workers', stateful({ ok: clusterWorkersFixture, empty: emptyClusterWorkersFixture, error: adminError }))
    .on('GET', '/api/v1/geocompute/cluster/runs/stuck', stateful({ ok: stuckRunsFixture, empty: emptyStuckRunsFixture, error: adminError }))
    .on('POST', '/api/v1/geocompute/cluster/runs/run-stuck-001/reset', stateful({ ok: resetRunOkFixture, empty: resetRunOkFixture, error: adminRequiredErrorFixture }))
    // 叙事：run-stuck-002 的干预命中并发转移（409 是回执不是故障）
    .always('POST', '/api/v1/geocompute/cluster/runs/run-stuck-002/reset', () => ({ status: 409, body: { code: 'RUN_NOT_RESETABLE', message: 'run is terminal/queued or was concurrently transitioned' } }))
    .on('POST', '/api/v1/geocompute/cluster/ledger/limits', stateful({ ok: { scope_key: 'global', limit_rows: 1000, limit_bytes: null, limit_units: null, limit_mem_mb: null, limit_gpu: null }, empty: { scope_key: 'global' }, error: { status: 500, body: { code: 'LEDGER_LIMITS_FAILED', message: 'failed' } } }))
    .on('GET', '/api/v1/geocompute/runs/run-live-100', stateful({ ok: { run_id: 'run-live-100', status: 'running', progress: { settled: 2, done: 3, failed: 0, total: 4 } }, empty: { run_id: 'run-live-100', status: 'running' }, error: adminError }))
    .on('GET', '/api/v1/geocompute/runs/run-cluster-400/events', stateful({
      ok: (ctx: { search: URLSearchParams }) => {
        const afterId = Number(ctx.search.get('after_id') ?? '0');
        return afterId === 0
          ? { run_id: 'run-cluster-400', events: runEventPages[0], after_id: 5, count: 5 }
          : { run_id: 'run-cluster-400', events: runEventPages[1], after_id: 19, count: 14 };
      },
      empty: { run_id: 'run-cluster-400', events: [], after_id: 0, count: 0 },
      error: clusterUnavailableErrorFixture,
    }))
    .on('GET', '/api/v1/geocompute/runs', stateful({ ok: runsListFixture, empty: emptyRunsListFixture, error: adminError }))
    .on('POST', '/api/v1/geocompute/plans/validate', stateful({ ok: planValidationFixture, empty: planValidationFixture, error: planInvalidErrorFixture }))
    .on('POST', '/api/v1/geocompute/plans/runs', stateful({ ok: clusterSubmitFixture, empty: clusterSubmitFixture, error: { status: 429, body: { code: 'CLUSTER_BACKPRESSURE', message: 'cluster overloaded' } } }))
    .on('POST', '/api/v1/geocompute/plans/execute', stateful({ ok: executionRunFixture, empty: executionRunFixture, error: planInvalidErrorFixture }))
    .on('POST', '/api/v1/geocompute/runs/run-live-100/cancel', stateful({ ok: { run_id: 'run-live-100', cancelled: false, requested: true, status: 'cancelling', source: 'cluster' }, empty: { run_id: 'run-live-100', cancelled: false, status: 'cancelled' }, error: adminError }))
    /* ---------- workflow-runtime ---------- */
    .on('GET', '/api/v1/workflow-runtime/instances', stateful({ ok: { success: true, instances: workflowInstanceRowsFixture }, empty: { success: true, instances: emptyWorkflowInstancesFixture }, error: { status: 401, body: { detail: 'Not authenticated' } } }))
    .on('GET', '/api/v1/workflow-runtime/instances/wi-ops-1', stateful({ ok: { success: true, instance: workflowInstanceFixture }, empty: { success: true, instance: emptyWorkflowInstanceFixture }, error: { status: 404, body: { detail: 'not found' } } }))
    .on('GET', '/api/v1/workflow-runtime/instances/wi-ops-1/recompute-plan', stateful({ ok: { success: true, ...recomputePlanFixture }, empty: { success: true, instance_id: 'wi-ops-1', stale: 0, counts: {}, decisions: [] }, error: { status: 404, body: { detail: 'not found' } } }))
    .on('GET', '/api/v1/workflow-runtime/instances/wi-ops-1/debug', stateful({ ok: { success: true, ...debugBundleFixture }, empty: { success: true, instance: emptyWorkflowInstanceFixture, recent_events: [], children: [] }, error: { status: 404, body: { detail: 'not found' } } }))
    .on('POST', '/api/v1/workflow-runtime/instances/wi-ops-1/nodes/validate_geom/retry', stateful({ ok: { node: 'validate_geom', state: 'READY', attempts: 4, force: false }, empty: { node: 'validate_geom', state: 'READY', attempts: 4, force: false }, error: instanceBusyErrorFixture }))
    .on('POST', '/api/v1/workflow-runtime/instances/wi-ops-1/cancel', stateful({ ok: { cancelled: true, status: 'cancelled' }, empty: { cancelled: false, status: 'cancelled' }, error: { status: 404, body: { detail: 'not found' } } }))
    .on('GET', '/api/v1/workflow-runtime/packages', stateful({ ok: { success: true, packages: workflowPackagesFixture }, empty: { success: true, packages: emptyWorkflowPackagesFixture }, error: { status: 401, body: { detail: 'Not authenticated' } } }))
    /* ---------- 健康面 ---------- */
    .on('GET', '/api/v1/health', stateful({ ok: healthBasicFixture, empty: healthBasicFixture, error: { status: 503, body: { detail: 'unavailable' } } }))
    .on('GET', '/api/v1/ready', stateful({ ok: readyOkFixture, empty: readyOkFixture, error: { status: 503, body: { ready: false } } }))
    .on('GET', '/api/v1/status/detailed', stateful({ ok: detailedStatusOkFixture, empty: detailedStatusOkFixture, error: { status: 503, body: { detail: 'unavailable' } } }))
    .on('GET', '/api/v1/version', stateful({ ok: versionInfoFixture, empty: versionInfoFixture, error: { status: 503, body: { detail: 'unavailable' } } }))
    .on('GET', '/api/v1/tasks/jobs', stateful({ ok: activeJobsFixture, empty: emptyJobsFixture, error: { status: 401, body: { detail: 'Not authenticated' } } }))
  );
}
