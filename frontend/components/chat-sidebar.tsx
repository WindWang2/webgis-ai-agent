'use client';
import React, { memo, useMemo } from 'react';
import type { ChatSession } from '@/lib/types/chat';

interface ChatSidebarProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  /** 隐藏移动端侧边栏 */
  hidden?: boolean;
}

/**
 * Chat Sidebar - 会话历史侧边栏
 * T005-018/T005-019: 会话历史管理
 */
export const ChatSidebar = memo(function ChatSidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  hidden = false,
}: ChatSidebarProps) {
  const formatDate = (timestamp: number) => {
    const d = new Date(timestamp);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24));
    
    if (diffDays === 0) return '今天';
    if (diffDays === 1) return '昨天';
    if (diffDays < 7) return `${diffDays}天前`;
    return d.toLocaleDateString();
  };

  const sidebarClasses = useMemo(() => `
    w-64 border-r bg-gray-50 flex-col
    ${hidden ? 'hidden md:flex' : 'flex'}
  `, [hidden]);

  return (
    <aside className={sidebarClass} role="navigation" aria-label="会话历史">
      {/* New Chat Button */}
      <div className="p-3 border-b">
        <button
          onClick={onNewSession}
          className="w-full flex items-center gap-2 px-3 py-2.5 
                   bg-gradient-to-r from-blue-600 to-blue-700 text-white 
                   rounded-lg hover:from-blue-700 hover:to-blue-800 
                   transition-all shadow-sm"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span className="font-medium">新建对话</span>
        </button>
      </div>

      {/* Sessions List */}
      <nav className="flex-1 overflow-y-auto">
        <div className="p-2">
          <h2 className="flex items-center gap-2 px-2 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12,6 12,12 16,14" />
            </svg>
            会话历史
          </h2>
          
          {sessions.length === 0 && (
            <p className="text-sm text-gray-400 px-2 py-6 text-center">
              暂无历史会话<br/>
              <span className="text-xs">开始一个新对话吧</span>
            </p>
          )}
          
          <ul className="space-y-1 mt-1">
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  onClick={() => onSelectSession(session.id)}
                  className={`
                    group w-full flex items-center gap-2 px-3 py-2.5 rounded-lg 
                    text-left transition-all
                    ${session.id === currentSessionId 
                      ? 'bg-blue-100 text-blue-700 ring-1 ring-blue-200' 
                      : 'hover:bg-gray-100 text-gray-700'
                    }
                  `}
                  aria-current={session.id === currentSessionId ? 'true' : undefined}
                >
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-sm font-medium">
                      {session.title || '新对话'}
                    </p>
                    <p className="text-xs text-gray-400 truncate">
                      {formatDate(session.updatedAt)}
                    </p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteSession(session.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1.5 
                             hover:bg-red-100 hover:text-red-600 rounded-md
                             transition-opacity"
                    aria-label="删除会话"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="3,6 5,12 9,18" />
                      <line x1="9" y1="6" x2="15" y2="18" />
                    </svg>
                  </button>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </nav>

      {/* Footer Tips */}
      <div className="p-3 border-t text-xs text-gray-400 text-center">
        <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-gray-600">Ctrl</kbd> + 
        <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-gray-600 mx-1">Enter</kbd> 发送
        <span className="mx-1">•</span>
        <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-gray-600">Esc</kbd> 清空
      </div>
    </aside>
  );
});

ChatSidebar.displayName = 'ChatSidebar';
export default ChatSidebar;