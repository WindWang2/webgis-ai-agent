'use client';

import React, { useState, useMemo, useCallback } from 'react';
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Search,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Copy,
  Check,
  Hash,
  Type,
  ToggleLeft,
  Boxes,
  MapPin,
  FileSpreadsheet,
} from 'lucide-react';
import clsx from 'clsx';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { QueryResult } from '@/lib/api/data-fabric';
import { EmptyState } from '@/components/shared/empty-state';
import { LoadingState } from '@/components/shared/loading-state';

export type ColumnType = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'geometry' | 'unknown';

export interface ColumnSchema {
  key: string;
  label: string;
  type: ColumnType;
  sortable?: boolean;
}

export type SortDirection = 'asc' | 'desc' | null;

export interface SortConfig {
  key: string | null;
  direction: SortDirection;
}

export interface TabularDataGridProps {
  /** The dataset: can be raw rows array, GeoJSON FeatureCollection, or QueryResult */
  data?: Array<Record<string, unknown>> | GeoJSONFeatureCollection | QueryResult | null;
  /** Direct features / rows override if data is not passed */
  features?: Array<Record<string, unknown>> | null;
  /** Custom schema definitions if specified, otherwise auto-extracted */
  columns?: ColumnSchema[];
  /** Total count override if known from backend */
  totalCount?: number;
  /** Loading indicator */
  loading?: boolean;
  /** Initial page size (default: 10) */
  defaultPageSize?: number;
  /** Available page sizes (default: [10, 25, 50, 100]) */
  pageSizeOptions?: number[];
  /** Searchable? (default: true) */
  enableSearch?: boolean;
  /** Enable column sorting? (default: true) */
  enableSort?: boolean;
  /** Enable row copy action? (default: true) */
  enableRowCopy?: boolean;
  /** Custom class name */
  className?: string;
  /** Empty state title / description */
  emptyTitle?: string;
  emptyDescription?: string;
  /** Row click callback */
  onRowClick?: (row: Record<string, unknown>, index: number) => void;
}

/** Format a GeoJSON geometry object into a compact readable string */
function formatGeometrySummary(geom: unknown): string {
  if (!geom || typeof geom !== 'object') return '未知几何';
  const g = geom as { type?: string; coordinates?: unknown };
  const type = g.type || 'Geometry';
  if (type === 'Point' && Array.isArray(g.coordinates) && g.coordinates.length >= 2) {
    const [x, y] = g.coordinates;
    const fx = typeof x === 'number' ? x.toFixed(4) : String(x);
    const fy = typeof y === 'number' ? y.toFixed(4) : String(y);
    return `Point [${fx}, ${fy}]`;
  }
  if (type === 'Polygon' && Array.isArray(g.coordinates)) {
    return `Polygon (${g.coordinates.length} rings)`;
  }
  if (type === 'MultiPolygon' && Array.isArray(g.coordinates)) {
    return `MultiPolygon (${g.coordinates.length} parts)`;
  }
  if (type === 'LineString' && Array.isArray(g.coordinates)) {
    return `LineString (${g.coordinates.length} pts)`;
  }
  return type;
}

/** Normalize input dataset to a flat array of row property dictionaries */
function normalizeRows(
  data?: Array<Record<string, unknown>> | GeoJSONFeatureCollection | QueryResult | null,
  directFeatures?: Array<Record<string, unknown>> | null
): Array<Record<string, unknown>> {
  const source = directFeatures ?? (data && typeof data === 'object' && 'features' in data ? (data.features as unknown[]) : data);
  if (!source || !Array.isArray(source)) return [];

  return source.map((item) => {
    if (!item || typeof item !== 'object') {
      return { value: item };
    }
    // GeoJSON Feature object: { type: 'Feature', properties: { ... }, geometry: { ... }, id?: ... }
    if ('type' in item && (item as { type?: string }).type === 'Feature') {
      const feature = item as {
        properties?: Record<string, unknown> | null;
        geometry?: unknown;
        id?: string | number;
      };
      const props: Record<string, unknown> = { ...(feature.properties ?? {}) };
      if (feature.id !== undefined && props.id === undefined) {
        props.id = feature.id;
      }
      if (feature.geometry && props.geometry === undefined && props._geometry === undefined) {
        props.geometry = formatGeometrySummary(feature.geometry);
      }
      return props;
    }
    return item as Record<string, unknown>;
  });
}

/** Infer column type from discovered values across rows */
function inferType(values: unknown[]): ColumnType {
  const nonNulls = values.filter((v) => v !== null && v !== undefined && v !== '');
  if (nonNulls.length === 0) return 'unknown';

  let hasNumber = false;
  let hasBoolean = false;
  let hasObject = false;
  let hasArray = false;
  let hasString = false;

  for (const val of nonNulls) {
    if (typeof val === 'number') {
      hasNumber = true;
    } else if (typeof val === 'boolean') {
      hasBoolean = true;
    } else if (Array.isArray(val)) {
      hasArray = true;
    } else if (typeof val === 'object') {
      hasObject = true;
    } else if (typeof val === 'string') {
      // Check if it represents geometry
      if (val.startsWith('Point [') || val.startsWith('Polygon') || val.startsWith('LineString')) {
        return 'geometry';
      }
      hasString = true;
    }
  }

  if (hasArray) return 'array';
  if (hasObject) return 'object';
  if (hasNumber && !hasString && !hasBoolean) return 'number';
  if (hasBoolean && !hasNumber && !hasString) return 'boolean';
  return 'string';
}

/** Dynamically extract column schema from all rows */
function extractColumnSchema(rows: Array<Record<string, unknown>>): ColumnSchema[] {
  if (rows.length === 0) return [];

  const keySet = new Set<string>();
  const keyValuesMap = new Map<string, unknown[]>();

  for (const row of rows) {
    for (const key of Object.keys(row)) {
      keySet.add(key);
      if (!keyValuesMap.has(key)) {
        keyValuesMap.set(key, []);
      }
      keyValuesMap.get(key)!.push(row[key]);
    }
  }

  const allKeys = Array.from(keySet);

  // Preferred column ordering: identifiers first, properties alphabetical, geometry/complex last
  const priorityKeys = ['id', 'ID', 'gid', 'name', 'NAME', 'title', 'code', 'category', 'type'];
  const sortedKeys = allKeys.sort((a, b) => {
    const aIdx = priorityKeys.indexOf(a);
    const bIdx = priorityKeys.indexOf(b);
    if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx;
    if (aIdx !== -1) return -1;
    if (bIdx !== -1) return 1;
    if (a === 'geometry' || a === '_geometry') return 1;
    if (b === 'geometry' || b === '_geometry') return -1;
    return a.localeCompare(b);
  });

  return sortedKeys.map((key) => {
    const values = keyValuesMap.get(key) || [];
    const type = key === 'geometry' || key === '_geometry' ? 'geometry' : inferType(values);
    return {
      key,
      label: key,
      type,
      sortable: true,
    };
  });
}

/** Render a column type badge */
function ColumnTypeIcon({ type }: { type: ColumnType }) {
  switch (type) {
    case 'number':
      return (
        <span title="数值 (number)" className="inline-flex">
          <Hash size={11} className="text-status-info opacity-75" aria-hidden />
        </span>
      );
    case 'string':
      return (
        <span title="文本 (string)" className="inline-flex">
          <Type size={11} className="text-ink-muted opacity-75" aria-hidden />
        </span>
      );
    case 'boolean':
      return (
        <span title="布尔 (boolean)" className="inline-flex">
          <ToggleLeft size={11} className="text-status-accent opacity-75" aria-hidden />
        </span>
      );
    case 'geometry':
      return (
        <span title="空间几何 (geometry)" className="inline-flex">
          <MapPin size={11} className="text-status-success opacity-75" aria-hidden />
        </span>
      );
    case 'object':
    case 'array':
      return (
        <span title="结构体/数组 (object/array)" className="inline-flex">
          <Boxes size={11} className="text-status-warning opacity-75" aria-hidden />
        </span>
      );
    default:
      return null;
  }
}

/** Format cell value for display */
function formatCellValue(value: unknown, type: ColumnType): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="font-mono text-micro italic text-ink-disabled">null</span>;
  }
  if (value === '') {
    return <span className="font-mono text-micro italic text-ink-disabled">(空)</span>;
  }
  if (typeof value === 'boolean') {
    return (
      <span
        className={clsx(
          'inline-flex items-center rounded px-1.5 py-0.2 text-micro font-mono font-medium',
          value
            ? 'bg-status-success-soft text-status-success'
            : 'bg-surface-sunken text-ink-muted'
        )}
      >
        {value ? 'true' : 'false'}
      </span>
    );
  }
  if (type === 'geometry' || (typeof value === 'string' && (value.startsWith('Point [') || value.startsWith('Polygon') || value.startsWith('LineString')))) {
    const geomStr = typeof value === 'object' && value !== null ? formatGeometrySummary(value) : String(value);
    return (
      <span className="inline-flex items-center gap-1 font-mono text-micro text-status-success">
        <MapPin size={10} className="shrink-0" />
        <span className="truncate">{geomStr}</span>
      </span>
    );
  }
  if (typeof value === 'object') {
    const str = JSON.stringify(value);
    return (
      <span
        className="inline-block max-w-[200px] truncate rounded bg-surface-sunken px-1 py-0.5 font-mono text-micro text-ink-secondary"
        title={str}
      >
        {str}
      </span>
    );
  }
  if (typeof value === 'number') {
    return <span className="font-mono text-ink">{Number.isFinite(value) ? String(value) : value}</span>;
  }
  return <span className="truncate text-ink">{String(value)}</span>;
}

/** Row copy action button */
function RowCopyButton({ rowData, rowIndex }: { rowData: Record<string, unknown>; rowIndex: number }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      navigator.clipboard.writeText(JSON.stringify(rowData, null, 2)).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      });
    },
    [rowData]
  );

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={`复制第 ${rowIndex + 1} 行数据`}
      title={copied ? '已复制行 JSON' : `复制第 ${rowIndex + 1} 行数据`}
      className={clsx(
        'flex h-5 w-5 items-center justify-center rounded transition-colors',
        copied
          ? 'bg-status-success-soft text-status-success'
          : 'text-ink-disabled hover:bg-surface-hover hover:text-ink'
      )}
    >
      {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
    </button>
  );
}

/**
 * TabularDataGrid — 交互式空间属性数据表格组件。
 *
 * 特性：
 * - 动态 Schema 提取与数据类型识别 (string/number/boolean/geometry/object)
 * - 列头点击三态排序 (asc / desc / reset)
 * - 实时全表检索匹配与高亮提示
 * - 行序号 (#) 与单行 JSON 快速复制
 * - 响应式分页控制 (10/25/50/100 每页切换，快捷翻页，跳页指示)
 * - 语义化主题令牌 (surface-panel, surface-raised, edge-subtle, text-ink, status-*)
 * - 优雅加载与空状态
 */
export function TabularDataGrid({
  data,
  features,
  columns: customColumns,
  totalCount: customTotalCount,
  loading = false,
  defaultPageSize = 10,
  pageSizeOptions = [10, 25, 50, 100],
  enableSearch = true,
  enableSort = true,
  enableRowCopy = true,
  className,
  emptyTitle = '无数据记录',
  emptyDescription = '当前数据集为空或未包含要素',
  onRowClick,
}: TabularDataGridProps) {
  // Normalize rows
  const allRows = useMemo(() => normalizeRows(data, features), [data, features]);

  // Schema extraction
  const columns = useMemo(() => {
    if (customColumns && customColumns.length > 0) return customColumns;
    return extractColumnSchema(allRows);
  }, [customColumns, allRows]);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');

  // Sort state: cycle none -> asc -> desc -> none
  const [sortConfig, setSortConfig] = useState<SortConfig>({ key: null, direction: null });

  // Pagination state
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [currentPage, setCurrentPage] = useState(1);

  // Filtered rows
  const filteredRows = useMemo(() => {
    if (!searchQuery.trim()) return allRows;
    const q = searchQuery.trim().toLowerCase();
    return allRows.filter((row) =>
      Object.values(row).some((val) => {
        if (val === null || val === undefined) return false;
        if (typeof val === 'object') return JSON.stringify(val).toLowerCase().includes(q);
        return String(val).toLowerCase().includes(q);
      })
    );
  }, [allRows, searchQuery]);

  // Sorted rows
  const sortedRows = useMemo(() => {
    if (!sortConfig.key || !sortConfig.direction) return filteredRows;

    const { key, direction } = sortConfig;
    const mult = direction === 'asc' ? 1 : -1;

    return [...filteredRows].sort((a, b) => {
      const valA = a[key];
      const valB = b[key];

      // Nulls always sort to bottom
      if (valA === null || valA === undefined) return 1;
      if (valB === null || valB === undefined) return -1;

      if (typeof valA === 'number' && typeof valB === 'number') {
        return (valA - valB) * mult;
      }
      if (typeof valA === 'boolean' && typeof valB === 'boolean') {
        return (Number(valA) - Number(valB)) * mult;
      }

      // Numeric strings comparison
      const numA = Number(valA);
      const numB = Number(valB);
      if (!isNaN(numA) && !isNaN(numB) && typeof valA !== 'boolean' && typeof valB !== 'boolean') {
        return (numA - numB) * mult;
      }

      return String(valA).localeCompare(String(valB), undefined, { numeric: true, sensitivity: 'base' }) * mult;
    });
  }, [filteredRows, sortConfig]);

  // Total pages
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));

  // Current page rows
  const paginatedRows = useMemo(() => {
    const page = Math.min(currentPage, totalPages);
    const start = (page - 1) * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, currentPage, totalPages, pageSize]);

  // Handle sort column click
  const handleSort = useCallback(
    (columnKey: string) => {
      if (!enableSort) return;
      setSortConfig((prev) => {
        if (prev.key !== columnKey) {
          return { key: columnKey, direction: 'asc' };
        }
        if (prev.direction === 'asc') {
          return { key: columnKey, direction: 'desc' };
        }
        return { key: null, direction: null };
      });
    },
    [enableSort]
  );

  // Reset page when search or page size changes
  const handleSearchChange = useCallback((val: string) => {
    setSearchQuery(val);
    setCurrentPage(1);
  }, []);

  const handlePageSizeChange = useCallback((newSize: number) => {
    setPageSize(newSize);
    setCurrentPage(1);
  }, []);

  if (loading) {
    return (
      <div className={clsx('flex h-64 items-center justify-center rounded-lg border border-edge-subtle bg-surface-panel', className)}>
        <LoadingState label="正在加载数据集属性..." />
      </div>
    );
  }

  if (allRows.length === 0 && !loading) {
    return (
      <div className={clsx('rounded-lg border border-edge-subtle bg-surface-panel p-6', className)}>
        <EmptyState icon={FileSpreadsheet} title={emptyTitle} description={emptyDescription} />
      </div>
    );
  }

  const startRowIndex = (currentPage - 1) * pageSize;
  const endRowIndex = Math.min(startRowIndex + pageSize, sortedRows.length);
  const totalRowsCount = customTotalCount ?? allRows.length;

  return (
    <div className={clsx('flex flex-col gap-2 rounded-lg border border-edge-subtle bg-surface-panel p-3 shadow-panel', className)}>
      {/* Toolbar: Search + Stats + Export/Actions */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge-subtle pb-2.5">
        {enableSearch && (
          <div className="relative min-w-[200px] max-w-xs flex-1">
            <Search
              size={13}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="搜索属性内容..."
              aria-label="搜索属性内容"
              className="h-7 w-full rounded-sm border border-edge-subtle bg-surface-sunken pl-8 pr-7 text-caption text-ink placeholder:text-ink-disabled focus:border-status-accent-border focus:outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => handleSearchChange('')}
                aria-label="清空搜索"
                className="absolute right-1.5 top-1/2 flex h-4 w-4 -translate-y-1/2 items-center justify-center rounded text-ink-muted hover:text-ink"
              >
                <X size={11} aria-hidden />
              </button>
            )}
          </div>
        )}

        {/* Stats badge */}
        <div className="flex items-center gap-2 text-meta text-ink-secondary">
          {searchQuery ? (
            <span className="rounded bg-status-accent-soft px-2 py-0.5 text-micro font-medium text-status-accent">
              匹配 {sortedRows.length} / {totalRowsCount} 条
            </span>
          ) : (
            <span className="rounded bg-surface-sunken px-2 py-0.5 text-micro text-ink-muted">
              共 {totalRowsCount} 条要素 · {columns.length} 个字段
            </span>
          )}
        </div>
      </div>

      {/* Grid Container */}
      {sortedRows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <EmptyState
            icon={Search}
            title="未找到匹配记录"
            description={`没有属性匹配 "${searchQuery}"，请尝试更换关键词`}
            action={{
              label: '清空搜索',
              onClick: () => handleSearchChange(''),
            }}
          />
        </div>
      ) : (
        <div className="relative max-h-[52vh] min-h-[140px] overflow-auto rounded border border-edge-subtle bg-surface-sunken">
          <table className="w-full border-collapse text-left text-meta" role="table">
            <thead className="sticky top-0 z-10 bg-surface-raised shadow-sm">
              <tr className="border-b border-edge-subtle text-caption font-semibold text-ink">
                {/* Index column */}
                <th
                  scope="col"
                  className="sticky left-0 z-20 w-10 border-r border-edge-subtle bg-surface-raised px-2 py-1.5 text-center text-micro text-ink-muted"
                >
                  #
                </th>

                {/* Data columns */}
                {columns.map((col) => {
                  const isSorted = sortConfig.key === col.key;
                  const sortDir = isSorted ? sortConfig.direction : null;

                  return (
                    <th
                      key={col.key}
                      scope="col"
                      aria-sort={sortDir === 'asc' ? 'ascending' : sortDir === 'desc' ? 'descending' : 'none'}
                      className="whitespace-nowrap border-r border-edge-subtle px-2.5 py-1.5 last:border-r-0"
                    >
                      <button
                        type="button"
                        onClick={() => handleSort(col.key)}
                        className={clsx(
                          'group flex w-full items-center justify-between gap-1.5 text-left font-medium transition-colors',
                          isSorted ? 'text-status-accent font-semibold' : 'text-ink hover:text-status-accent'
                        )}
                        aria-label={`按 ${col.label} 排序`}
                      >
                        <div className="flex items-center gap-1 min-w-0">
                          <ColumnTypeIcon type={col.type} />
                          <span className="truncate">{col.label}</span>
                        </div>
                        {enableSort && (
                          <span className="shrink-0 text-ink-disabled group-hover:text-status-accent">
                            {sortDir === 'asc' ? (
                              <ArrowUp size={12} className="text-status-accent" />
                            ) : sortDir === 'desc' ? (
                              <ArrowDown size={12} className="text-status-accent" />
                            ) : (
                              <ArrowUpDown size={11} className="opacity-40 group-hover:opacity-100" />
                            )}
                          </span>
                        )}
                      </button>
                    </th>
                  );
                })}

                {/* Action column */}
                {enableRowCopy && (
                  <th scope="col" className="w-9 px-2 py-1.5 text-center text-micro text-ink-muted">
                    操作
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-edge-subtle bg-surface-panel">
              {paginatedRows.map((row, rowIdx) => {
                const absoluteIndex = startRowIndex + rowIdx;
                return (
                  <tr
                    key={absoluteIndex}
                    onClick={() => onRowClick?.(row, absoluteIndex)}
                    className={clsx(
                      'transition-colors hover:bg-surface-hover/60',
                      onRowClick && 'cursor-pointer'
                    )}
                  >
                    {/* Index cell */}
                    <td className="sticky left-0 z-0 border-r border-edge-subtle bg-surface-sunken/40 px-2 py-1.5 text-center font-mono text-micro text-ink-disabled">
                      {absoluteIndex + 1}
                    </td>

                    {/* Column cells */}
                    {columns.map((col) => (
                      <td
                        key={col.key}
                        className="max-w-[260px] truncate border-r border-edge-subtle px-2.5 py-1.5 last:border-r-0"
                      >
                        {formatCellValue(row[col.key], col.type)}
                      </td>
                    ))}

                    {/* Row copy action */}
                    {enableRowCopy && (
                      <td className="px-1.5 py-1 text-center">
                        <RowCopyButton rowData={row} rowIndex={absoluteIndex} />
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination Footer */}
      {sortedRows.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-meta text-ink-secondary">
          {/* Range info */}
          <div className="text-caption text-ink-muted">
            显示 <span className="font-mono text-ink font-medium">{startRowIndex + 1}</span>–
            <span className="font-mono text-ink font-medium">{endRowIndex}</span> / 共{' '}
            <span className="font-mono text-ink font-medium">{sortedRows.length}</span> 条
          </div>

          {/* Controls */}
          <div className="flex items-center gap-3">
            {/* Page Size Selector */}
            <div className="flex items-center gap-1.5 text-caption">
              <span className="text-ink-muted">每页</span>
              <select
                value={pageSize}
                onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                aria-label="每页显示条数"
                className="h-6 rounded border border-edge-subtle bg-surface-sunken px-1.5 font-mono text-micro text-ink focus:border-status-accent-border focus:outline-none"
              >
                {pageSizeOptions.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            </div>

            {/* Pagination navigation buttons */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setCurrentPage(1)}
                disabled={currentPage <= 1}
                aria-label="第一页"
                title="第一页"
                className="flex h-6 w-6 items-center justify-center rounded border border-edge-subtle bg-surface-sunken text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronsLeft size={13} aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
                aria-label="上一页"
                title="上一页"
                className="flex h-6 w-6 items-center justify-center rounded border border-edge-subtle bg-surface-sunken text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronLeft size={13} aria-hidden />
              </button>

              <span className="px-1.5 font-mono text-caption text-ink">
                {currentPage} / {totalPages}
              </span>

              <button
                type="button"
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage >= totalPages}
                aria-label="下一页"
                title="下一页"
                className="flex h-6 w-6 items-center justify-center rounded border border-edge-subtle bg-surface-sunken text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronRight size={13} aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setCurrentPage(totalPages)}
                disabled={currentPage >= totalPages}
                aria-label="最后一页"
                title="最后一页"
                className="flex h-6 w-6 items-center justify-center rounded border border-edge-subtle bg-surface-sunken text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-40 disabled:pointer-events-none"
              >
                <ChevronsRight size={13} aria-hidden />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default TabularDataGrid;
