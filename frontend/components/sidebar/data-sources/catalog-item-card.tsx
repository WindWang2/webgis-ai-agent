'use client';

import { Download, Eye, Info } from 'lucide-react';
import type { CatalogItem } from '@/lib/api/data-fabric';

export interface CatalogItemCardProps {
  item: CatalogItem;
  /** 该条目标识正在实例化（按钮转 loading 文案并禁用） */
  materializing: boolean;
  onShowDescriptor: (itemId: string) => void;
  onPreview: (itemId: string) => void;
  onMaterialize: (item: CatalogItem) => void;
}

/** 空间目录单条卡片：标题/描述 + 几何类型徽标 + 契约/预览/加载至地图操作。 */
export function CatalogItemCard({
  item,
  materializing,
  onShowDescriptor,
  onPreview,
  onMaterialize,
}: CatalogItemCardProps) {
  return (
    <div className="rounded-xl border border-[var(--theme-border-subtle)] bg-[var(--theme-bg-glass)] p-2.5 transition-all hover:shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-[13px] font-semibold text-[var(--theme-text-primary)]">
            {item.title || item.name}
          </h4>
          <p className="mt-0.5 line-clamp-1 text-[11px] text-[var(--theme-text-muted)]">
            {item.description || item.name}
          </p>
        </div>
        <span className="shrink-0 rounded bg-[var(--theme-bg-muted)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--theme-text-secondary)]">
          {item.geometry_type || 'Vector'}
        </span>
      </div>

      <div className="mt-2 flex items-center gap-2 border-t border-[var(--theme-border-subtle)] pt-2 text-[11px]">
        <button
          type="button"
          onClick={() => onShowDescriptor(item.id)}
          className="flex items-center gap-1 rounded bg-[var(--theme-bg-muted)] px-2 py-1 text-[var(--theme-text-secondary)] transition hover:bg-[var(--theme-bg-hover)]"
        >
          <Info size={12} aria-hidden />
          <span>契约</span>
        </button>
        <button
          type="button"
          onClick={() => onPreview(item.id)}
          className="flex items-center gap-1 rounded bg-[var(--theme-bg-muted)] px-2 py-1 text-[var(--theme-text-secondary)] transition hover:bg-[var(--theme-bg-hover)]"
        >
          <Eye size={12} aria-hidden />
          <span>预览</span>
        </button>
        <button
          type="button"
          onClick={() => onMaterialize(item)}
          disabled={materializing}
          className="ml-auto flex items-center gap-1 rounded px-2.5 py-1 font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-50"
          style={{ background: 'var(--agent-accent, #16a34a)' }}
        >
          <Download size={12} aria-hidden />
          <span>{materializing ? '实例化中...' : '加载至地图'}</span>
        </button>
      </div>
    </div>
  );
}
