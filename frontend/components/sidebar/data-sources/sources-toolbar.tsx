'use client';

import { Plus } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';

export interface SourcesToolbarProps {
  showAddForm: boolean;
  onToggleAddForm: () => void;
}

/** 数据源工具条：面板标题 + 添加/取消按钮。 */
export function SourcesToolbar({ showAddForm, onToggleAddForm }: SourcesToolbarProps) {
const t = useT();
  return (
    <div className="flex shrink-0 items-center justify-between border-b border-edge-subtle px-panel py-2">
      <span className="text-caption font-medium text-ink-muted">{t('sidebar.ds.registeredSources')}</span>
      <button
        type="button"
        onClick={onToggleAddForm}
        className="flex items-center gap-1 rounded-sm bg-status-accent px-2 py-1 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85"
      >
        <Plus size={12} aria-hidden />
        <span>{showAddForm ? '取消' : '添加数据源'}</span>
      </button>
    </div>
  );
}
