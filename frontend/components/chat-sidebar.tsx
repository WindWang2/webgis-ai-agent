'use client';
import React, { memo, useMemo, useState } from 'react';
import { Plus, Clock, Trash2, MessageSquare, Search } from 'lucide-react';
import type { ChatSession } from '@/lib/types/chat';

interface ChatSidebarProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
}

type TimeGroup = 'today' | 'yesterday' | 'week' | 'older';

function getTimeGroup(timestamp: number): TimeGroup {
  const d = new Date(timestamp);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOfWeek = startOfToday - 6 * 86400000;

  if (timestamp >= startOfToday) return 'today';
  if (timestamp >= startOfYesterday) return 'yesterday';
  if (timestamp >= startOfWeek) return 'week';
  return 'older';
}

function formatGroupLabel(group: TimeGroup): string {
  switch (group) {
    case 'today': return '今天';
    case 'yesterday': return '昨天';
    case 'week': return '近7天';
    case 'older': return '更早';
  }
}

function formatRelativeTime(timestamp: number): string {
  const diffMs = Date.now() - timestamp;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days === 1) return '昨天';
  if (days < 7) return `${days}天前`;
  return new Date(timestamp).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

export const ChatSidebar = memo(function ChatSidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
}: ChatSidebarProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter(s =>
      (s.title || '新对话').toLowerCase().includes(q)
    );
  }, [sessions, searchQuery]);

  const grouped = useMemo(() => {
    const groups: Record<TimeGroup, ChatSession[]> = { today: [], yesterday: [], week: [], older: [] };
    for (const s of filteredSessions) {
      groups[getTimeGroup(s.updatedAt)].push(s);
    }
    return groups;
  }, [filteredSessions]);

  const handleDelete = (id: string) => {
    if (confirmDeleteId === id) {
      onDeleteSession(id);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(id);
      setTimeout(() => setConfirmDeleteId(null), 2500);
    }
  };

  const renderGroup = (group: TimeGroup) => {
    const items = grouped[group];
    if (items.length === 0) return null;
    return (
      <div key={group} className="mb-2">
        <div className="flex items-center gap-1.5 px-3 py-1.5">
          <span className="text-[9px] font-mono uppercase tracking-[0.2em] text-white/20">
            {formatGroupLabel(group)}
          </span>
          <div className="flex-1 h-px bg-white/[0.04]" />
          <span className="text-[9px] font-mono text-white/15">{items.length}</span>
        </div>
        <div className="space-y-px px-1.5">
          {items.map((session) => {
            const isActive = session.id === currentSessionId;
            const isConfirming = confirmDeleteId === session.id;
            return (
              <div key={session.id} className="relative">
                <button
                  onClick={() => { onSelectSession(session.id); setConfirmDeleteId(null); }}
                  className={`
                    group w-full flex items-center gap-2.5 pl-3 pr-2 py-2 rounded-lg
                    text-left transition-all duration-200 text-[12px] relative
                    ${isActive
                      ? 'bg-hud-cyan/[0.08] text-white/90'
                      : 'hover:bg-white/[0.03] text-white/45 hover:text-white/70'
                    }
                  `}
                >
                  {isActive && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 rounded-r-full bg-hud-cyan shadow-[0_0_8px_rgba(0,242,255,0.5)]" />
                  )}
                  <MessageSquare className={`h-3.5 w-3.5 flex-shrink-0 ${isActive ? 'text-hud-cyan/70' : 'text-white/15'}`} />
                  <div className="flex-1 min-w-0">
                    <p className="truncate font-medium leading-tight" title={session.title || '新对话'}>
                      {session.title || '新对话'}
                    </p>
                    <p className="text-[10px] text-white/20 mt-0.5">
                      {formatRelativeTime(session.updatedAt)}
                    </p>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(session.id); }}
                    className={`
                      flex-shrink-0 p-1 rounded transition-all
                      ${isConfirming
                        ? 'opacity-100 bg-red-500/20 text-red-400'
                        : 'opacity-0 group-hover:opacity-100 text-white/15 hover:bg-red-500/10 hover:text-red-400'
                      }
                    `}
                    aria-label={isConfirming ? '确认删除' : '删除会话'}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 pt-3 pb-2 space-y-2">
        <button
          onClick={onNewSession}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5
                   bg-hud-cyan/[0.06] border border-hud-cyan/15 text-hud-cyan/90
                   rounded-xl hover:bg-hud-cyan/[0.12] hover:border-hud-cyan/25
                   transition-all text-[12px] font-medium tracking-wide
                   shadow-[0_0_16px_rgba(0,242,255,0.05)]"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>新建对话</span>
        </button>

        {sessions.length > 5 && (
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3 w-3 text-white/20" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索会话..."
              className="w-full h-7 bg-white/[0.03] border border-white/[0.06] rounded-lg pl-7 pr-3
                       text-[11px] text-white/60 placeholder:text-white/15
                       focus:outline-none focus:border-hud-cyan/20 transition-colors"
            />
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto pb-2">
        {filteredSessions.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-center px-6">
            <Clock className="h-8 w-8 text-white/[0.06] mb-3" />
            <p className="text-[11px] text-white/20 font-light">
              {searchQuery ? '未找到匹配的会话' : '暂无历史会话'}
            </p>
            <p className="text-[10px] text-white/10 mt-1">
              {searchQuery ? '尝试其他关键词' : '开始对话后自动记录'}
            </p>
          </div>
        )}

        {renderGroup('today')}
        {renderGroup('yesterday')}
        {renderGroup('week')}
        {renderGroup('older')}
      </div>
    </div>
  );
});

ChatSidebar.displayName = 'ChatSidebar';
export default ChatSidebar;
