/**
 * geocompute 客户端（V7 Phase G）—— 消费执行平面既有端点，不重写 runtime。
 *
 * - GET /runs/{id}/events：有界 observability trace（封闭词表 event、
 *   after_id 断点续读游标；run 行 retention 清理后 404 —— 调用方须诚实
 *   披露「事件不可用」而非静默空表）。
 * - POST /runs/{id}/cancel：取消。
 */
import { apiFetch, isApiError } from './transport';

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
