/**
 * 统一任务中心 API 客户端（ADR-0052）。
 *
 * 对应后端 `/api/v1/tasks/jobs*`。同一个 JobView 形状同时承载 agent task 与
 * durable GIS job，所以任务中心只需处理一种类型。
 *
 * 走 `apiFetch`（lib/api/transport）而不是裸 fetch —— 由它统一处理超时、
 * X-Request-ID、ApiError 归一化与 ownerToken → X-Session-Token 头。
 */
import { apiFetch } from './transport';

/** 后端 JobStatus。cancelling/stale 是 ADR-0052 新增语义。 */
export type JobStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'stale';

export type JobKind = 'agent' | 'analysis' | 'workflow' | 'explorer';

export interface JobView {
  id: string;
  kind: JobKind | string;
  name: string;
  status: JobStatus;
  /** 0–100；null 表示不确定进度（后端明确不编造假百分比） */
  progress: number | null;
  message: string | null;
  cancellable: boolean;
  retryable: boolean;
  active: boolean;
  attempt: number;
  session_id: string | null;
  project_id: string | null;
  agent_task_id: string | null;
  agent_step_id: string | null;
  /** agent task 派生出的后台 durable job（Turn → Step → Job 链） */
  background_job_ids: string[];
  /** 单行错误摘要。后端保证不含 traceback */
  error: string | null;
  result_ref: string | null;
  step_count: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
}

export interface JobListResponse {
  jobs: JobView[];
  /** 是否仍有活跃 job。false → 前端停止轮询（规范 §32） */
  has_active: boolean;
  /** 后端建议的下次轮询间隔；null 表示不要再轮询 */
  poll_after_ms: number | null;
}

export interface JobCancelResponse {
  id: string;
  status: JobStatus;
  /** 本次调用是否真的改变了状态。重复取消为 false，但仍是 200（幂等） */
  cancel_requested: boolean;
  cancelling: boolean;
}

export interface JobRetryResponse {
  id: string;
  status: JobStatus;
  retried: boolean;
  reason: string;
  attempt: number;
}

export interface ListJobsOptions {
  sessionId?: string | null;
  activeOnly?: boolean;
  limit?: number;
  ownerToken?: string | null;
  signal?: AbortSignal;
}

/** 未终结状态集合。前端据此决定 UI 与是否继续轮询。 */
export const ACTIVE_JOB_STATUSES: readonly JobStatus[] = [
  'pending',
  'queued',
  'running',
  'cancelling',
];

export function isJobActive(status: JobStatus): boolean {
  return ACTIVE_JOB_STATUSES.includes(status);
}

export async function listJobs(options: ListJobsOptions = {}): Promise<JobListResponse> {
  const params = new URLSearchParams();
  if (options.sessionId) params.set('session_id', options.sessionId);
  if (options.activeOnly) params.set('active_only', 'true');
  if (options.limit) params.set('limit', String(options.limit));
  const qs = params.toString();

  return apiFetch<JobListResponse>(`/api/v1/tasks/jobs${qs ? `?${qs}` : ''}`, {
    method: 'GET',
    ownerToken: options.ownerToken ?? undefined,
    signal: options.signal,
    label: 'Task center API error',
  });
}

export async function getJob(
  jobId: string,
  options: { ownerToken?: string | null; signal?: AbortSignal } = {},
): Promise<JobView> {
  return apiFetch<JobView>(`/api/v1/tasks/jobs/${encodeURIComponent(jobId)}`, {
    method: 'GET',
    ownerToken: options.ownerToken ?? undefined,
    signal: options.signal,
    label: 'Task detail API error',
  });
}

/**
 * 请求取消 job。后端幂等：重复取消返回 200 且 cancel_requested=false。
 *
 * 注意返回的 status 可能仍是 `cancelling` —— UI 必须显示「取消中…」而不是直接
 * 显示「已取消」，只有后端到达终态才算取消完成（规范 §30）。
 */
export async function cancelJob(
  jobId: string,
  options: { ownerToken?: string | null; signal?: AbortSignal } = {},
): Promise<JobCancelResponse> {
  return apiFetch<JobCancelResponse>(`/api/v1/tasks/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
    ownerToken: options.ownerToken ?? undefined,
    signal: options.signal,
    label: 'Task cancel API error',
  });
}

/** 重试 failed/stale job（新 attempt）。被取消的 job 后端会拒绝。 */
export async function retryJob(
  jobId: string,
  options: { ownerToken?: string | null; signal?: AbortSignal } = {},
): Promise<JobRetryResponse> {
  return apiFetch<JobRetryResponse>(
    `/api/v1/tasks/jobs/${encodeURIComponent(jobId)}/retry`,
    {
      method: 'POST',
      ownerToken: options.ownerToken ?? undefined,
      signal: options.signal,
      label: 'Task retry API error',
    },
  );
}
