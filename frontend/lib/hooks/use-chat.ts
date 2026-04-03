'use client';
import { useState, useCallback, useEffect } from 'react';
import type { ChatMessage, ChatSession } from '@/lib/types/chat';
import {
  sendChatMessage,
  getSessionList,
  getSessionDetail,
  deleteSession as apiDeleteSession,
  clearSessionMessages,
} from '@/lib/api/chat-mock';

/**
 * Chat Hook - 管理聊天会话状态和API调用
 * T005: 后端API对接、会话历史管理
 */
interface UseChatOptions {
  /** 初始会话ID */
  initialSessionId?: string;
}

interface UseChatReturn {
  // State
  messages: ChatMessage[];
  sessionId: string | null;
  sessions: ChatSession[];
  isLoading: boolean;
  error: string | null;

  // Actions
  sendMessage: (content: string) => Promise<void>;
  selectSession: (id: string) => void;
  createNewSession: () => void;
  deleteSession: (id: string) => void;
  clearCurrentSession: () => Promise<void>;
}

/**
 * Chat 状态管理Hook
 */
export function useChat(options: UseChatOptions = {}): UseChatReturn {
  const { initialSessionId } = options;

  // State
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId || null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载会话历史
  const loadSessions = useCallback(async () => {
    try {
      const list = await getSessionList();
      setSessions(
        list.map(s => ({
          id: s.id,
          title: s.title,
          messages: [],
          createdAt: s.created_at,
          updatedAt: s.updated_at,
        }))
      );
    } catch (err) {
      console.error('Failed to load sessions:', err);
    }
  }, []);

  // 初始化时加载历史
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // 选择会话
  const selectSession = useCallback(async (id: string) => {
    try {
      const detail = await getSessionDetail(id);
      setMessages(detail.messages);
      setSessionId(id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载会话失败');
    }
  }, []);

  // 创建新会话
  const createNewSession = useCallback(() => {
    setMessages([]);
    setSessionId(null);
    setError(null);
  }, []);

  // 发送消息
  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;

    setIsLoading(true);
    setError(null);

    // 添加用户消息（乐观更新）
    const tempUserMsg: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: content.trim(),
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, tempUserMsg]);

    try {
      const response = await sendChatMessage(content.trim(), sessionId || undefined);

      // 设置实际的sessionId
      if (!sessionId && response.session_id) {
        setSessionId(response.session_id);
        
        // 更新会话列表
        const detail = await getSessionDetail(response.session_id);
        setSessions(prev => [
          {
            id: response.session_id,
            title: detail.title,
            messages: [],
            createdAt: detail.created_at,
            updatedAt: detail.updated_at,
          },
          ...prev.filter(s => s.id !== response.session_id),
        ]);
      }

      // 添加AI回复
      const aiMsg: ChatMessage = {
        id: `ai-${Date.now()}`,
        role: 'assistant',
        content: response.message,
        timestamp: response.timestamp,
      };

      setMessages(prev => [...prev.slice(0, -1), tempUserMsg, aiMsg]);
    } catch (err) {
      // 移除临时用户消息，显示错误
      setMessages(prev => prev.filter(m => m.id !== tempUserMsg.id));
      setError(err instanceof Error ? err.message : '发送消息失败，请重试');
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  // 删除会话
  const deleteSessionHandler = useCallback(async (id: string) => {
    try {
      await apiDeleteSession(id);
      
      // 如果删除的是当前会话，切到新会话
      if (id === sessionId) {
        createNewSession();
      }
      
      // 更新列表
      setSessions(prev => prev.filter(s => s.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除会话失败');
    }
  }, [sessionId, createNewSession]);

  // 清空当前会话
  const clearCurrentSession = useCallback(async () => {
    if (!sessionId) return;

    try {
      await clearSessionMessages(sessionId);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '清空会话失败');
    }
  }, [sessionId]);

  return {
    // State
    messages,
    sessionId,
    sessions,
    isLoading,
    error,

    // Actions
    sendMessage,
    selectSession,
    createNewSession,
    deleteSession: deleteSessionHandler,
    clearCurrentSession,
  };
}