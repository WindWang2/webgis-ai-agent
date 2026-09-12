/**
 * geocompute 客户端（V7 Phase G → V9 运维控制台全量观测面，ADR-0142）。
 *
 * 基础面（V7）：
 * - GET /runs/{id}/events：有界 observability trace（封闭词表 event、
 *   after_id 断点续读游标；run 行 retention 清理后 404 —— 调用方须诚实
 *   披露「事件不可用」而非静默空表）。
 * - POST /runs/{id}/cancel：取消。
 *
 * 运维面（V9，端点契约逐字对照 app/api/routes/geocompute.py @8b5b8375）：
 * - GET  /cluster/metrics：ClusterMetrics 快照（ADR-0133 五类指标）。
 * - GET  /cluster/workers / /cluster/runs/stuck：worker 心跳与卡住 run。
 * - POST /cluster/runs/{id}/reset：stuck 干预（requeue | fail）。
 * - POST /cluster/ledger/limits：账本限额写入。
 * - GET  /runs 列表 / GET /runs/{id} / GET /runs/{id}/summary。
 * - POST /plans/validate | /plans/execute | /plans/runs | /plans/drift-check。
 *
 * 权限：cluster 读面全部 require_admin —— 403 是一等公民状态（ADR-0142 D7），
 * 调用方须呈现「需要管理员权限」诚实态而非死按钮。
 */
import { apiFetch, isApiError } from './transport';

/* ------------------------------------------------------------------ */
/* 事件流（V7 既有面，原样保留）                                        */
/* ------------------------------------------------------------------ */

export interface GeoComputeRunEvent {
  id: number;
  run_id: string;
  /** 封闭词表（后端 cluster.events.EVENT_VOCABULARY）。 */
  event: string;
  node_id?: string | null;
  worker_id?: string | null;
  attempt?: number | null;
  status?: string | null;
  rows?: number | null;
  bytes?: number | null;
  error_code?: string | null;
  created_at: string;
}

export interface RunEventsPage {
  run_id: string;
  events: GeoComputeRunEvent[];
  after_id: number;
  count: number;
}

export class RunEventsUnavailableError extends Error {
  constructor(public readonly reason: 'not_found' | 'unavailable') {
    super(reason === 'not_found' ? 'RUN_NOT_FOUND' : 'CLUSTER_UNAVAILABLE');
    this.name = 'RunEventsUnavailableError';
  }
}

export async function getRunEvents(
  runId: string,
  opts: {
    afterId?: number;
    limit?: number;
    ownerToken?: string | null;
    signal?: AbortSignal;
  } = {},
): Promise<RunEventsPage> {
  const params = new URLSearchParams();
  if (opts.afterId != null) params.set('after_id', String(opts.afterId));
  if (opts.limit != null) params.set('limit', String(opts.limit));
  const qs = params.toString();
  try {
    return await apiFetch<RunEventsPage>(
      `/api/v1/geocompute/runs/${encodeURIComponent(runId)}/events${qs ? `?${qs}` : ''}`,
      { ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    if (isApiError(err) && err.status === 404) {
      throw new RunEventsUnavailableError('not_found');
    }
    if (isApiError(err) && err.status === 503) {
      throw new RunEventsUnavailableError('unavailable');
    }
    throw err;
  }
}

export async function cancelRun(runId: string, ownerToken?: string | null): Promise<void> {
  await apiFetch(`/api/v1/geocompute/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    ownerToken: ownerToken ?? null,
  });
}

/* ------------------------------------------------------------------ */
/* V9 运维面 —— 共同类型                                                */
/* ------------------------------------------------------------------ */

/** geocompute 控制面 typed 错误（body 形如 {code, message, details?}）。 */
export class GeoComputeApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = 'GeoComputeApiError';
  }
}

/** 把 apiFetch 抛出的 ApiError 折叠成 typed 错误；非 {code,...} 形状原样上抛。 */
function rethrowTyped(err: unknown): never {
  if (isApiError(err)) {
    // ADR-0142 D7：cluster 面 require_admin —— 403 是一等公民权限态，
    // 控制台据此渲染「需要管理员权限」诚实态而非通用错误。
    if (err.status === 403) {
      const detail = (err.body as { detail?: unknown } | null)?.detail;
      throw new GeoComputeApiError(
        'ADMIN_REQUIRED',
        typeof detail === 'string' && detail ? detail : '需要管理员权限',
        403,
      );
    }
    const body = err.body as { code?: unknown; message?: unknown; detail?: unknown } | null;
    if (body && typeof body === 'object') {
      // GeoComputeError.to_dict() 形状：{detail: {code, message, details}}
      const detailObj =
        body.detail && typeof body.detail === 'object'
          ? (body.detail as { code?: unknown; message?: unknown; details?: unknown })
          : null;
      const code =
        typeof body.code === 'string'
          ? body.code
          : typeof detailObj?.code === 'string'
            ? detailObj.code
            : undefined;
      if (code) {
        const message =
          typeof body.message === 'string'
            ? body.message
            : typeof detailObj?.message === 'string'
              ? detailObj.message
              : code;
        const details =
          'details' in body ? body.details : detailObj && 'details' in detailObj ? detailObj.details : undefined;
        throw new GeoComputeApiError(code, message, err.status, details);
      }
      // FastAPI 422 的 {detail: [...]} 校验形状
      if (body.detail !== undefined) {
        throw new GeoComputeApiError(
          'GEOCOMPUTE_REQUEST_INVALID',
          typeof body.detail === 'string' ? body.detail : '请求被后端拒绝',
          err.status,
          body.detail,
        );
      }
    }
  }
  throw err;
}

type OwnerOpts = { ownerToken?: string | null; signal?: AbortSignal };

/* ------------------------------------------------------------------ */
/* Plan 校验 / 提交 / 执行                                              */
/* ------------------------------------------------------------------ */

/** ExecutionNodeIn —— 与后端 schema 逐字对齐（多余字段后端 extra=ignore）。 */
export interface ExecutionPlanNode {
  node_id: string;
  category: string;
  operation?: string;
  inputs?: string[];
  dataset_fingerprints?: Record<string, string>;
  parameters?: Record<string, unknown>;
  crs?: Record<string, unknown> | null;
  estimate?: Record<string, unknown> | null;
  policy?: string;
  reuse?: string;
  retry?: Record<string, unknown>;
  deadline_s?: number | null;
  cancellable?: boolean;
  locality_hint?: string | null;
  description?: string | null;
  lineage_inputs?: { ref_id: string; kind: string }[];
}

export interface ExecutionPlan {
  plan_id: string;
  nodes: ExecutionPlanNode[];
  budget?: Record<string, unknown>;
  description?: string | null;
}

export interface PlanValidation {
  plan_id: string;
  graph_fingerprint: string;
  node_fingerprints: Record<string, string>;
  waves: unknown[];
  wired_categories: string[];
}

export interface NodeEvidence {
  status: 'pending' | 'ready' | 'running' | 'completed' | 'reused' | 'failed' | 'cancelled' | 'skipped';
  attempts?: number;
  duration_s?: number | null;
  rows_emitted?: number | null;
  bytes_emitted?: number | null;
  output_ref?: string | null;
  output_summary?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  retry_safe?: boolean | null;
  fingerprint?: string | null;
  policy?: string | null;
  failure_codes?: string[];
  checkpoint_verified?: boolean | null;
}

/** ExecutionRun 投影 —— source=null 内存 / "snapshot" 快照回放 / "cluster" cluster 行。 */
export interface ExecutionRun {
  run_id: string;
  plan_id: string;
  plan_fingerprint: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'preempted';
  wall_time_s?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  source?: 'snapshot' | 'cluster' | null;
  evidence?: Record<string, NodeEvidence>;
  lineage?: unknown[];
  reproducibility?: Record<string, unknown>;
  progress?: { settled: number; done: number; failed: number; total: number };
}

/** cluster 提交的资源请求（ResourceRequest，extra=forbid —— 未知字段会被 422）。 */
export interface ClusterResourceRequest {
  min_mem_mb?: number;
  min_cpu?: number;
  gpu?: number;
  zone?: string | null;
  required_profiles?: string[];
  fallback_cpu?: boolean;
}

export interface ClusterSubmitResult {
  run_id: string;
  status: string;
  plan_fingerprint: string;
  required_profiles: string[];
  resource: Record<string, unknown> | null;
  source: 'cluster';
}

export interface DriftVerdict {
  state: 'current' | 'stale_runtime' | 'degraded_plan' | 'unknown';
  reason: string | null;
  stored_plan_fingerprint: string;
  current_plan_fingerprint: string;
  stored_runtime_fingerprint: string;
  current_runtime_fingerprint: string | null;
}

/** 校验 plan（不执行；可选认证）。 */
export async function validatePlan(
  plan: ExecutionPlan,
  opts: OwnerOpts = {},
): Promise<PlanValidation> {
  try {
    return await apiFetch<PlanValidation>('/api/v1/geocompute/plans/validate', {
      method: 'POST',
      body: plan,
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowTyped(err);
  }
}

/** 内存路径执行（同步返回 ExecutionRun 快照）。 */
export async function executePlan(
  plan: ExecutionPlan,
  opts: { sessionId?: string | null } & OwnerOpts = {},
): Promise<ExecutionRun> {
  try {
    return await apiFetch<ExecutionRun>('/api/v1/geocompute/plans/execute', {
      method: 'POST',
      body: { plan, session_id: opts.sessionId ?? null },
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowTyped(err);
  }
}

/** V6 cluster 提交（202；背压 429 携带 Retry-After: 5）。 */
export async function submitClusterRun(
  plan: ExecutionPlan,
  opts: {
    sessionId?: string | null;
    priority?: number;
    projectId?: string | null;
    resource?: ClusterResourceRequest | null;
  } & OwnerOpts = {},
): Promise<ClusterSubmitResult> {
  try {
    return await apiFetch<ClusterSubmitResult>('/api/v1/geocompute/plans/runs', {
      method: 'POST',
      body: {
        plan,
        session_id: opts.sessionId ?? null,
        ...(opts.priority !== undefined ? { priority: opts.priority } : {}),
        ...(opts.projectId ? { project_id: opts.projectId } : {}),
        ...(opts.resource ? { resource: opts.resource } : {}),
      },
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowTyped(err);
  }
}

/** 计划漂移检查（stored 计划 vs 当前 runtime 指纹）。 */
export async function checkPlanDrift(
  stored: Record<string, unknown>,
  opts: { plan?: ExecutionPlan } & OwnerOpts = {},
): Promise<DriftVerdict> {
  try {
    return await apiFetch<DriftVerdict>('/api/v1/geocompute/plans/drift-check', {
      method: 'POST',
      body: { stored, ...(opts.plan ? { plan: opts.plan } : {}) },
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowTyped(err);
  }
}

/* ------------------------------------------------------------------ */
/* Run 列表 / 详情 / 摘要 / 取消                                        */
/* ------------------------------------------------------------------ */

/** /runs 列表与 stuck 端点的行投影（控制面扫描列，字段逐字对齐 _scan_projection）。 */
export interface ClusterRunRow {
  run_id: string;
  status: string;
  owner_scope: string;
  plan_fingerprint: string;
  session_id: string | null;
  priority: number;
  attempts: number;
  preempts: number;
  lease_epoch: number;
  cancel_requested_at: string | null;
  yield_requested_at: string | null;
  error_code: string | null;
  required_profiles: string[];
  resource_request: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  terminal_at: string | null;
  heartbeat_at?: string | null;
  lease_expires_at?: string | null;
  /** 控制面扫描列（cluster/store.py _scan_projection 同款附加键）。 */
  id: number;
  tenant_key: string;
  coordinator_id: string | null;
  dispatch_seq: number;
}

export interface RunsListPage {
  runs: ClusterRunRow[];
  terminal_snapshots: { run_id: string; status: string; source: 'snapshot'; created_at: string }[];
  limit: number;
  offset: number;
}

export interface CancelRunResult {
  run_id: string;
  cancelled: boolean;
  requested?: boolean;
  status: string;
  source: 'cluster' | 'snapshot' | string | null;
}

export async function listClusterRuns(
  opts: { status?: string[]; limit?: number; offset?: number } & OwnerOpts = {},
): Promise<RunsListPage> {
  const params = new URLSearchParams();
  if (opts.status?.length) params.set('status', opts.status.join(','));
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.offset != null) params.set('offset', String(opts.offset));
  const qs = params.toString();
  try {
    return await apiFetch<RunsListPage>(
      `/api/v1/geocompute/runs${qs ? `?${qs}` : ''}`,
      { ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    rethrowTyped(err);
  }
}

/** run 详情（cluster 行投影形状；内存/快照命中的 ExecutionRun 形状由 source 字段区分）。 */
export async function getClusterRun(
  runId: string,
  opts: OwnerOpts = {},
): Promise<ExecutionRun | ClusterRunRow> {
  try {
    return await apiFetch<ExecutionRun | ClusterRunRow>(
      `/api/v1/geocompute/runs/${encodeURIComponent(runId)}`,
      { ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    rethrowTyped(err);
  }
}

export async function getRunSummary(
  runId: string,
  opts: OwnerOpts = {},
): Promise<{ lines: string[] }> {
  try {
    return await apiFetch<{ lines: string[] }>(
      `/api/v1/geocompute/runs/${encodeURIComponent(runId)}/summary`,
      { ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    rethrowTyped(err);
  }
}

/** 取消（cluster 路径返回 {requested}；内存/快照路径无 requested 字段）。 */
export async function cancelClusterRun(
  runId: string,
  opts: OwnerOpts = {},
): Promise<CancelRunResult> {
  try {
    return await apiFetch<CancelRunResult>(
      `/api/v1/geocompute/runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST', ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    rethrowTyped(err);
  }
}

/* ------------------------------------------------------------------ */
/* Cluster 运维面（require_admin）                                      */
/* ------------------------------------------------------------------ */

export interface LatencyDigest {
  p50_s: number | null;
  p95_s: number | null;
  samples: number;
}

export interface LedgerScopeUsage {
  scope_key: string;
  [dimension: string]: unknown;
}

export interface QuarantineRow {
  owner_scope: string;
  fingerprint: string;
  failure_count: number;
  active: boolean;
  last_error_code: string;
}

/** ADR-0133 五类观测指标 + V6 控制面计数的完整快照。 */
export interface ClusterMetrics {
  runs_by_status: Record<string, number>;
  queue_depth: number;
  inflight: number;
  completed: number;
  failed: number;
  cancelled: number;
  preempted_total: number;
  lease_loss_total: number;
  cancel_latency: LatencyDigest;
  queue_wait: LatencyDigest;
  waiting_by_profile: Record<string, number>;
  events_counters: Record<string, number>;
  workers: {
    live: number;
    by_role: Record<string, number>;
    profile_slots: Record<string, number>;
    gpu_workers: number;
  };
  leader: { count: number; ids: string[] };
  ledger: LedgerScopeUsage[];
  resource_rejections: Record<string, number>;
  oom_avoided: number;
  gpu_fallbacks: number;
  spill: { count: number; bytes: number; rehydrate_hits: number; rehydrate_misses: number };
  // ADR-0133 五类
  transfer: { bytes_total: number };
  cache: { worker_cache_hits: number };
  lineage: {
    node_completed: number;
    node_reused: number;
    node_lost: number;
    partition_planned: number;
    speculative_dispatched: number;
    poison_quarantined: number;
  };
  utilization: { reserved_units: number; capacity_units: number; ratio: number | null };
  quarantine: QuarantineRow[];
}

export interface ClusterWorker {
  worker_id: string;
  role: 'worker' | 'coordinator';
  profiles: Record<string, number>;
  /** 旧 worker 无能力披露 —— 诚实 null。 */
  capability: Record<string, unknown> | null;
  heartbeat_age_s: number;
  cache_entries: number;
  cache_bytes: number;
}

export interface StuckRunsPage {
  runs: ClusterRunRow[];
  count: number;
}

export interface ResetRunResult {
  run_id: string;
  outcome: 'requeued' | 'failed';
  status: string;
  attempts: number;
}

export interface LedgerLimits {
  scope_key: string;
  limit_rows: number | null;
  limit_bytes: number | null;
  limit_units: number | null;
  limit_mem_mb: number | null;
  limit_gpu: number | null;
}

/** 503 → ClusterUnavailableError（控制台显示降级态而非错误横幅）。 */
export class ClusterUnavailableError extends Error {
  constructor() {
    super('CLUSTER_UNAVAILABLE');
    this.name = 'ClusterUnavailableError';
  }
}

function rethrowClusterUnavailable(err: unknown): never {
  if (isApiError(err) && err.status === 503) throw new ClusterUnavailableError();
  rethrowTyped(err);
}

export async function getClusterMetrics(opts: OwnerOpts = {}): Promise<ClusterMetrics> {
  try {
    return await apiFetch<ClusterMetrics>('/api/v1/geocompute/cluster/metrics', {
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowClusterUnavailable(err);
  }
}

export async function getClusterWorkers(
  opts: OwnerOpts = {},
): Promise<{ workers: ClusterWorker[]; live: number }> {
  try {
    return await apiFetch<{ workers: ClusterWorker[]; live: number }>(
      '/api/v1/geocompute/cluster/workers',
      { ownerToken: opts.ownerToken ?? null, signal: opts.signal },
    );
  } catch (err) {
    rethrowClusterUnavailable(err);
  }
}

export async function getStuckRuns(opts: OwnerOpts = {}): Promise<StuckRunsPage> {
  try {
    return await apiFetch<StuckRunsPage>('/api/v1/geocompute/cluster/runs/stuck', {
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    rethrowClusterUnavailable(err);
  }
}

/** stuck run 干预：requeue（重派）或 failed（隔离/驱逐为终态失败）。 */
export async function resetStuckRun(
  runId: string,
  opts: { reason?: string } & OwnerOpts = {},
): Promise<ResetRunResult> {
  try {
    return await apiFetch<ResetRunResult>(
      `/api/v1/geocompute/cluster/runs/${encodeURIComponent(runId)}/reset`,
      {
        method: 'POST',
        body: { reason: opts.reason ?? 'admin reset' },
        ownerToken: opts.ownerToken ?? null,
        signal: opts.signal,
      },
    );
  } catch (err) {
    rethrowClusterUnavailable(err);
  }
}

export async function setLedgerLimits(
  req: LedgerLimits,
  opts: OwnerOpts = {},
): Promise<LedgerLimits> {
  try {
    return await apiFetch<LedgerLimits>('/api/v1/geocompute/cluster/ledger/limits', {
      method: 'POST',
      body: req,
      ownerToken: opts.ownerToken ?? null,
      signal: opts.signal,
    });
  } catch (err) {
    if (isApiError(err) && err.status === 500) {
      throw new GeoComputeApiError('LEDGER_LIMITS_FAILED', '账本限额写入失败', 500);
    }
    rethrowClusterUnavailable(err);
  }
}
