'use client';

import React, { useRef, useState, useCallback } from 'react';
import {
  X,
  Table as TableIcon,
  Code2,
  Copy,
  Check,
  Layers,
} from 'lucide-react';
import clsx from 'clsx';
import type { QueryResult } from '@/lib/api/data-fabric';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { IconButton } from '@/components/shared/icon-button';
import { TabularDataGrid } from './tabular-data-grid';

export interface PreviewModalProps {
  /** QueryResult, GeoJSONFeatureCollection, or array of features */
  result: QueryResult | GeoJSONFeatureCollection | Array<Record<string, unknown>>;
  /** Custom dataset title */
  title?: string;
  /** Modal close callback */
  onClose: () => void;
}

type TabType = 'grid' | 'json';

/**
 * PreviewModal — 空间数据集 / 分析结果样例预览弹窗。
 *
 * 升级特性：
 * - 属性表格 (Tabular Data Grid) 与 原始 JSON 双视图自由切换
 * - 完整的列排序、字段 Schema 提取、模糊检索与分页控制器
 * - 一键复制全量 JSON / 单行属性 / 导出数据
 * - 完备的 WAI-APG 对话框焦点管理与快捷键（Escape 关闭，Tab 循环，方向键切换页签）
 * - 响应式多端适配与语义化主题令牌（surface-panel, border-edge-subtle, text-ink）
 */
export function PreviewModal({ result, title, onClose }: PreviewModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open: true, containerRef: dialogRef, onEscape: onClose });

  const [activeTab, setActiveTab] = useState<TabType>('grid');
  const [jsonCopied, setJsonCopied] = useState(false);

  // Normalize features & dataset info
  const features = Array.isArray(result)
    ? result
    : typeof result === 'object' && result !== null && 'features' in result && Array.isArray(result.features)
    ? result.features
    : [];

  const datasetId =
    typeof result === 'object' && result !== null && 'dataset_id' in result
      ? (result as QueryResult).dataset_id
      : undefined;

  const totalCount =
    typeof result === 'object' && result !== null && 'total_count' in result && typeof (result as QueryResult).total_count === 'number'
      ? (result as QueryResult).total_count
      : features.length;

  const displayTitle = title || (datasetId ? `数据集: ${datasetId}` : '数据样例预览');

  // Copy full JSON payload
  const handleCopyJson = useCallback(() => {
    const rawData = JSON.stringify(result, null, 2);
    navigator.clipboard.writeText(rawData).then(() => {
      setJsonCopied(true);
      setTimeout(() => setJsonCopied(false), 2000);
    });
  }, [result]);

  // Tab keyboard navigation (WAI-APG tablist)
  const handleTabKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      setActiveTab((prev) => (prev === 'grid' ? 'json' : 'grid'));
    }
  }, []);

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="数据样例预览"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim p-3 sm:p-6"
    >
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-lg border border-edge-subtle bg-surface-panel shadow-overlay animate-in fade-in zoom-in-95 duration-200">
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-edge-subtle px-4 py-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-status-accent-soft text-status-accent">
              <Layers size={15} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-title font-bold text-ink" title={displayTitle}>
                  {displayTitle}
                </h3>
                <span className="shrink-0 rounded-pill bg-surface-sunken border border-edge-subtle px-2 py-0.5 font-mono text-micro text-ink-secondary">
                  共 {totalCount} 要素
                </span>
              </div>
            </div>
          </div>

          <IconButton label="关闭" icon={X} onClick={onClose} />
        </div>

        {/* Navigation Tabs Bar & Action Toolbar */}
        <div className="flex items-center justify-between border-b border-edge-subtle bg-surface-sunken/50 px-4 py-1.5">
          {/* Tab buttons */}
          <div
            role="tablist"
            aria-label="数据视图切换"
            onKeyDown={handleTabKeyDown}
            className="flex items-center gap-1"
          >
            <button
              type="button"
              role="tab"
              id="preview-tab-grid"
              aria-selected={activeTab === 'grid'}
              aria-controls="preview-panel-grid"
              tabIndex={activeTab === 'grid' ? 0 : -1}
              onClick={() => setActiveTab('grid')}
              className={clsx(
                'flex items-center gap-1.5 rounded-sm px-3 py-1 text-meta font-medium transition-all',
                activeTab === 'grid'
                  ? 'bg-surface-raised text-status-accent shadow-sm border border-edge-subtle font-semibold'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              )}
            >
              <TableIcon size={13} aria-hidden />
              <span>属性表格</span>
            </button>

            <button
              type="button"
              role="tab"
              id="preview-tab-json"
              aria-selected={activeTab === 'json'}
              aria-controls="preview-panel-json"
              tabIndex={activeTab === 'json' ? 0 : -1}
              onClick={() => setActiveTab('json')}
              className={clsx(
                'flex items-center gap-1.5 rounded-sm px-3 py-1 text-meta font-medium transition-all',
                activeTab === 'json'
                  ? 'bg-surface-raised text-status-accent shadow-sm border border-edge-subtle font-semibold'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              )}
            >
              <Code2 size={13} aria-hidden />
              <span>原始 JSON</span>
            </button>
          </div>

          {/* Quick Actions */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCopyJson}
              aria-label="复制原始 JSON 数据"
              title={jsonCopied ? '已复制全量 JSON' : '复制原始 JSON 数据'}
              className={clsx(
                'flex items-center gap-1.5 rounded-sm border border-edge-subtle px-2.5 py-1 text-meta font-medium transition-colors',
                jsonCopied
                  ? 'bg-status-success-soft text-status-success border-status-success'
                  : 'bg-surface-raised text-ink-secondary hover:bg-surface-hover hover:text-ink'
              )}
            >
              {jsonCopied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
              <span>{jsonCopied ? '已复制' : '复制 JSON'}</span>
            </button>
          </div>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === 'grid' ? (
            <div
              id="preview-panel-grid"
              role="tabpanel"
              aria-labelledby="preview-tab-grid"
              className="space-y-3"
            >
              <TabularDataGrid
                data={result}
                totalCount={totalCount}
                defaultPageSize={10}
                pageSizeOptions={[10, 25, 50]}
                enableSearch={true}
                enableSort={true}
                enableRowCopy={true}
              />
            </div>
          ) : (
            <div
              id="preview-panel-json"
              role="tabpanel"
              aria-labelledby="preview-tab-json"
              className="space-y-2"
            >
              <div className="flex items-center justify-between text-meta text-ink-muted">
                <span>格式化 GeoJSON / 结果数据</span>
                <span className="font-mono text-micro">{features.length} 条记录</span>
              </div>
              <div className="max-h-[54vh] overflow-auto rounded-md border border-edge-subtle bg-surface-sunken p-3 font-mono text-caption text-ink">
                <pre className="text-ink-secondary whitespace-pre-wrap break-words">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default PreviewModal;
