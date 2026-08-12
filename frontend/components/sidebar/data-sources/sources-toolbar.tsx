'use client';

import { Plus } from 'lucide-react';

export interface SourcesToolbarProps {
  showAddForm: boolean;
  onToggleAddForm: () => void;
}

/** 数据源工具条：面板标题 + 添加/取消按钮。 */
export function SourcesToolbar({ showAddForm, onToggleAddForm }: SourcesToolbarProps) {
  return (
    <div className="flex shrink-0 items-center justify-between border-b border-[var(--theme-border)] p-2.5">
      <span className="text-[11px] font-medium text-[var(--theme-text-muted)]">已注册数据源</span>
      <button
        type="button"
        onClick={onToggleAddForm}
        className="flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-white transition-opacity hover:opacity-85"
        style={{ background: 'var(--agent-accent, #16a34a)' }}
      >
        <Plus size={13} aria-hidden />
        <span>{showAddForm ? '取消' : '添加数据源'}</span>
      </button>
    </div>
  );
}
