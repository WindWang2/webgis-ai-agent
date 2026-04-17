'use client';
import React, { memo, useMemo } from 'react';
import { Plus, Clock, Trash2 } from 'lucide-react';
import type { ChatSession } from '@/lib/types/chat';

interface ChatSidebarProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
}

/**
 * Chat Sidebar — HUD-themed conversation history manager
 */
export const ChatSidebar = memo(function ChatSidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
}: ChatSidebarProps) {
  const formatDate = (timestamp: number) => {
    const d = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return '刚刚';
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays === 1) return '昨天';
    if (diffDays < 7) return `${diffDays}天前`;
    return d.toLocaleDateString();
  };

  return (
    <div className="flex flex-col h-full">
      {/* New Chat Button */}
      <div className="px-3 pt-3 pb-2">
        <button
          onClick={onNewSession}
          className="w-full flex items-center justify-center gap-2 px-3 py-2
                   bg-hud-cyan/10 border border-hud-cyan/20 text-hud-cyan
                   rounded-lg hover:bg-hud-cyan/20 hover:border-hud-cyan/30
                   transition-all text-[12px] font-medium tracking-wide"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>新建对话</span>
        </button>
      </div>

      {/* Sessions List */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        <div className="flex items-center gap-1.5 px-2 py-2">
          <Clock className="h-3 w-3 text-white/25" />
          <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-white/25">
            HISTORY
          </span>
        </div>

        {sessions.length === 0 && (
          <p className="text-[11px] text-white/20 px-3 py-6 text-center">
            暂无历史会话
          </p>
        )}

        <ul className="space-y-0.5">
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                onClick={() => onSelectSession(session.id)}
                className={`
                  group w-full flex items-center gap-2 px-3 py-2 rounded-lg
                  text-left transition-all text-[12px]
                  ${session.id === currentSessionId
                    ? 'bg-hud-cyan/10 border border-hud-cyan/20 text-white/90'
                    : 'hover:bg-white/[0.03] border border-transparent text-white/50 hover:text-white/70'
                  }
                `}
              >
                <div className="flex-1 min-w-0">
                  <p className="truncate font-medium">
                    {session.title || '新对话'}
                  </p>
                  <p className="text-[10px] text-white/25 mt-0.5">
                    {formatDate(session.updatedAt)}
                  </p>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(session.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1
                           hover:bg-red-500/10 hover:text-red-400 rounded
                           transition-opacity text-white/20"
                  aria-label="删除会话"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
});

ChatSidebar.displayName = 'ChatSidebar';
export default ChatSidebar;