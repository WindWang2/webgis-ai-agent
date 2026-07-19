/**
 * Task API - 任务管理接口
 */

import { API_BASE } from './config';

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

/**
 * 获取任务详情
 */
export async function getTask(taskId: string): Promise<TaskInfo> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`);
  if (!response.ok) {
    throw new Error(`Task API error: ${response.status}`);
  }
  return response.json();
}

/**
 * 获取任务列表。
 *
 * 审计契约：后端要求 session_id 必填（防跨租户泄漏所有用户任务），
 * 前端这里同步改为必填。
 */
export async function listTasks(
  sessionId: string,
): Promise<{ tasks: TaskInfo[] }> {
  const url = `${API_BASE}/api/v1/tasks?session_id=${encodeURIComponent(sessionId)}`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Task API error: ${response.status}`);
  }
  return response.json();
}

/**
 * 取消任务。
 *
 * 审计契约断裂：前端之前 POST /tasks/{id}/cancel，后端实际路由是
 * DELETE /tasks/{id}（无 /cancel 后缀）→ 一直 404。改为匹配后端。
 */
export async function cancelTask(taskId: string): Promise<{ cancelled: boolean }> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Task API error: ${response.status}`);
  }
  return response.json();
}
