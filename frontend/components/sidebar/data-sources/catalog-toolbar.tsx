'use client';

import { SearchField } from '@/components/shared/search-field';
import type { DataSource } from '@/lib/api/data-fabric';

export interface CatalogToolbarProps {
  searchQuery: string;
  onSearchChange: (q: string) => void;
  /** 数据源选项（用于 source 筛选下拉）；空则不渲染下拉 */
  sources: DataSource[];
  selectedSourceFilter: string;
  onSourceFilterChange: (sourceId: string) => void;
}

/**
 * 空间目录工具条：搜索（debounce 在 use-spatial-catalog 的 effect 中，故
 * SearchField debounceMs=0 保持即输即发的既有时序）+ source 类型筛选。
 */
export function CatalogToolbar({
  searchQuery,
  onSearchChange,
  sources,
  selectedSourceFilter,
  onSourceFilterChange,
}: CatalogToolbarProps) {
  return (
    <div className="shrink-0 space-y-2 border-b border-[var(--theme-border)] p-2.5">
      <SearchField
        value={searchQuery}
        onChange={onSearchChange}
        placeholder="搜索空间数据集、图层或关键词..."
        aria-label="搜索空间数据集"
        debounceMs={0}
      />
      {sources.length > 0 && (
        <select
          value={selectedSourceFilter}
          onChange={(e) => onSourceFilterChange(e.target.value)}
          aria-label="按数据源筛选空间目录"
          className="w-full rounded border border-[var(--theme-border)] bg-[var(--theme-bg-input)] px-2 py-1 text-[11px] text-[var(--theme-text-secondary)]"
        >
          <option value="">全部数据源</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.source_type})
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
