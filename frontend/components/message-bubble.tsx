'use client';
import React, { memo, useMemo } from 'react';
import type { ChatMessage } from '@/lib/types/chat';
import { parseMessageContent } from './code-highlight/code-block';

interface MessageBubbleProps {
  message: ChatMessage;
}

/**
 * Message Bubble - 消息气泡组件
 * T005-005: 实现消息气泡组件，支持区分用户/AI消息样式
 * T005-017: 更新增加代码块高亮支持
 */
export const MessageBubble = memo(function MessageBubble({
  message,
}: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  const bubbleClass = useMemo(() => `
    max-w-[85%] rounded-xl px-4 py-3 shadow-sm
    ${isUser 
      ? 'bg-gradient-to-br from-blue-600 to-blue-700 text-white' 
      : isSystem 
        ? 'bg-yellow-50 text-yellow-800 border border-yellow-200'
        : 'bg-gray-50 text-gray-900 border border-gray-100'
    }
  `, [isUser, isSystem]);

  const timeClass = useMemo(() => `
    text-xs mt-1.5 block opacity-70
    ${isUser ? 'text-blue-100' : 'text-gray-400'}
  `, [isUser]);

  const renderContent = useMemo(() => {
    // 用户消息和系统消息直接显示
    if (isUser || isSystem) {
      return <p className="whitespace-pre-wrap break-word leading-relaxed">{message.content}</p>;
    }

    // AI消息解析代码块
    return (
      <div className="leading-relaxed">
        {parseMessageContent(message.content)}
      </div>
    );
  }, [message.content, isUser, isSystem]);

  return (
    <div 
      className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fade-in`}
      role="article"
      aria-label={`${message.role} message`}
    >
      <div className={bubbleClass}>
        {renderContent}
        <span className={timeClass}>
          {new Date(message.timestamp).toLocaleTimeString()}
        </span>
      </div>
    </div>
  );
});

MessageBubble.displayName = 'MessageBubble';

export default MessageBubble;