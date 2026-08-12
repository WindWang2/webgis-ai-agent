/**
 * Explorer API client.
 *
 * F-FE-3 migration: previously raw fetch + plain Error. The streaming
 * endpoint continues to use the shared SSE stream parser (with proper EOF
 * flush and reader cancel on abort — A-F-06); it now flows through the
 * shared transport's `openStream` so connect-phase failures also surface
 * as ApiError with status/requestId.
 */

import { apiFetch, openStream } from './transport';
import { fastGet } from './get-fast-path';
import { parseSSEStream } from './sse-stream-parser';

const EXPLORER_LABEL = 'Explorer API error';

export interface StartExploreRequest {
  query: string;
  session_id?: string;
  expected_data_type?: string;
  source_hint?: string[];
  auto_threshold?: number;
}

export async function startExploration(req: StartExploreRequest): Promise<{ task_id: string; status: string }> {
  return apiFetch<{ task_id: string; status: string }>('/api/v1/explorer/start', {
    method: 'POST',
    body: req,
    label: `${EXPLORER_LABEL} (start)`,
  });
}

export async function getExplorerStatus(
  taskId: string,
  opts?: { signal?: AbortSignal }
): Promise<{
  task_id: string;
  status: string;
  progress: number;
  result: unknown;
}> {
  const result = await fastGet<{
    task_id: string;
    status: string;
    progress: number;
    result: unknown;
  }>(`/api/v1/explorer/status/${taskId}`, {
    signal: opts?.signal,
    // Status is a polling endpoint — 1s cache to dedupe near-simultaneous polls.
    ttlMs: 1_000,
    label: `${EXPLORER_LABEL} (status)`,
  });
  return result.data;
}

export async function abortExploration(taskId: string): Promise<{ task_id: string; aborted: boolean }> {
  return apiFetch<{ task_id: string; aborted: boolean }>(
    `/api/v1/explorer/abort/${taskId}`,
    {
      method: 'POST',
      label: `${EXPLORER_LABEL} (abort)`,
    }
  );
}

export async function* streamExplorerProgress(
  taskId: string,
  signal?: AbortSignal
): AsyncGenerator<{
  event: string;
  data: Record<string, unknown>;
}> {
  const response = await openStream(
    `/api/v1/explorer/stream/${taskId}`,
    { signal, label: `${EXPLORER_LABEL} (stream)` }
  );
  if (!response.body) throw new Error('No response body');

  for await (const ev of parseSSEStream(response.body, signal)) {
    const data =
      typeof ev.data === 'string'
        ? { raw: ev.data }
        : (ev.data as Record<string, unknown>);
    yield { event: ev.event, data };
  }
}
