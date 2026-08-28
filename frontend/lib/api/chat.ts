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

/**
 * Chat/explorer SSE event names.
 *
 * Real backend emitters (app/services/chat + chat.py + event_resume,
 * explorer orchestrator): token, content, tool_call, tool_result, step_*,
 * task_*, session, plan_*, done, error, keep_alive, resume_gap,
 * explorer_progress, heartbeat.
 *
 * Ghost names kept because tests / historical clients still emit them:
 * message, thinking, planning, acting, observing, end, tool_error, task_plan.
 *
 * keep_alive / heartbeat are pings — no handler (skip). tool_result and
 * resume_gap are handled in use-sse-stream.
 */
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
  | 'plan_finalized'
  | 'keep_alive'
  | 'resume_gap'
  | 'heartbeat';

export interface SSEEvent {
  event: SSEEventType;
  data: Record<string, unknown> | string;
  /** Per-turn monotonic SSE event id (DUP-1). Absent on events the server
   * synthesized without an `id:` (e.g. resume terminal events). */
  id?: string;
}

/**
 * `step_result` 事件载荷的最小契约（#1009：SSE 层去 any 的第一步）。
 *
 * 只声明前端实际读取的字段；`result` 是后端工具结果的有界投影（slim_event_result），
 * 其内部结构因工具而异，故保持 Record 兜底 + 已知可选字段显式化。
 * 新字段先在这里声明再使用——类型错误即契约漂移信号。
 */
export type StepResultPayload = {
  tool?: string;
  name?: string;
  arguments?: string;
  /** 数据 ref（ref:geojson-*）——挂载图层 id 即该值。 */
  geojson_ref?: string;
  /** 会话数据引用描述符（执行引擎附加，V3 性能通道）。 */
  ref_descriptor?: import('@/lib/types/layer').RefDescriptor;
  /** 工具结果有界投影；已知形状见各分支。 */
  result?: {
    success?: boolean;
    type?: string;
    task_id?: string;
    image?: unknown;
    command?: string;
    plan_id?: string;
    title?: string;
    summary?: string;
    step_count?: number;
    destructive_steps?: string[];
    steps_preview?: Array<{ id?: string; tool?: string; purpose?: string; destructive?: boolean } & Record<string, unknown>>;
    legend_spec?: import('@/lib/map-kit/types').LegendSpec;
    layer_meta?: { title?: string | null } & Record<string, unknown>;
    runtime_patch?: {
      visible?: boolean;
      opacity?: number;
      layer_id?: string;
      mapspec_fingerprint?: string;
      projection_fingerprint?: string;
      repair_attempts?: Array<Record<string, unknown>>;
      style?: Record<string, unknown>;
      legend_spec?: import('@/lib/map-kit/types').LegendSpec;
      image_ref?: string;
    } & Record<string, unknown>;
    chart?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  } & Record<string, unknown>;
  /** 后端投影的 committed MapSpec（组件/图层突变事件携带）。 */
  mapspec?: Record<string, unknown>;
  mutation_revision?: number;
  /** 会话 id（跨会话事件守卫 INV-2 读取）。 */
  session_id?: string;
} & Record<string, unknown>;

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
  lastEventId?: string | number | null,
  /** #558: 当前选中的项目 workspace id —— 有项目时才携带，请求体 project_id
   * 后端据此渲染项目上下文摘要块。不猜、不空发。 */
  projectId?: string | null
): AsyncGenerator<SSEEvent> {
  const response = await openStream('/api/v1/chat/stream', {
    method: "POST",
    body: {
      message,
      session_id: sessionId,
      map_state: mapState,
      skill_name: skillName,
      ...(projectId ? { project_id: projectId } : {}),
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
  ownerToken?: string | null,
  /** #558: 当前选中的项目 workspace id（有项目时才携带，见 streamChat）。 */
  projectId?: string | null
): Promise<{ content: string; session_id: string; owner_token?: string }> {
  return apiFetch<{ content: string; session_id: string; owner_token?: string }>(
    '/api/v1/chat/completions',
    {
      method: "POST",
      body: {
        message,
        session_id: sessionId,
        map_state: mapState,
        ...(projectId ? { project_id: projectId } : {}),
      },
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
 * 获取当前 SessionPlan 信封投影（Pi 路径面板水合源，#1047）。
 *
 * SEC-08：匿名会话需提供 ownerToken 匹配 X-Session-Token 头。
 * 后端无信封时回 204，apiFetch 解析为 undefined（A-F-14）——「没有计划」
 * 是正常态，调用方据此隐藏面板而不是报错。
 * 只读：后端只返回当前信封，绝无历史列表。
 */
export async function getSessionPlan(
  sessionId: string,
  ownerToken?: string | null
): Promise<import('@/lib/types/session-plan').SessionPlanProjection | undefined> {
  return apiFetch<import('@/lib/types/session-plan').SessionPlanProjection | undefined>(
    `/api/v1/chat/sessions/${sessionId}/plan`,
    { ownerToken, label: "API Error" }
  );
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
