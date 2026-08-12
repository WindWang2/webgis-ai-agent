'use client';

import { Activity, RefreshCw } from 'lucide-react';
import type { DataSource } from '@/lib/api/data-fabric';
import { StatusBadge } from '@/components/shared/status-badge';
import { ConfirmAction } from '@/components/shared/confirm-action';

/**
 * 数据源状态 → 共享 StatusBadge 映射（原 light-only 手写徽标收敛）。
 * healthy/active=正常(ok)，degraded=降级(stale/amber)，其余=离线(error)。
 */
function toStatusBadgeProps(status: string): { status: string; label: string } {
  if (status === 'healthy' || status === 'active') return { status: 'ok', label: '正常' };
  if (status === 'degraded') return { status: 'stale', label: '降级' };
  return { status: 'error', label: '离线' };
}

export interface SourceItemCardProps {
  source: DataSource;
  onProbe: (sourceId: string) => void;
  onSync: (sourceId: string) => void;
  onDelete: (sourceId: string) => void;
}

/** 已注册数据源卡片：名称 + 状态徽标 + Endpoint + 探查/同步/两段式删除。 */
export function SourceItemCard({ source, onProbe, onSync, onDelete }: SourceItemCardProps) {
  const badge = toStatusBadgeProps(source.status);
  return (
    <div className="rounded-xl border border-[var(--theme-border-subtle)] bg-[var(--theme-bg-glass)] p-2.5">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-[var(--theme-text-primary)]">{source.name}</span>
        <StatusBadge status={badge.status} label={badge.label} />
      </div>
      <div className="mt-1 truncate font-mono text-[11px] text-[var(--theme-text-muted)]">
        {source.endpoint_url}
      </div>
      <div className="mt-2 flex items-center gap-2 border-t border-[var(--theme-border-subtle)] pt-2 text-[11px]">
        <button
          type="button"
          onClick={() => onProbe(source.id)}
          className="flex items-center gap-1 text-[var(--theme-text-secondary)] transition hover:text-[var(--theme-text-primary)]"
        >
          <Activity size={12} aria-hidden />
          <span>探查</span>
        </button>
        <button
          type="button"
          onClick={() => onSync(source.id)}
          className="flex items-center gap-1 text-[var(--theme-text-secondary)] transition hover:text-[var(--theme-text-primary)]"
        >
          <RefreshCw size={12} aria-hidden />
          <span>同步</span>
        </button>
        <ConfirmAction
          label="删除"
          confirmLabel="确认删除？"
          className="ml-auto"
          onConfirm={() => onDelete(source.id)}
        />
      </div>
    </div>
  );
}
