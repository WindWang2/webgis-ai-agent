'use client';

import { SearchField } from '@/components/shared/search-field';
import type { DataSource } from '@/lib/api/data-fabric';
import { useT } from '@/lib/i18n/useT';

export interface CatalogToolbarProps {
  searchQuery: string;
  onSearchChange: (q: string) => void;
  /** 数据源选项（用于 source 筛选下拉）；空则不渲染下拉 */
  sources: DataSource[];
  selectedSourceFilter: string;
  onSourceFilterChange: (sourceId: string) => void;
  /** V2 (ADR-0094 §9)：可用状态筛选（'' 全部 / 'available' 可用 / 'unavailable' 已下线）。 */
  availabilityFilter: string;
  onAvailabilityFilterChange: (v: string) => void;
}

/** 可用状态筛选项（chip 词表，与 CatalogItemCard 徽标同一用语）。 */
const AVAILABILITY_OPTIONS: Array<{ value: string; labelKey: string }> = [
  { value: '', labelKey: 'ds.filterAll' },
  { value: 'available', labelKey: 'ds.filterAvailable' },
  { value: 'unavailable', labelKey: 'ds.filterRetired' },
];

/**
 * 空间目录工具条：搜索（debounce 在 use-spatial-catalog 的 effect 中，故
 * SearchField debounceMs=0 保持即输即发的既有时序）+ source 类型筛选 +
 * V2 可用状态筛选 chip（服务端列表暂未携带 availability 时前端过滤兜底）。
 */
export function CatalogToolbar({
  searchQuery,
  onSearchChange,
  sources,
  selectedSourceFilter,
  onSourceFilterChange,
  availabilityFilter,
  onAvailabilityFilterChange,
}: CatalogToolbarProps) {
const t = useT();
  return (
    <div className="shrink-0 space-y-2 border-b border-edge-subtle px-panel py-2">
      <SearchField
        value={searchQuery}
        onChange={onSearchChange}
        placeholder={t('sidebar.ds.searchPh')}
        aria-label={t('sidebar.ds.searchAria')}
        debounceMs={0}
      />
      <div className="flex items-center gap-2">
        {sources.length > 0 && (
          <select
            value={selectedSourceFilter}
            onChange={(e) => onSourceFilterChange(e.target.value)}
            aria-label={t('sidebar.ds.filterBySourceAria')}
            className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption text-ink-secondary"
          >
            <option value="">{t('sidebar.ds.allSources')}</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.source_type})
              </option>
            ))}
          </select>
        )}
        <div
          role="group"
          aria-label={t('sidebar.ds.filterByStatusAria')}
          className={`flex shrink-0 items-center gap-1 ${sources.length === 0 ? 'ml-auto' : ''}`}
        >
          {AVAILABILITY_OPTIONS.map((opt) => {
            const active = availabilityFilter === opt.value;
            return (
              <button
                key={opt.value || 'all'}
                type="button"
                aria-pressed={active}
                onClick={() => onAvailabilityFilterChange(opt.value)}
                className={`rounded-pill border px-2 py-0.5 text-micro font-medium transition-colors ${
                  active
                    ? 'border-status-accent-border bg-status-accent-soft text-status-accent'
                    : 'border-edge-subtle bg-surface-sunken text-ink-muted hover:text-ink-secondary'
                }`}
              >
                {t(`sidebar.${opt.labelKey}`)}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
