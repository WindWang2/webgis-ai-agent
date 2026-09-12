'use client';

import { RefreshCw } from 'lucide-react';
import type { CatalogOwnerType } from '@/lib/api/lakehouse';
import type { CatalogFilters } from './use-lakehouse-catalog';
import { CATALOG_KIND_OPTIONS } from './use-lakehouse-catalog';

export interface CatalogToolbarProps {
  ownerType: CatalogOwnerType;
  onOwnerTypeChange: (t: CatalogOwnerType) => void;
  projectId: string;
  onProjectIdChange: (id: string) => void;
  filters: CatalogFilters;
  onFiltersChange: (f: CatalogFilters) => void;
  onRefresh: () => void;
  loading: boolean;
}

const inputClass =
  'min-w-0 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption text-ink-secondary';

/**
 * Lakehouse 目录工具条：owner 域切换（session/project）+ kind/producer/时间
 * 过滤 + include_revoked + 刷新。session 域以当前会话为域；project 域需要
 * 显式项目 id（后端按 project owner fail-closed 校验）。
 */
export function CatalogToolbar({
  ownerType,
  onOwnerTypeChange,
  projectId,
  onProjectIdChange,
  filters,
  onFiltersChange,
  onRefresh,
  loading,
}: CatalogToolbarProps) {
  const patch = (p: Partial<CatalogFilters>) => onFiltersChange({ ...filters, ...p });
  return (
    <div className="shrink-0 space-y-2 border-b border-edge-subtle px-panel py-2">
      <div className="flex items-center gap-2">
        <select
          value={ownerType}
          onChange={(e) => onOwnerTypeChange(e.target.value as CatalogOwnerType)}
          aria-label="owner 域"
          className={inputClass}
        >
          <option value="session">会话域</option>
          <option value="project">项目域</option>
        </select>
        {ownerType === 'project' && (
          <input
            type="text"
            value={projectId}
            onChange={(e) => onProjectIdChange(e.target.value)}
            placeholder="项目 ID"
            aria-label="项目 ID"
            className={`${inputClass} flex-1`}
          />
        )}
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          aria-label="刷新目录"
          title="刷新目录"
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm bg-surface-sunken text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          <RefreshCw size={12} aria-hidden className={loading ? 'animate-spin motion-reduce:animate-none' : ''} />
        </button>
      </div>
      <div className="flex items-center gap-2">
        <select
          value={filters.kind}
          onChange={(e) => patch({ kind: e.target.value })}
          aria-label="按类型筛选"
          className={inputClass}
        >
          {CATALOG_KIND_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={filters.producer}
          onChange={(e) => patch({ producer: e.target.value })}
          placeholder="生产者"
          aria-label="按生产者筛选"
          className={`${inputClass} w-24`}
        />
      </div>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1 text-caption text-ink-secondary">
          从
          <input
            type="date"
            value={filters.timeFrom}
            onChange={(e) => patch({ timeFrom: e.target.value })}
            aria-label="时间范围起始"
            className={inputClass}
          />
        </label>
        <label className="flex items-center gap-1 text-caption text-ink-secondary">
          至
          <input
            type="date"
            value={filters.timeTo}
            onChange={(e) => patch({ timeTo: e.target.value })}
            aria-label="时间范围结束"
            className={inputClass}
          />
        </label>
        <label className="ml-auto flex shrink-0 items-center gap-1 text-caption text-ink-secondary">
          <input
            type="checkbox"
            checked={filters.includeRevoked}
            onChange={(e) => patch({ includeRevoked: e.target.checked })}
            className="accent-[var(--agent-accent)]"
          />
          含已撤销
        </label>
      </div>
    </div>
  );
}
