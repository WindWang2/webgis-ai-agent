'use client';

import { useState, useMemo, useRef } from 'react';
import { History, X, Plus, Search } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import type { SessionSummary } from '@/lib/store/hud-types';

interface HistoryDrawerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (session: SessionSummary | null) => void;
}

export function HistoryDrawer({ open, onClose, onSelect }: HistoryDrawerProps) {
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

  const handleSelect = (session: SessionSummary) => {
    onSelect(session);
    onClose();
  }

  // 审计 a11y HIGH（findings.md F4 残留缺口）曾在这里手写焦点陷阱（本地
  // getTabbableIn + Tab/Escape/回焦），与全站共用的 useDialogFocus 是两套平行
  // 实现，逻辑容易漂移。统一改用共用 hook：搜索框初始聚焦、Tab/Shift+Tab 围栏、
  // Escape 关闭、关闭回焦，行为与原来一致。
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus({
    open,
    containerRef: panelRef,
    onEscape: onClose,
    initialFocusSelector: 'input',
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop (left side -- click to close) */}
      <div
        className="flex-1 bg-surface-scrim"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-drawer-title"
        tabIndex={-1}
        className="w-[340px] shrink-0 flex flex-col border-l border-edge-subtle shadow-drawer outline-none"
        style={{
          // V4：overlay 表面不透明（--surface-overlay 双主题均不透明），
          // 半透明白底 + backdrop blur 已无作用，去掉。
          background: 'var(--surface-overlay)',
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
        <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-edge-subtle">
          <History size={16} style={{ color: 'var(--agent-accent)' }} />
          <h2 id="history-drawer-title" className="flex-1 text-body font-semibold text-ink">历史会话</h2>
          <button
            onClick={() => { onSelect(null); onClose(); }}
            aria-label="新建会话"
            className="flex items-center gap-1 px-2 py-1 rounded-sm text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-90"
            style={{ backgroundColor: 'var(--agent-accent)' }}
          >
            <Plus size={12} />
            新建会话
          </button>
          <button
            onClick={onClose}
            aria-label="关闭历史会话"
            className="p-1.5 rounded-sm text-ink-disabled hover:text-ink-secondary hover:bg-surface-hover transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        {/* Search input */}
        <div className="shrink-0 px-3 py-2 border-b border-edge-subtle">
          <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-surface-sunken border border-edge-subtle">
            <Search size={13} className="text-ink-disabled shrink-0" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="搜索历史会话"
              placeholder="搜索会话..."
              className="flex-1 bg-transparent text-body text-ink-secondary placeholder:text-ink-disabled outline-none"
            />
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto" role="list" aria-label="历史会话列表">
          {filtered.length === 0 ? (
            <div
              className="flex flex-col items-center justify-center h-full text-center px-6"
              role="status"
              aria-live="polite"
            >
              <History size={20} className="text-ink-disabled mb-2" />
              <p className="text-body text-ink-muted">
                {search ? '没有匹配的会话' : '暂无历史会话'}
              </p>
            </div>
          ) : (
            <div className="px-2 py-1.5 space-y-0.5">
              {filtered.map((session) => (
                <button
                  key={session.id}
                  onClick={() => handleSelect(session)}
                  className="w-full text-left px-3 py-2.5 rounded-md hover:bg-surface-hover transition-colors group"
                  role="listitem"
                >
                  {/* Title */}
                  <p className="text-meta font-medium text-ink-secondary truncate group-hover:text-ink">
                    {session.title || '未命名会话'}
                  </p>

                  {/* Meta */}
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-micro text-ink-muted">{session.time}</span>
                    {session.msgs > 0 && (
                      <span className="text-micro text-ink-muted">
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
                          className="inline-flex px-1.5 py-0.5 rounded-pill text-body font-medium"
                          style={{
                            backgroundColor: 'color-mix(in srgb, var(--agent-accent) 7%, transparent)',
                            color: 'var(--agent-accent)',
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
        <div className="shrink-0 px-4 py-2.5 border-t border-edge-subtle bg-surface-raised">
          <span className="text-body text-ink-muted">
            共 {filtered.length} 条历史会话
          </span>
        </div>
      </div>
    </div>
  );
}

export default HistoryDrawer;
