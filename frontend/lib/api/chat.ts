/**
 * Chat API - 对接后端 SSE 流式接口
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: Array<{
    name: string;
    arguments: string;
  }>;
  toolResults?: Array<{
    name: string;
    result: any;
  }>;
}

export type SSEEventType =
  | 'message'
  | 'thinking'
  | 'planning'
  | 'acting'
  | 'observing'
  | 'done'
  | 'tool_error'
  | 'task_start'
  | 'step_start'
  | 'step_result'
  | 'step_error'
  | 'task_complete'
  | 'task_error'
  | 'task_cancelled';

export interface SSEEvent {
  event: SSEEventType;
  data: any;
}

/**
 * 发送流式对话请求，返回 AsyncGenerator
 */
export async function* streamChat(
  message: string,
  sessionId?: string
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (!response.ok) {
    throw new Error(`Chat API error: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentData = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        currentData += line.slice(6);
      } else if (line === "" && currentEvent && currentData) {
        // Empty line = end of event
        try {
          yield { event: currentEvent, data: JSON.parse(currentData) };
        } catch {
          yield { event: currentEvent, data: currentData };
        }
        currentEvent = "";
        currentData = "";
      }
    }
  }
}

/**
 * 非流式对话
 */
export async function sendChat(
  message: string,
  sessionId?: string
): Promise<{ content: string; session_id: string }> {
  const response = await fetch(`${API_BASE}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
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
  const res = await fetch(`${API_BASE}/chat/sessions`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 获取会话详细内容
 */
export async function getSessionDetail(sessionId: string) {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 删除会话
 */
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
}

/**
 * 清空会话消息（保留会话）
 */
export async function clearSessionMessages(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/clear`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
}

/**
 * 直接执行单个工具（REST API，不依赖SSE）
 */
export async function executeToolDirect(tool: string, argument: Record<string, any>): Promise<any> {
  const res = await fetch(`${API_BASE}/chat/tools/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, argument }),
  });
  if (!res.ok) throw new Error(`Tool execute error: ${res.status}`);
  return res.json();
}
