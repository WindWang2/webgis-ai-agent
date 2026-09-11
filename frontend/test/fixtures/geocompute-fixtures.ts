/**
 * geocompute 端点 fixtures（三态：ok / empty / error，ADR-0142 D4）。
 *
 * 形状逐字对照 /api/v1/geocompute 实际响应（ops-console-recon.md §2）。
 * 时间戳统一用相对 `Date.now()` 计算的 ISO，让「心跳新鲜度/卡住时长」等
 * 展示分档在任意时刻运行测试都稳定落在预期档位。
 */
import type {
  ClusterMetrics,
  ClusterRunRow,
  ClusterWorker,
  ExecutionPlan,
  GeoComputeRunEvent,
  PlanValidation,
} from '@/lib/api/geocompute';
import type { MetricPoint } from './metric-series';

const nowMs = () => Date.now();
const iso = (offsetMs: number) => new Date(nowMs() + offsetMs).toISOString();
const MIN = 60_000;

/* ------------------------------------------------------------------ */
/* cluster/metrics                                                     */
/* ------------------------------------------------------------------ */

export const clusterMetricsFixture: ClusterMetrics = {
  runs_by_status: { queued: 4, leased: 3, running: 9, completed: 128, failed: 6, cancelled: 2, preempted: 1 },
  queue_depth: 5,
  inflight: 12,
  completed: 128,
  failed: 6,
  cancelled: 2,
  preempted_total: 3,
  lease_loss_total: 1,
  cancel_latency: { p50_s: 0.8, p95_s: 2.4, samples: 42 },
  queue_wait: { p50_s: 1.5, p95_s: 9.1, samples: 128 },
  waiting_by_profile: { light_cpu: 2, raster: 2, heavy_cpu: 1 },
  events_counters: { event_rejected_invalid: 0, event_budget_exhausted_total: 1, event_append_failed_total: 0 },
  workers: { live: 6, by_role: { worker: 5, coordinator: 1 }, profile_slots: { light_cpu: 12, heavy_cpu: 6, raster: 8 }, gpu_workers: 1 },
  leader: { count: 1, ids: ['coord-primary'] },
  ledger: [
    { scope_key: 'global', rows: 154_203, bytes: 48_120_000_000, limit_rows: null, limit_bytes: null },
    { scope_key: 't:acme', rows: 12_400, bytes: 3_100_000_000, limit_rows: 100_000, limit_bytes: null },
  ],
  resource_rejections: { rows: 2, bytes: 1, units: 4, mem_mb: 1, gpu: 0 },
  oom_avoided: 3,
  gpu_fallbacks: 1,
  spill: { count: 18, bytes: 6_400_000_000, rehydrate_hits: 11, rehydrate_misses: 7 },
  transfer: { bytes_total: 27_140_000_000 },
  cache: { worker_cache_hits: 431 },
  lineage: {
    node_completed: 2_140,
    node_reused: 312,
    node_lost: 9,
    partition_planned: 480,
    speculative_dispatched: 57,
    poison_quarantined: 3,
  },
  utilization: { reserved_units: 14, capacity_units: 26, ratio: 0.538 },
  quarantine: [
    { owner_scope: 'owner:a1b2c3d4e5f6…', fingerprint: '9f2c1e07aa4b13dc', failure_count: 4, active: true, last_error_code: 'CRS_MISMATCH' },
    { owner_scope: 'owner:77aa00bbccdd…', fingerprint: '01ab77ee5590cc21', failure_count: 1, active: false, last_error_code: 'OOM_WORKER' },
  ],
};

/** 空集群：全零 + 空集合（诚实缺失，ratio=null）。 */
export const emptyClusterMetricsFixture: ClusterMetrics = {
  runs_by_status: {},
  queue_depth: 0,
  inflight: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
  preempted_total: 0,
  lease_loss_total: 0,
  cancel_latency: { p50_s: null, p95_s: null, samples: 0 },
  queue_wait: { p50_s: null, p95_s: null, samples: 0 },
  waiting_by_profile: {},
  events_counters: {},
  workers: { live: 0, by_role: {}, profile_slots: {}, gpu_workers: 0 },
  leader: { count: 0, ids: [] },
  ledger: [],
  resource_rejections: { rows: 0, bytes: 0, units: 0, mem_mb: 0, gpu: 0 },
  oom_avoided: 0,
  gpu_fallbacks: 0,
  spill: { count: 0, bytes: 0, rehydrate_hits: 0, rehydrate_misses: 0 },
  transfer: { bytes_total: 0 },
  cache: { worker_cache_hits: 0 },
  lineage: {
    node_completed: 0,
    node_reused: 0,
    node_lost: 0,
    partition_planned: 0,
    speculative_dispatched: 0,
    poison_quarantined: 0,
  },
  utilization: { reserved_units: 0, capacity_units: 0, ratio: null },
  quarantine: [],
};

/* ------------------------------------------------------------------ */
/* cluster/workers                                                     */
/* ------------------------------------------------------------------ */

export const clusterWorkersFixture: { workers: ClusterWorker[]; live: number } = {
  live: 3,
  workers: [
    {
      worker_id: 'w-3f9d2a11c4e5',
      role: 'coordinator',
      profiles: { light_cpu: 2, heavy_cpu: 1 },
      capability: { gpus: [], disk_free_mb: 204_800, cpu_cores: 16, mem_mb: 65_536 },
      heartbeat_age_s: 2.1,
      cache_entries: 0,
      cache_bytes: 0,
    },
    {
      worker_id: 'w-88aa00bb21cc',
      role: 'worker',
      profiles: { raster: 4, light_cpu: 4 },
      capability: { gpus: [{ model: 'A10G', mem_mb: 24_576 }], disk_free_mb: 1_024_000, cpu_cores: 32, mem_mb: 131_072 },
      heartbeat_age_s: 5.4,
      cache_entries: 214,
      cache_bytes: 8_410_000_000,
    },
    {
      worker_id: 'w-51de99ff02aa',
      role: 'worker',
      profiles: { heavy_cpu: 2, network: 2 },
      capability: { gpus: [], disk_free_mb: 512_000, cpu_cores: 24, mem_mb: 98_304 },
      heartbeat_age_s: 8.9,
      cache_entries: 96,
      cache_bytes: 2_200_000_000,
    },
    {
      worker_id: 'w-77ccb43d9012',
      role: 'worker',
      profiles: { light_cpu: 2 },
      // 旧 worker 无能力披露 —— 诚实 null
      capability: null,
      heartbeat_age_s: 47.2,
      cache_entries: 12,
      cache_bytes: 310_000_000,
    },
    {
      worker_id: 'w-dead17beef07',
      role: 'worker',
      profiles: { light_cpu: 1 },
      capability: null,
      heartbeat_age_s: 183.5,
      cache_entries: 0,
      cache_bytes: 0,
    },
  ],
};

export const emptyClusterWorkersFixture: { workers: ClusterWorker[]; live: number } = {
  live: 0,
  workers: [],
};

/* ------------------------------------------------------------------ */
/* cluster/runs/stuck + runs 列表                                      */
/* ------------------------------------------------------------------ */

function stuckRow(partial: Partial<ClusterRunRow> & { run_id: string }): ClusterRunRow {
  return {
    status: 'running',
    owner_scope: 'owner:test',
    plan_fingerprint: 'aa77bb00ee55',
    session_id: 'sess-1',
    priority: 5,
    attempts: 1,
    preempts: 0,
    lease_epoch: 2,
    cancel_requested_at: null,
    yield_requested_at: null,
    error_code: null,
    required_profiles: ['light_cpu'],
    resource_request: null,
    created_at: iso(-20 * MIN),
    started_at: iso(-18 * MIN),
    terminal_at: null,
    heartbeat_at: iso(-9 * MIN),
    lease_expires_at: iso(-4 * MIN),
    id: 1,
    tenant_key: 't:test',
    coordinator_id: 'coord-primary',
    dispatch_seq: 7,
    ...partial,
  };
}

export const stuckRunsFixture = {
  count: 2,
  runs: [
    stuckRow({
      run_id: 'run-stuck-001',
      status: 'running',
      attempts: 3,
      lease_epoch: 4,
      lease_expires_at: iso(-6 * MIN),
      started_at: iso(-31 * MIN),
      error_code: null,
    }),
    stuckRow({
      run_id: 'run-stuck-002',
      status: 'leased',
      attempts: 1,
      required_profiles: ['raster'],
      lease_expires_at: iso(-2 * MIN),
      started_at: iso(-9 * MIN),
    }),
  ],
};

export const emptyStuckRunsFixture = { count: 0, runs: [] };

export const runsListFixture = {
  runs: [
    stuckRow({ run_id: 'run-live-100', status: 'running', started_at: iso(-3 * MIN), heartbeat_at: iso(-5_000), lease_expires_at: iso(2 * MIN) }),
    stuckRow({ run_id: 'run-queued-101', status: 'queued', started_at: null, heartbeat_at: null, lease_expires_at: null }),
    stuckRow({ run_id: 'run-completed-102', status: 'completed', terminal_at: iso(-1 * MIN), lease_expires_at: null }),
  ],
  terminal_snapshots: [
    { run_id: 'run-snap-200', status: 'failed', source: 'snapshot' as const, created_at: iso(-45 * MIN) },
  ],
  limit: 50,
  offset: 0,
};

export const emptyRunsListFixture = { runs: [], terminal_snapshots: [], limit: 50, offset: 0 };

/* ------------------------------------------------------------------ */
/* run 事件（21 值封闭词表的分页脚本）                                   */
/* ------------------------------------------------------------------ */

function ev(id: number, event: string, at: string, extra: Partial<GeoComputeRunEvent> = {}): GeoComputeRunEvent {
  return { id, run_id: 'run-live-100', event, node_id: null, worker_id: null, attempt: null, status: null, rows: null, bytes: null, error_code: null, created_at: at, ...extra };
}

/** 三页事件脚本：常规派发 → 投机/溢出叙事 → 终态。 */
export const runEventPages: GeoComputeRunEvent[][] = [
  [
    ev(1, 'run_started', iso(-30 * MIN), { worker_id: 'w-3f9d2a11c4e5' }),
    ev(2, 'node_dispatched', iso(-29 * MIN), { node_id: 'clip_a', worker_id: 'w-88aa00bb21cc', attempt: 1 }),
    ev(3, 'node_started', iso(-29 * MIN), { node_id: 'clip_a', worker_id: 'w-88aa00bb21cc', attempt: 1 }),
    ev(4, 'partition_planned', iso(-28 * MIN), { node_id: 'clip_a', status: 'tiles=8' }),
    ev(5, 'node_output_ready', iso(-27 * MIN), { node_id: 'clip_a', rows: 120_400, bytes: 48_600_000 }),
  ],
  [
    ev(6, 'speculative_dispatch', iso(-26 * MIN), { node_id: 'merge_b', attempt: 1 }),
    ev(7, 'waiting_resource', iso(-25 * MIN), { node_id: 'merge_b', status: 'heavy_cpu' }),
    ev(8, 'gpu_fallback', iso(-24 * MIN), { node_id: 'merge_b' }),
    ev(9, 'node_completed', iso(-23 * MIN), { node_id: 'clip_a', rows: 120_400 }),
    ev(10, 'worker_cache_hit', iso(-22 * MIN), { node_id: 'buffer_c', worker_id: 'w-51de99ff02aa' }),
    ev(11, 'node_reused', iso(-21 * MIN), { node_id: 'buffer_c' }),
    ev(12, 'node_failed', iso(-20 * MIN), { node_id: 'reproject_d', attempt: 1, error_code: 'CRS_MISMATCH' }),
    ev(13, 'node_dispatched', iso(-20 * MIN), { node_id: 'reproject_d', attempt: 2 }),
    ev(14, 'node_completed', iso(-19 * MIN), { node_id: 'reproject_d', attempt: 2 }),
    ev(15, 'speculative_resolved', iso(-18 * MIN), { node_id: 'merge_b', status: 'primary' }),
    ev(16, 'straggler_detected', iso(-17 * MIN), { worker_id: 'w-dead17beef07' }),
    ev(17, 'node_lost', iso(-16 * MIN), { node_id: 'simplify_e', worker_id: 'w-dead17beef07' }),
    ev(18, 'poison_quarantined', iso(-15 * MIN), { node_id: 'simplify_e', error_code: 'GEOM_INVALID' }),
    ev(19, 'node_cancelled', iso(-14 * MIN), { node_id: 'simplify_e' }),
  ],
  [
    ev(20, 'node_completed', iso(-2 * MIN), { node_id: 'merge_b', rows: 1_204_000, bytes: 912_000_000 }),
    ev(21, 'run_completed', iso(-1 * MIN), { status: 'completed' }),
  ],
];

/** 空页（run 存在但事件窗为空）。 */
export const emptyRunEventsPage = { run_id: 'run-empty', events: [], after_id: 0, count: 0 };

/* ------------------------------------------------------------------ */
/* plans 校验 / 执行 / cluster 提交                                     */
/* ------------------------------------------------------------------ */

export const samplePlan: ExecutionPlan = {
  plan_id: 'plan-ops-demo-1',
  description: '裁剪→缓冲→合并 示范计划',
  budget: { max_nodes: 16 },
  nodes: [
    { node_id: 'clip_a', category: 'vector', operation: 'clip', inputs: [], parameters: { boundary: 'aoi' }, policy: 'in_process' },
    { node_id: 'buffer_b', category: 'vector', operation: 'buffer', inputs: ['clip_a'], parameters: { distance_m: 500 }, policy: 'in_process' },
    { node_id: 'merge_c', category: 'vector', operation: 'merge', inputs: ['clip_a', 'buffer_b'], reuse: 'allow' },
  ],
};

export const planValidationFixture: PlanValidation = {
  plan_id: 'plan-ops-demo-1',
  graph_fingerprint: 'graph_9f2c1e07aa4b',
  node_fingerprints: {
    clip_a: 'fp_a1',
    buffer_b: 'fp_b2',
    merge_c: 'fp_c3',
  },
  waves: [['clip_a'], ['buffer_b'], ['merge_c']],
  wired_categories: ['vector'],
};

/** 校验失败：422 GeoComputeError.to_dict() 形状（错误定位到分区条目）。 */
export const planInvalidErrorFixture = {
  status: 422,
  body: {
    detail: {
      code: 'PLAN_INVALID',
      message: 'plan graph invalid',
      details: {
        errors: [
          { node_id: 'merge_c', field: 'inputs', issue: 'unknown input node: buffer_x' },
          { node_id: 'clip_a', field: 'category', issue: 'category not wired: quantum' },
        ],
      },
    },
  },
};

export const executionRunFixture = {
  run_id: 'run-exec-300',
  plan_id: 'plan-ops-demo-1',
  plan_fingerprint: 'graph_9f2c1e07aa4b',
  status: 'completed',
  wall_time_s: 41.7,
  error_code: null,
  error_message: null,
  source: 'cluster',
  evidence: {
    clip_a: { status: 'completed', attempts: 1, duration_s: 8.2, rows_emitted: 120_400, bytes_emitted: 48_600_000, output_ref: 'blob://a1', retry_safe: true, failure_codes: [] },
    buffer_b: { status: 'completed', attempts: 1, duration_s: 15.9, rows_emitted: 120_400, bytes_emitted: 96_200_000, output_ref: 'blob://b2', retry_safe: true, failure_codes: [] },
    merge_c: { status: 'reused', attempts: 0, duration_s: 0, rows_emitted: 1_204_000, output_ref: 'blob://c3', fingerprint: 'fp_c3', failure_codes: [] },
  },
  lineage: [],
  reproducibility: { graph_fingerprint: 'graph_9f2c1e07aa4b' },
};

/** cluster 提交 202（阶段化进度观察的起点）。 */
export const clusterSubmitFixture = {
  run_id: 'run-cluster-400',
  status: 'queued',
  plan_fingerprint: 'graph_9f2c1e07aa4b',
  required_profiles: ['light_cpu'],
  resource: { min_mem_mb: 1024, min_cpu: 2, gpu: 0, zone: null, required_profiles: ['light_cpu'], fallback_cpu: false },
  source: 'cluster',
};

export const driftVerdictFixture = {
  state: 'stale_runtime',
  reason: 'runtime fingerprint changed since stored plan',
  stored_plan_fingerprint: 'graph_9f2c1e07aa4b',
  current_plan_fingerprint: 'graph_9f2c1e07aa4b',
  stored_runtime_fingerprint: 'rt_01',
  current_runtime_fingerprint: 'rt_02',
};

export const resetRunOkFixture = { run_id: 'run-stuck-001', outcome: 'requeued', status: 'queued', attempts: 4 };

export const resetRunConflictFixture = {
  status: 409,
  body: { code: 'RUN_NOT_RESETABLE', message: 'run is terminal/queued or was concurrently transitioned' },
};

/* ------------------------------------------------------------------ */
/* 错误形状（cluster 面共通）                                           */
/* ------------------------------------------------------------------ */

export const adminRequiredErrorFixture = {
  status: 403,
  body: { detail: 'Admin privileges required' },
};

export const clusterUnavailableErrorFixture = {
  status: 503,
  body: { code: 'CLUSTER_UNAVAILABLE', message: 'cluster control plane unavailable' },
};

/* ------------------------------------------------------------------ */
/* 合成序列 → 快照（时序图/大屏叙事测试）                                */
/* ------------------------------------------------------------------ */

/** 把 metric-series 采样点折叠成 /cluster/metrics 快照形状（增量累计）。 */
export function snapshotFromPoint(point: MetricPoint, cumulative: { transfer: number; cache: number; completed: number }): ClusterMetrics {
  cumulative.transfer += point.transferBytes;
  cumulative.cache += point.cacheHits;
  cumulative.completed += point.nodeCompleted;
  return {
    ...emptyClusterMetricsFixture,
    queue_depth: point.queueDepth,
    inflight: point.inflight,
    workers: { live: point.workersLive, by_role: { worker: Math.max(0, point.workersLive - 1), coordinator: 1 }, profile_slots: {}, gpu_workers: 0 },
    spill: { count: point.spillCount, bytes: point.spillCount * 320_000_000, rehydrate_hits: 0, rehydrate_misses: 0 },
    transfer: { bytes_total: cumulative.transfer },
    cache: { worker_cache_hits: cumulative.cache },
    lineage: {
      node_completed: cumulative.completed,
      node_reused: point.nodeReused,
      node_lost: point.nodeLost,
      partition_planned: point.partitionPlanned,
      speculative_dispatched: point.speculativeDispatched,
      poison_quarantined: point.poisonQuarantined,
    },
    utilization: { reserved_units: Math.round((point.utilization ?? 0) * 26), capacity_units: 26, ratio: point.utilization },
    quarantine: [],
  };
}
