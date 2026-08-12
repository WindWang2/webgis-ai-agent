/**
 * Task API - 任务管理接口
 *
 * F-FE-3 migration: previously raw fetch + plain Error with status only.
 * Now routes through the shared transport (typed ApiError, abort, timeout,
 * request id). GETs go through the Fast Path (in-flight dedup + 5s LRU).
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const TASK_LABEL = 'Task API error';

export interface TaskStepInfo {
  id: string;
  tool: string;
  status: "running" | "completed" | "failed";
  error?: string;
}

export interface TaskInfo {
  task_id: string;
  session_id: string;
  original_request: string;
  status: "running" | "completed" | "failed" | "cancelled";
  steps: TaskStepInfo[];
}

/** GET /api/v1/tasks/{id} — task detail. */
export async function getTask(
  taskId: string,
  opts?: { signal?: AbortSignal }
): Promise<TaskInfo> {
  const result = await fastGet<TaskInfo>(`/api/v1/tasks/${taskId}`, {
    signal: opts?.signal,
    label: TASK_LABEL,
  });
  return result.data;
}

/** GET /api/v1/tasks?session_id=… — task list, scoped per session. */
export async function listTasks(
  sessionId: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal }
): Promise<{ tasks: TaskInfo[] }> {
  const result = await fastGet<{ tasks: TaskInfo[] }>('/api/v1/tasks', {
    params: { session_id: sessionId },
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: TASK_LABEL,
  });
  return result.data;
}

/** DELETE /api/v1/tasks/{id} — cancel task. */
export async function cancelTask(taskId: string): Promise<{ cancelled: boolean }> {
  const out = await apiFetch<{ cancelled: boolean }>(`/api/v1/tasks/${taskId}`, {
    method: "DELETE",
    label: TASK_LABEL,
  });
  invalidateCache('/api/v1/tasks');
  return out;
}
