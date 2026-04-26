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
 * 获取任务列表
 */
export async function listTasks(
  sessionId?: string
): Promise<{ tasks: TaskInfo[] }> {
  const url = sessionId
    ? `${API_BASE}/api/v1/tasks?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/api/v1/tasks`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Task API error: ${response.status}`);
  }
  return response.json();
}

/**
 * 取消任务
 */
export async function cancelTask(taskId: string): Promise<{ cancelled: boolean }> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/cancel`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Task API error: ${response.status}`);
  }
  return response.json();
}
