/**
 * Chat API - 对接后端 SSE 流式接口
 *
 * F-FE-3: 所有请求统一走 `./transport`（apiFetch / openStream），错误统一为
 * ApiError（含 status + body + requestId），超时由 transport 的
 * AbortController 模型处理，非幂等 POST 永不自动重试。
 */

import type { ToolResult } from '@/lib/types';
import { apiFetch, openStream } from './transport';
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
  | 'step_cancelled'
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
  /** Per-turn monotonic SSE event id (DUP-1). Absent on events the server
   * synthesized without an `id:` (e.g. resume terminal events). */
  id?: string;
}

/**
 * 发送流式对话请求，返回 AsyncGenerator
 *
 * SEC-08：ownerToken 仅在匿名会话首次创建后由前端持有；提供时附在
 * `X-Session-Token` 头里回传，后端据此放行该匿名会话的访问。
 *
 * 超时模型：openStream 只对"连接阶段"（拿到响应头之前）计时，一旦开始
 * 流式输出，时长由调用方的 signal 控制 —— 长 Agent turn 不会被计时器杀掉。
 *
 * DUP-1 resume：lastEventId（上一次收到的 SSE 事件 id）在重连时以
 * `Last-Event-ID` 头回传。后端把带该头的 POST 视为"续读"（replay 错过的
 * 事件，绝不重新执行 turn），而不是新 turn。未收到任何事件时传 "0"，
 * 表示"从缓冲开头重放"。
 */
export async function* streamChat(
  message: string,
  sessionId?: string,
  mapState?: Record<string, unknown>,
  signal?: AbortSignal,
  skillName?: string,
  ownerToken?: string | null,
  lastEventId?: string | number | null
): AsyncGenerator<SSEEvent> {
  const response = await openStream('/api/v1/chat/stream', {
    method: "POST",
    body: {
      message,
      session_id: sessionId,
      map_state: mapState,
      skill_name: skillName
    },
    signal,
    ownerToken,
    label: "Chat API error",
    ...(lastEventId !== undefined && lastEventId !== null
      ? { headers: { 'Last-Event-ID': String(lastEventId) } }
      : {}),
  });

  if (!response.body) throw new Error("No response body");

  // transport goal §9 / B-P2-10/11: delegate to the shared, spec-correct
  // parser (CRLF incl. cross-chunk, partial-UTF-8 EOF flush, [DONE] sentinel,
  // comment lines, data/event with or without leading space, abort, id:).
  // The inline parser that lived here had two latent bugs (a `data:` line split
  // across a CRLF chunk boundary was dropped, and the TextDecoder was never
  // flushed at EOF so a trailing partial multi-byte char was lost).
  for await (const ev of parseSSEStream(response.body, signal, { doneSentinel: "[DONE]" })) {
    yield {
      event: ev.event as SSEEventType,
      data: ev.data as Record<string, unknown> | string,
      ...(ev.id !== undefined ? { id: ev.id } : {}),
    };
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
  return apiFetch<{ content: string; session_id: string; owner_token?: string }>(
    '/api/v1/chat/completions',
    {
      method: "POST",
      body: { message, session_id: sessionId, map_state: mapState },
      ownerToken,
      label: "Chat API error",
    }
  );
}

/**
 * 获取会话历史列表
 */
export async function getSessionList() {
  return apiFetch('/api/v1/chat/sessions', { label: "API Error" });
}

/**
 * 获取会话详细内容
 *
 * SEC-08：匿名会话需提供 ownerToken 匹配 X-Session-Token 头。
 */
export async function getSessionDetail(sessionId: string, ownerToken?: string | null) {
  return apiFetch(`/api/v1/chat/sessions/${sessionId}`, { ownerToken, label: "API Error" });
}

/**
 * 删除会话
 *
 * SEC-08：匿名会话需提供 ownerToken 匹配 X-Session-Token 头。
 * DELETE 为幂等方法，但 transport 默认不重试；204 无响应体，parseJson: false。
 */
export async function deleteSession(sessionId: string, ownerToken?: string | null): Promise<void> {
  await apiFetch<void>(`/api/v1/chat/sessions/${sessionId}`, {
    method: "DELETE",
    ownerToken,
    label: "API Error",
    parseJson: false,
  });
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
 *
 * 非幂等 POST：工具执行会被当作新的一次执行，transport 保证永不自动重试。
 */
export async function executeToolDirect(
  tool: string,
  arguments_: Record<string, unknown>,
): Promise<ToolResult> {
  return apiFetch<ToolResult>('/api/v1/chat/tools/execute', {
    method: "POST",
    body: { tool, arguments: arguments_ },
    label: "Tool execute error",
  });
}
