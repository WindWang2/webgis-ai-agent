'use client';
import React, { memo, useState, useRef, useEffect } from 'react';
import { Send, Loader2, AlertCircle, RotateCcw } from 'lucide-react';
import type { ChatMessage } from '@/lib/types/chat';
import { MessageBubble } from './message-bubble';
import { useKeyboardShortcut } from '@/lib/hooks/use-keyboard-shortcut';

interface ChatPanelProps {
  messages: ChatMessage[];
  onSendMessage: (content: string) => void;
  onClearInput?: () => void;
  isLoading?: boolean;
  error?: string;
  onRetry?: () => void;
}

/**
 * Chat Panel - AI聊天面板
 * T005: 完整的AI交互模块功能
 * - 后端API对接 (via props callbacks)
 * - 代码块高亮 (MessageBubble内建)
 * - 会话历史管理 (via ChatSidebar)
 * - 快捷键支持
 * - 响应式适配
 * - 无障碍支持
 */
export const ChatPanel = memo(function ChatPanel({
  messages,
  onSendMessage,
  onClearInput,
  isLoading = false,
  error,
  onRetry,
}: ChatPanelProps) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 快捷键绑定
  const handleSend = () => {
    if (input.trim() && !isLoading) {
      onSendMessage(input.trim());
      setInput('');
    }
  };

  const handleClear = () => {
    setInput('');
    inputRef.current?.focus();
    onClearInput?.();
  };

  useKeyboardShortcut({
    onSend: handleSend,
    onClear: handleClear,
    disabled: isLoading,
  });

  // 自动滚动到底部
  useEffect(() => {
    if (messagesEndRef.current) {
      try {
        messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
      } catch {
        // jsdom doesn't support scrollIntoView
      }
    }
  }, [messages]);

  // 聚焦输入框
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    handleSend();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Shift+Enter 换行（默认行为）
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-lg shadow-sm border" role="region" aria-label="AI聊天面板">
      {/* Messages List */}
      <div 
        role="log" 
        aria-label="聊天消息列表"
        aria-live="polite"
        className="flex-1 overflow-y-auto p-4 space-y-4"
      >
        {/* Welcome Tip */}
        {messages.length === 0 && !error && (
          <div className="text-center text-gray-400 py-8" role="status">
            <div className="mb-2">
              <svg className="inline-block w-12 h-12 text-blue-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <p className="text-base mb-1">欢迎使用 WebGIS AI 助手</p>
            <p className="text-sm">输入您的地理问题，开始对话</p>
            <p className="text-xs mt-4 text-gray-300">
              示例：帮我做一个缓冲区分析、这个图层的面积统计
            </p>
          </div>
        )}
        
        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 mb-4" role="alert">
            <div className="flex items-start gap-3">
              <AlertCircle size={20} className="shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-medium">发送失败</p>
                <p className="text-xs mt-1 text-red-600">{error}</p>
                {onRetry && (
                  <button
                    onClick={onRetry}
                    className="mt-2 flex items-center gap-1.5 px-3 py-1.5 
                             bg-red-100 hover:bg-red-200 text-red-700 
                             rounded-md text-xs transition-colors"
                  >
                    <RotateCcw size={14} />
                    重试
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
        
        {/* Messages Bubbles */}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        
        {/* Loading Indicator */}
        {isLoading && (
          <div 
            data-testid="loading-indicator"
            className="flex justify-start"
            role="status"
            aria-label="AI正在思考"
          >
            <div className="bg-gray-100 rounded-xl px-4 py-3 flex items-center gap-2">
              <Loader2 
                size={20} 
                className="animate-spin text-blue-600" 
              />
              <span className="text-sm text-gray-500">AI正在思考...</span>
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} aria-hidden="true" />
      </div>

      {/* Input Area */}
      <form 
        onSubmit={handleSubmit}
        className="border-t p-4 flex gap-3 items-end"
      >
        <div className="flex-1 relative">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的地理问题..."
            className="w-full resize-none border border-gray-200 rounded-xl px-4 py-3 
                     focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                     transition-all text-base"
            rows={1}
            aria-label="输入消息"
            aria-describedby="input-hint"
            disabled={isLoading}
          />
          <span id="input-hint" className="sr-only">
            按Enter发送消息，Shift+Enter换行，Ctrl+Enter也可发送
          </span>
        </div>
        
        <button
          type="submit"
          disabled={!input.trim() || isLoading}
          className="px-5 py-3 bg-gradient-to-r from-blue-600 to-blue-700 text-white 
                   rounded-xl hover:from-blue-700 hover:to-blue-800 
                   disabled:opacity-50 disabled:cursor-not-allowed 
                   transition-all shadow-sm flex items-center gap-2 font-medium"
          aria-label="发送消息"
        >
          <Send size={20} />
          <span className="hidden sm:inline">发送</span>
        </button>
      </form>

      {/* Keyboard Hint */}
      <div className="border-t px-4 py-2 bg-gray-50 text-xs text-gray-400 text-center sm:hidden">
        <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-gray-600">Ctrl</kbd> + 
        <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-gray-600 ml-1">Enter</kbd> 发送
      </div>
    </div>
  );
});

ChatPanel.displayName = 'ChatPanel';
export default ChatPanel;