/**
 * T005 AI Chat API Client
 * 对接后端 /api/v1/chat 接口
 */

import type { ChatMessage, ChatSession } from '../types/chat';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export interface ChatResponse {
  code: string;
  success: boolean;
  message: string;
  data: {
    session_id: string;
    message: string;
    timestamp: number;
  };
}

export interface SessionListResponse {
  code: string;
  success: boolean;
  data: {
    sessions: Array<{
      id: string;
      title: string;
      created_at: number;
      updated_at: number;
      message_count: number;
    }>;
  };
}

export interface SessionDetailResponse {
  code: string;
  success: boolean;
  data: {
    id: string;
    title: string;
    messages: ChatMessage[];
    created_at: number;
    updated_at: number;
  };
}

/**
 * 发送聊天消息
 */
export async function sendChatMessage(
  message: string,
  sessionId?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      session_id: sessionId || null,
    }),
  });
  
  if (!res.ok) {
    throw new Error(`API Error: ${res.status}`);
  }
  
  return res.json();
}

/**
 * 获取会话历史列表
 */
export async function getSessionList(): Promise<SessionListResponse> {
  const res = await fetch(`${API_BASE}/chat/sessions`);
  
  if (!res.ok) {
    throw new Error(`API Error: ${res.status}`);
  }
  
  return res.json();
}

/**
 * 获取会话详细内容
 */
export async function getSessionDetail(sessionId: string): Promise<SessionDetailResponse> {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}`);
  
  if (!res.ok) {
    throw new Error(`API Error: ${res.status}`);
  }
  
  return res.json();
}

/**
 * 删除会话
 */
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  
  if (!res.ok) {
    throw new Error(`API Error: ${res.status}`);
  }
}

/**
 * 清空会话消息（保留会话）
 */
export async function clearSessionMessages(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/clear`, {
    method: 'DELETE',
  });
  
  if (!res.ok) {
    throw new Error(`API Error: ${res.status}`);
  }
}