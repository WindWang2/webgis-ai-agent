/**
 * Workflow Runtime V5/V6 typed client（ADR-0142 D2 —— runtime-inspector 复活
 * 与 ops 控制台运行时分区的数据通道）。
 *
 * 端点契约逐字对照 app/api/routes/workflow_runtime.py @8b5b8375（13 端点）。
 * 仓内此前无此客户端模块 —— runtime-inspector.tsx 的 fetcher 一直无生产注入方
 * （#P1 接线复活）。
 *
 * 错误模型：后端统一 `detail = "{CODE}: {detail}"[:300]` 字符串 —— 本客户端
 * 解析错误码前缀，折叠为 WorkflowRuntimeApiError{code, message, status}。
 * 词表（contracts.py，封闭）：
 * - NodeState: PENDING READY RUNNING SUCCEEDED FAILED BLOCKED SKIPPED CANCELLED STALE
 * - InstanceStatus: running succeeded failed cancelled superseded
 */
import { apiFetch, isApiError } from './transport';

/* ------------------------------------------------------------------ */
/* 类型（instance_projection / 节点投影 / explain，字段逐字）            */
/* ------------------------------------------------------------------ */

export interface WorkflowNode {
  node_id: string;
  state: string;
  attempts: number;
  error_code: string | null;
  bound_ref: string | null;
  output_ref: string | null;
  reused: boolean;
  reuse_evidence: Record<string, unknown>;
  binding_violations: string[];
}

export interface WorkflowExplain {
  why_recomputed: string[];
  why_reused: string[];
  blocked: { node: string; codes: string[] }[];
}

export interface WorkflowInstance {
  instance_id: string;
  package_id: string;
  package_version: string;
  package_fingerprint: string;
  status: string;
  revision: number;
  cancel_requested: boolean;
  nodes: WorkflowNode[];
  counts: Record<string, number>;
  decisions: Record<string, unknown>[];
  pending_changes: Record<string, unknown>[];
  error_code: string | null;
  error_detail: string | null;
  methodology_family?: string;
  compiler_version?: string;
  explain?: WorkflowExplain;
}

export interface WorkflowPackage {
  package_id: string;
  version: string;
  fingerprint?: string;
  status?: string;
  [key: string]: unknown;
}

export interface WorkflowInstanceRow {
  instance_id: string;
  package_id: string;
  status: string;
  [key: string]: unknown;
}

export interface RuntimeJournalEvent {
  id?: number;
  node_id: string | null;
  kind: string;
  from_state: string | null;
  to_state: string | null;
  reason: string | null;
  actor: string | null;
  attempt: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RecomputePlanView {
  instance_id: string;
  stale: number;
  counts: Record<string, number>;
  decisions: Record<string, unknown>[];
  /** GET 变体（service.recompute_plan）附加的规划视图字段。 */
  style_only?: boolean;
  recompute?: string[];
  reuse?: string[];
  would_mark_stale?: string[];
  explanations?: string[];
  changed_dimensions?: string[];
}

export interface NodeDetail {
  instance: {
    instance_id: string;
    status: string;
    run_lease_owner: string | null;
    run_lease_expires_at: string | null;
  };
  node: WorkflowNode & Record<string, unknown>;
}

export interface DebugBundle {
  instance: WorkflowInstance;
  recent_events: RuntimeJournalEvent[];
  children: { instance_id: string; parent_node_id: string; status: string; package_id: string }[];
}

/* ------------------------------------------------------------------ */
/* 错误模型                                                            */
/* ------------------------------------------------------------------ */

export class WorkflowRuntimeApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'WorkflowRuntimeApiError';
  }
}

/** 后端 detail 形如 "INSTANCE_BUSY: instance is running" —— 解析 CODE 前缀。 */
function parseRuntimeError(err: unknown): never {
  if (isApiError(err)) {
    const detail = (err.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') {
      const sep = detail.indexOf(':');
      const code = sep > 0 ? detail.slice(0, sep).trim() : '';
      // 仅当前缀是已知大写错误码词表形状时折叠，否则整串作 message。
      if (sep > 0 && /^[A-Z][A-Z0-9_]+$/.test(code)) {
        throw new WorkflowRuntimeApiError(code, detail.slice(sep + 1).trim() || code, err.status);
      }
      // owner 隔离 404 是 {"detail": "not found"} —— 语义化为 INSTANCE_NOT_FOUND
      if (err.status === 404) {
        throw new WorkflowRuntimeApiError('INSTANCE_NOT_FOUND', detail, 404);
      }
      throw new WorkflowRuntimeApiError('WORKFLOW_RUNTIME_ERROR', detail, err.status);
    }
    // owner 隔离 404 是 {"detail": "not found"} —— 语义化为 INSTANCE_NOT_FOUND
    if (err.status === 404) {
      throw new WorkflowRuntimeApiError('INSTANCE_NOT_FOUND', 'not found', 404);
    }
    throw new WorkflowRuntimeApiError('WORKFLOW_RUNTIME_ERROR', `HTTP ${err.status}`, err.status);
  }
  throw err;
}

type OwnerOpts = { ownerToken?: string | null; signal?: AbortSignal };

async function rt<T>(path: string, init: OwnerOpts & { method?: string; body?: unknown } = {}): Promise<T> {
  try {
    return await apiFetch<T>(`/api/v1/workflow-runtime${path}`, {
      ownerToken: init.ownerToken ?? null,
      signal: init.signal,
      ...(init.method ? { method: init.method } : {}),
      ...(init.body !== undefined ? { body: init.body } : {}),
    });
  } catch (err) {
    parseRuntimeError(err);
  }
}

/* ------------------------------------------------------------------ */
/* Packages                                                            */
/* ------------------------------------------------------------------ */

export async function listPackages(opts: OwnerOpts = {}): Promise<WorkflowPackage[]> {
  const res = await rt<{ success: boolean; packages: WorkflowPackage[] }>('/packages', opts);
  return res.packages ?? [];
}

export async function listPackageVersions(
  packageId: string,
  opts: OwnerOpts = {},
): Promise<WorkflowPackage[]> {
  const res = await rt<{ success: boolean; versions: WorkflowPackage[] }>(
    `/packages/${encodeURIComponent(packageId)}/versions`,
    opts,
  );
  return res.versions ?? [];
}

export async function registerPackage(
  req: {
    query: string;
    recipe_id?: string;
    project_id?: string;
    profile?: Record<string, unknown> | null;
  },
  opts: OwnerOpts = {},
): Promise<Record<string, unknown>> {
  return rt<Record<string, unknown>>('/packages/register', { ...opts, method: 'POST', body: req });
}

export async function publishPackage(
  packageId: string,
  version: string,
  opts: OwnerOpts = {},
): Promise<Record<string, unknown>> {
  return rt<Record<string, unknown>>(`/packages/${encodeURIComponent(packageId)}/publish`, {
    ...opts,
    method: 'POST',
    body: { version },
  });
}

/* ------------------------------------------------------------------ */
/* Instances                                                           */
/* ------------------------------------------------------------------ */

export async function listInstances(opts: OwnerOpts = {}): Promise<WorkflowInstanceRow[]> {
  const res = await rt<{ success: boolean; instances: WorkflowInstanceRow[] }>(
    '/instances',
    opts,
  );
  return res.instances ?? [];
}

/** 实例详情（自带 explain —— runtime-inspector 的 fetcher 走这里，ADR-0142 D2）。 */
export async function getInstance(
  instanceId: string,
  opts: OwnerOpts = {},
): Promise<WorkflowInstance> {
  const res = await rt<{ success: boolean; instance: WorkflowInstance }>(
    `/instances/${encodeURIComponent(instanceId)}`,
    opts,
  );
  return res.instance;
}

export async function createInstance(
  req: { package_id: string; version?: string; session_id?: string; project_id?: string },
  opts: OwnerOpts = {},
): Promise<WorkflowInstance> {
  const res = await rt<{ success: boolean; instance: WorkflowInstance }>('/instances', {
    ...opts,
    method: 'POST',
    body: req,
  });
  return res.instance;
}

export async function runInstance(
  instanceId: string,
  opts: { deadlineS?: number } & OwnerOpts = {},
): Promise<{ run: Record<string, unknown>; instance: WorkflowInstance }> {
  return rt(`/instances/${encodeURIComponent(instanceId)}/run`, {
    ...opts,
    method: 'POST',
    body: { deadline_s: opts.deadlineS ?? 60.0 },
  });
}

export async function cancelInstance(
  instanceId: string,
  opts: OwnerOpts = {},
): Promise<{ cancelled: boolean; status: string }> {
  return rt(`/instances/${encodeURIComponent(instanceId)}/cancel`, {
    ...opts,
    method: 'POST',
    body: {},
  });
}

export async function cloneInstance(
  instanceId: string,
  opts: {
    sessionId?: string | null;
    onlyNodes?: string[];
    skipNodes?: string[];
  } & OwnerOpts = {},
): Promise<Record<string, unknown>> {
  return rt(`/instances/${encodeURIComponent(instanceId)}/clone`, {
    ...opts,
    method: 'POST',
    body: {
      ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
      ...(opts.onlyNodes ? { only_nodes: opts.onlyNodes } : {}),
      ...(opts.skipNodes ? { skip_nodes: opts.skipNodes } : {}),
    },
  });
}

/* ------------------------------------------------------------------ */
/* 观测 / 干预                                                         */
/* ------------------------------------------------------------------ */

export async function getInstanceEvents(
  instanceId: string,
  opts: { limit?: number; afterId?: number; kind?: string } & OwnerOpts = {},
): Promise<RuntimeJournalEvent[]> {
  const params = new URLSearchParams();
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.afterId != null) params.set('after_id', String(opts.afterId));
  if (opts.kind) params.set('kind', opts.kind);
  const qs = params.toString();
  const res = await rt<{ success: boolean; events: RuntimeJournalEvent[] }>(
    `/instances/${encodeURIComponent(instanceId)}/events${qs ? `?${qs}` : ''}`,
    opts,
  );
  return res.events ?? [];
}

export async function getRecomputePlan(
  instanceId: string,
  opts: OwnerOpts = {},
): Promise<RecomputePlanView> {
  const res = await rt<{ success: boolean } & RecomputePlanView>(
    `/instances/${encodeURIComponent(instanceId)}/recompute-plan`,
    opts,
  );
  const { success: _success, ...view } = res;
  return view;
}

export async function getNodeDetail(
  instanceId: string,
  nodeId: string,
  opts: OwnerOpts = {},
): Promise<NodeDetail> {
  return rt(
    `/instances/${encodeURIComponent(instanceId)}/nodes/${encodeURIComponent(nodeId)}`,
    opts,
  );
}

export async function retryNode(
  instanceId: string,
  nodeId: string,
  opts: { force?: boolean } & OwnerOpts = {},
): Promise<{ node: string; state: string; attempts: number; force: boolean }> {
  const qs = opts.force ? '?force=true' : '';
  return rt(
    `/instances/${encodeURIComponent(instanceId)}/nodes/${encodeURIComponent(nodeId)}/retry${qs}`,
    { ...opts, method: 'POST', body: {} },
  );
}

export async function cancelNodes(
  instanceId: string,
  nodeIds: string[],
  opts: { includeDescendants?: boolean } & OwnerOpts = {},
): Promise<{ requested: string[]; flagged: string[] }> {
  return rt(`/instances/${encodeURIComponent(instanceId)}/nodes/cancel`, {
    ...opts,
    method: 'POST',
    body: {
      node_ids: nodeIds,
      include_descendants: opts.includeDescendants ?? true,
    },
  });
}

/** 增量变更申请（dry_run=true 返回 recompute-plan 形状预览）。 */
export async function applyChanges(
  instanceId: string,
  changes: { dimension: string; target_kind: string; target: string; detail: string }[],
  opts: { dryRun?: boolean } & OwnerOpts = {},
): Promise<Record<string, unknown>> {
  return rt(`/instances/${encodeURIComponent(instanceId)}/changes`, {
    ...opts,
    method: 'POST',
    body: { changes, dry_run: opts.dryRun ?? false },
  });
}

export async function getDebugBundle(
  instanceId: string,
  opts: OwnerOpts = {},
): Promise<DebugBundle> {
  const res = await rt<{ success: boolean } & DebugBundle>(
    `/instances/${encodeURIComponent(instanceId)}/debug`,
    opts,
  );
  const { success: _success, ...bundle } = res;
  return bundle;
}
