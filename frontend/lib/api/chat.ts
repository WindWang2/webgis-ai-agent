/**
 * Chat API - 对接后端 SSE 流式接口
 */

import type { ToolResult } from '@/lib/types';
import { API_BASE } from './config';
import { parseSSEStream } from './sse-stream-parser';

export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: Array<{
    name: string;
    arguments: string;
  }>;
  toolResults?: Array<{
    name: string;
    result: ToolResult;
  }>;
}

export type SSEEventType =
  | 'message'
  | 'thinking'
  | 'planning'
  | 'acting'
  | 'observing'
  | 'done'
  | 'end'
  | 'content'
  | 'tool_call'
  | 'tool_result'
  | 'tool_error'
  | 'task_start'
  | 'step_start'
  | 'step_result'
  | 'step_error'
  | 'task_complete'
  | 'task_error'
  | 'task_cancelled'
  | 'session'
  | 'task_plan'
  | 'token'
  | 'error'
  | 'explorer_progress'
  | 'plan_ready'
  | 'plan_step_done'
  | 'plan_finalized';

export interface SSEEvent {
  event: SSEEventType;
  data: Record<string, unknown> | string;
}

/**
 * 发送流式对话请求，返回 AsyncGenerator
 *
 * SEC-08：ownerToken 仅在匿名会话首次创建后由前端持有；提供时附在
 * `X-Session-Token` 头里回传，后端据此放行该匿名会话的访问。
 */
export async function* streamChat(
  message: string,
  sessionId?: string,
  mapState?: Record<string, unknown>,
  signal?: AbortSignal,
  skillName?: string,
  ownerToken?: string | null
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/api/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(ownerToken ? { "X-Session-Token": ownerToken } : {}),
    },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      map_state: mapState,
      skill_name: skillName
    }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Chat API error: ${response.status}`);
  }

  if (!response.body) throw new Error("No response body");

  // transport goal §9 / B-P2-10/11: delegate to the shared, spec-correct
  // parser (CRLF incl. cross-chunk, partial-UTF-8 EOF flush, [DONE] sentinel,
  // comment lines, data/event with or without leading space, abort). The
  // inline parser that lived here had two latent bugs (a `data:` line split
  // across a CRLF chunk boundary was dropped, and the TextDecoder was never
  // flushed at EOF so a trailing partial multi-byte char was lost).
  for await (const ev of parseSSEStream(response.body, signal, { doneSentinel: "[DONE]" })) {
    yield { event: ev.event as SSEEventType, data: ev.data as Record<string, unknown> | string };
  }
}

/**
 * 非流式对话
 *
 * SEC-08：ownerToken 用于匿名会话回传；提供时附在 X-Session-Token 头。
 * 响应可能含 owner_token（新建匿名会话时由服务端签发），调用方需存储。
 */
export async function sendChat(
  message: string,
  sessionId?: string,
  mapState?: Record<string, unknown>,
  ownerToken?: string | null
): Promise<{ content: string; session_id: string; owner_token?: string }> {
  const response = await fetch(`${API_BASE}/api/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(ownerToken ? { "X-Session-Token": ownerToken } : {}),
    },
    body: JSON.stringify({ message, session_id: sessionId, map_state: mapState }),
  });

  if (!response.ok) {
    throw new Error(`Chat API error: ${response.status}`);
  }

  return response.json();
}

/**
 * 获取会话历史列表
 */
export async function getSessionList() {
  const res = await fetch(`${API_BASE}/api/v1/chat/sessions`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 获取会话详细内容
 *
 * SEC-08：匿名会话需提供 ownerToken 匹配 X-Session-Token 头。
 */
export async function getSessionDetail(sessionId: string, ownerToken?: string | null) {
  const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}`, {
    headers: ownerToken ? { "X-Session-Token": ownerToken } : {},
  });
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 删除会话
 *
 * SEC-08：匿名会话需提供 ownerToken 匹配 X-Session-Token 头。
 */
export async function deleteSession(sessionId: string, ownerToken?: string | null): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}`, {
    method: "DELETE",
    headers: ownerToken ? { "X-Session-Token": ownerToken } : {},
  });
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
}

/**
 * 清空会话消息（保留会话）。
 *
 * FE-14：之前此函数与 deleteSession 完全相同（后端无 /clear 路由），且无调用方。
 * 后端不支持"仅清消息保留 session"——删除此误导性函数。如需此功能，后端需先实现。
 * 保留 deleteSession 作为唯一的 session 删除入口。
 */

/**
 * 直接执行单个工具（REST API，不依赖SSE）。
 *
 * 审计契约断裂：前端之前发 { tool, argument }（单数），后端 ToolExecuteRequest
 * 期望 { tool, arguments }（复数）→ 参数被 pydantic 默认值 {} 覆盖，工具收到
 * 空参数。改为匹配后端字段名。
 */
export async function executeToolDirect(
  tool: string,
  arguments_: Record<string, unknown>,
): Promise<ToolResult> {
  const res = await fetch(`${API_BASE}/api/v1/chat/tools/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, arguments: arguments_ }),
  });
  if (!res.ok) throw new Error(`Tool execute error: ${res.status}`);
  return res.json();
}
