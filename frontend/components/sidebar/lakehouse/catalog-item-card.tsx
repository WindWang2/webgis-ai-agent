'use client';

import { GitBranch, History, Info } from 'lucide-react';
import type { CatalogEntry } from '@/lib/api/lakehouse';
import { StatusBadge } from '@/components/shared/status-badge';

export interface CatalogItemCardProps {
  item: CatalogEntry;
  onShowDetail: (item: CatalogEntry) => void;
  onQuery: (item: CatalogEntry) => void;
  onLineage: (item: CatalogEntry) => void;
}

const KIND_LABEL: Record<string, string> = {
  zarr_cube: 'Cube',
  vector_parquet: '矢量',
  cog_raster: '栅格',
  arrow_ipc: 'Arrow',
  virtual: '虚拟',
  modelops_artifact: '产物',
};

function formatBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(1)} KB`;
  return `${n} B`;
}

/**
 * Lakehouse 目录单条卡片（与 data-sources 卡片同款交互配方：hover 底色 +
 * 左侧 accent 指示条位）。撤销态以 StatusBadge 呈现；操作：详情 / 查询 / 血缘。
 */
export function CatalogItemCard({ item, onShowDetail, onQuery, onLineage }: CatalogItemCardProps) {
  const revoked = item.status === 'revoked';
  return (
    <div
      data-testid="lakehouse-catalog-card"
      className="rounded-md border border-l-2 border-edge-subtle border-l-transparent bg-surface-overlay px-panel py-2 transition-colors hover:bg-surface-hover"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-body font-semibold text-ink">{item.title || item.object_id.slice(0, 12)}</h4>
          <p className="mt-0.5 line-clamp-1 font-mono text-micro text-ink-muted">
            {item.object_id.slice(0, 16)}… · {formatBytes(item.byte_size)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {revoked && <StatusBadge status="stale" label="已撤销" />}
          <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
            {KIND_LABEL[item.kind] ?? item.kind}
          </span>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-caption text-ink-muted">
        {item.time_start && (
          <span title={`至 ${item.time_end ?? '—'}`}>{item.time_start.slice(0, 10)}</span>
        )}
        {item.tags.map((t) => (
          <span key={t} className="rounded-pill bg-surface-sunken px-1.5 text-micro">
            {t}
          </span>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2 border-t border-edge-subtle pt-2 text-caption">
        <button
          type="button"
          onClick={() => onShowDetail(item)}
          className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <Info size={12} aria-hidden />
          <span>详情</span>
        </button>
        <button
          type="button"
          onClick={() => onQuery(item)}
          disabled={item.kind !== 'zarr_cube'}
          title={item.kind === 'zarr_cube' ? '打开查询构建器' : '仅 cube 条目支持窗口查询'}
          className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-40"
        >
          <History size={12} aria-hidden />
          <span>查询</span>
        </button>
        <button
          type="button"
          onClick={() => onLineage(item)}
          className="ml-auto flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <GitBranch size={12} aria-hidden />
          <span>血缘</span>
        </button>
      </div>
    </div>
  );
}
