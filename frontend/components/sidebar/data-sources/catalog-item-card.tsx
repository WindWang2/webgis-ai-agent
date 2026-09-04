'use client';

import { Download, Eye, Info, Table2 } from 'lucide-react';
import type { CatalogItem } from '@/lib/api/data-fabric';
import { StatusBadge } from '@/components/shared/status-badge';

export interface CatalogItemCardProps {
  item: CatalogItem;
  /** 该条目标识正在实例化（按钮转 loading 文案并禁用） */
  materializing: boolean;
  onShowDescriptor: (itemId: string) => void;
  onPreview: (itemId: string) => void;
  onMaterialize: (item: CatalogItem) => void;
  /** V2 数据工作台：打开数据集检视器（契约 + 查询构建器）。 */
  onInspect: (item: CatalogItem) => void;
}

/**
 * 空间目录单条卡片：标题/描述 + 几何类型徽标 + 可用性徽标（V2：sync 后
 * 数据集消失 → availability='unavailable'，元数据保留）+ 契约/预览/数据集/
 * 加载至地图操作。
 */
export function CatalogItemCard({
  item,
  materializing,
  onShowDescriptor,
  onPreview,
  onMaterialize,
  onInspect,
}: CatalogItemCardProps) {
  // 列表 summary 载荷未携带 availability 时视为可用（向后兼容）。
  const unavailable = (item.availability ?? 'available') === 'unavailable';
  return (
    /* 与 layers-tab 行同款交互配方：hover 底色 + 左侧 accent 指示条位
       （border-l-2 border-l-transparent）；垂直内边距收一步更密。 */
    <div className="rounded-md border border-l-2 border-edge-subtle border-l-transparent bg-surface-overlay px-panel py-2 transition-colors hover:bg-surface-hover">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-body font-semibold text-ink">
            {item.title || item.name}
          </h4>
          <p className="mt-0.5 line-clamp-1 text-caption text-ink-muted">
            {item.description || item.name}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {unavailable && <StatusBadge status="stale" label="已下线" />}
          <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
            {item.geometry_type || 'Vector'}
          </span>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-2 border-t border-edge-subtle pt-2 text-caption">
        <button
          type="button"
          onClick={() => onInspect(item)}
          className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <Table2 size={12} aria-hidden />
          <span>数据集</span>
        </button>
        <button
          type="button"
          onClick={() => onShowDescriptor(item.id)}
          className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <Info size={12} aria-hidden />
          <span>契约</span>
        </button>
        <button
          type="button"
          onClick={() => onPreview(item.id)}
          className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <Eye size={12} aria-hidden />
          <span>预览</span>
        </button>
        <button
          type="button"
          onClick={() => onMaterialize(item)}
          disabled={materializing}
          className="ml-auto flex items-center gap-1 rounded-sm bg-status-accent px-2.5 py-1 font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
        >
          <Download size={12} aria-hidden />
          <span>{materializing ? '实例化中...' : '加载至地图'}</span>
        </button>
      </div>
    </div>
  );
}
