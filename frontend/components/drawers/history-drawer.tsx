'use client';

import { useState, useMemo } from 'react';
import { History, X, Plus, Search } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

interface HistoryDrawerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (session: any) => void;
  accentColor: string;
}

export function HistoryDrawer({ open, onClose, onSelect, accentColor }: HistoryDrawerProps) {
  const sessions = useHudStore((s) => s.sessions);
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    if (!search.trim()) return sessions;
    const q = search.toLowerCase();
    return sessions.filter(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.tags?.some((t) => t.toLowerCase().includes(q))
    );
  }, [sessions, search]);

  const handleSelect = (session: any) => {
    onSelect(session);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop (left side -- click to close) */}
      <div
        className="flex-1 bg-slate-900/20"
        style={{ backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)' }}
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div
        className="w-[340px] shrink-0 flex flex-col border-l border-slate-200/60 shadow-[-2px_0_24px_rgba(15,23,42,0.09)]"
        style={{
          background: 'rgba(252,253,254,0.92)',
          backdropFilter: 'blur(28px)',
          WebkitBackdropFilter: 'blur(28px)',
          animation: 'slide-from-right 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      >
        <style>{`
          @keyframes slide-from-right {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
          }
        `}</style>

        {/* Header */}
        <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-slate-200/60">
          <History size={16} style={{ color: accentColor }} />
          <h2 className="flex-1 text-[13px] font-semibold text-slate-800">历史会话</h2>
          <button
            onClick={() => { onSelect(null); onClose(); }}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[10.5px] font-medium text-white transition-opacity hover:opacity-90"
            style={{ backgroundColor: accentColor }}
          >
            <Plus size={12} />
            新建会话
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        {/* Search input */}
        <div className="shrink-0 px-3 py-2 border-b border-slate-100">
          <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-slate-100/60 border border-slate-200/60">
            <Search size={13} className="text-slate-300 shrink-0" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索会话..."
              className="flex-1 bg-transparent text-[12px] text-slate-700 placeholder:text-slate-300 outline-none"
            />
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <History size={20} className="text-slate-200 mb-2" />
              <p className="text-[11.5px] text-slate-400">
                {search ? '没有匹配的会话' : '暂无历史会话'}
              </p>
            </div>
          ) : (
            <div className="px-2 py-1.5 space-y-0.5">
              {filtered.map((session) => (
                <button
                  key={session.id}
                  onClick={() => handleSelect(session)}
                  className="w-full text-left px-3 py-2.5 rounded-xl hover:bg-slate-50/80 transition-colors group"
                >
                  {/* Title */}
                  <p className="text-[12.5px] font-medium text-slate-700 truncate group-hover:text-slate-900">
                    {session.title || '未命名会话'}
                  </p>

                  {/* Meta */}
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9.5px] text-slate-300">{session.time}</span>
                    {session.msgs > 0 && (
                      <span className="text-[9.5px] text-slate-300">
                        {session.msgs} 条消息
                      </span>
                    )}
                  </div>

                  {/* Tags */}
                  {session.tags && session.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {session.tags.map((tag: string) => (
                        <span
                          key={tag}
                          className="inline-flex px-1.5 py-0.5 rounded-full text-[9px] font-medium"
                          style={{
                            backgroundColor: `${accentColor}12`,
                            color: accentColor,
                          }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 px-4 py-2.5 border-t border-slate-200/60 bg-white/30">
          <span className="text-[10px] text-slate-400">
            共 {filtered.length} 条历史会话
          </span>
        </div>
      </div>
    </div>
  );
}

export default HistoryDrawer;
