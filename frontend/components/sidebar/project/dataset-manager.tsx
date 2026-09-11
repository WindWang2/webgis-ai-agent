'use client';

/**
 * DatasetManager — 项目数据集管理面板（ADR-0143 P2）。
 *
 * 后端契约（勘察报告 §2.2）：datasets 族仅 attach / detach / list 三端点。
 * - 无 rename 端点 → 不提供重命名（协调点，见 PR 描述）。
 * - schema_profile 仅在 attach 响应返回一次 → 本会话内捕获展示。
 * - detach 为软删除且后端不做引用检查 → 删除确认带依赖警示。
 * - 上传入口链接到既有 upload 流程，不重做上传（#1221 归 upload 线）。
 */

import { useState } from 'react';
import {
  Plus,
  Database,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Lock,
} from 'lucide-react';

import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { SearchField } from '@/components/shared/search-field';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import { SField } from '@/components/shared/section-title';
import {
  datasetDependencyWarning,
  useProjectDatasets,
} from '@/lib/hooks/use-project-assets';
import type { DatasetSourceType } from '@/lib/api/project-assets';
import type { ProjectDataset } from '@/lib/api/project';
import { formatCrs, shortId } from '@/lib/workflow/recovery';
import { formatIso } from './format';
import { DatasetPreview } from './dataset-preview';

const SOURCE_TYPES: Array<{ value: DatasetSourceType; label: string }> = [
  { value: 'layer', label: '图层（layer）' },
  { value: 'upload', label: '上传（upload）' },
  { value: 'external', label: '外部服务（external）' },
  { value: 'vector', label: '矢量目录（vector）' },
  { value: 'raster', label: '栅格目录（raster）' },
];

export interface DatasetManagerProps {
  projectId: string;
  authed: boolean;
  /** layer 型数据集跳主地图（交叉导航 P7）。 */
  onOpenInMap?: (datasetId: string) => void;
}

export function DatasetManager({ projectId, authed, onOpenInMap }: DatasetManagerProps) {
  const ds = useProjectDatasets(projectId);
  const addToast = useToastStore((s) => s.addToast);
  const [showAttach, setShowAttach] = useState(false);
  const [expandedId, setExpandedId] = useState('');
  const [filter, setFilter] = useState('');
  const [form, setForm] = useState<{
    name: string;
    source_type: DatasetSourceType;
    source_ref: string;
    crs: string;
  }>({ name: '', source_type: 'layer', source_ref: '', crs: '' });

  const visible = filter
    ? ds.datasets.filter((d) => d.name.toLowerCase().includes(filter.toLowerCase()))
    : ds.datasets;

  const handleAttach = async () => {
    if (!form.name.trim()) return;
    const attached = await ds.attach({
      name: form.name.trim(),
      source_type: form.source_type,
      source_ref: form.source_ref.trim() || undefined,
      crs: form.crs.trim() || undefined,
    });
    if (attached) {
      addToast(`已挂载数据集 ${attached.name}`, 'success');
      setForm({ name: '', source_type: 'layer', source_ref: '', crs: '' });
      setShowAttach(false);
      setExpandedId(attached.id);
    }
  };

  const handleDetach = async (d: ProjectDataset) => {
    const ok = await ds.detach(d.id);
    if (ok) {
      addToast(`已解绑数据集 ${d.name}`, 'success');
      if (expandedId === d.id) setExpandedId('');
    }
  };

  return (
    <section aria-labelledby="ds-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 id="ds-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
          <Database size={14} className="text-ink-muted" aria-hidden /> 数据集 ({ds.total})
        </h3>
        <span className="flex gap-1">
          <IconButton
            label="刷新数据集"
            icon={RefreshCw}
            iconSize={13}
            disabled={ds.loading}
            onClick={() => {
              void ds.reload({ forceRefresh: true });
            }}
          />
          <IconButton
            label="挂载数据集"
            icon={Plus}
            iconSize={15}
            active={showAttach}
            disabled={!authed}
            title={authed ? undefined : '需要登录账号（设置 → 账户）'}
            onClick={() => setShowAttach(!showAttach)}
          />
        </span>
      </div>

      {!authed && (
        <p className="flex items-center gap-1.5 text-caption text-ink-muted">
          <Lock size={12} aria-hidden /> 挂载/解绑需要登录账号
        </p>
      )}

      {ds.error && <InlineNotice variant="error">{ds.error}</InlineNotice>}

      {showAttach && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          <SField label="名称" value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="数据集名称…" />
          <label className="block space-y-1">
            <span className="block text-meta font-medium text-ink-secondary">来源类型</span>
            <select
              value={form.source_type}
              onChange={(e) => setForm({ ...form, source_type: e.target.value as DatasetSourceType })}
              className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
            >
              {SOURCE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <SField
            label="来源引用（source_ref）"
            value={form.source_ref}
            onChange={(v) => setForm({ ...form, source_ref: v })}
            placeholder="目录项 ID / 图层 ID…"
            hint="指向数据目录项时，详情可预览样例数据"
          />
          <SField
            label="CRS（可选）"
            value={form.crs}
            onChange={(v) => setForm({ ...form, crs: v })}
            placeholder="留空由后端默认"
          />
          {form.source_type === 'upload' && (
            <p className="text-micro text-ink-muted">
              文件上传请使用左侧「数据源」上传区完成后再挂载——本面板不重复上传能力。
            </p>
          )}
          <button
            type="button"
            onClick={() => {
              void handleAttach();
            }}
            disabled={!authed || ds.busyId === 'attach' || !form.name.trim()}
            className="w-full rounded-sm bg-status-accent py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {ds.busyId === 'attach' ? '挂载中…' : '确认挂载'}
          </button>
        </div>
      )}

      {ds.loading && ds.datasets.length === 0 ? (
        <LoadingState label="加载数据集…" />
      ) : visible.length === 0 ? (
        <EmptyState icon={Database} title={filter ? '无匹配数据集' : '暂无挂载数据集'} />
      ) : (
        <div className="space-y-1.5">
          {visible.map((d) => {
            const expanded = expandedId === d.id;
            const schema = ds.schemaByDataset[d.id];
            return (
              <div key={d.id} className="rounded-md border border-edge-subtle bg-surface-raised">
                <div className="flex items-center justify-between gap-2 px-panel py-2">
                  <button
                    type="button"
                    onClick={() => setExpandedId(expanded ? '' : d.id)}
                    aria-expanded={expanded}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    {expanded ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
                    )}
                    <span className="min-w-0">
                      <span className="block truncate text-meta font-medium text-ink">{d.name}</span>
                      <span className="block text-micro text-ink-muted">
                        {formatCrs(d.crs)} • {d.source_type}
                      </span>
                    </span>
                  </button>
                  <span className="flex shrink-0 items-center gap-1">
                    <StatusBadge status={d.quality_status || 'unchecked'} />
                    <ConfirmAction
                      label="解绑"
                      confirmLabel="确认解绑？"
                      onConfirm={() => {
                        void handleDetach(d);
                      }}
                      disabled={!authed || ds.busyId === d.id}
                      title={authed ? '解绑（软删除）' : '需要登录账号'}
                    />
                  </span>
                </div>

                {expanded && (
                  <div className="space-y-2 border-t border-edge-subtle px-panel py-2">
                    <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-micro text-ink-secondary">
                      <dt>ID</dt>
                      <dd className="truncate font-mono" title={d.id}>{shortId(d.id, 16)}</dd>
                      <dt>来源</dt>
                      <dd className="truncate font-mono" title={d.source_ref ?? ''}>{shortId(d.source_ref, 20)}</dd>
                      <dt>创建于</dt>
                      <dd>{formatIso(d.created_at)}</dd>
                    </dl>

                    {datasetDependencyWarning(d) && (
                      <InlineNotice variant="warning">{datasetDependencyWarning(d)}</InlineNotice>
                    )}

                    <DatasetPreview datasetId={d.id} sourceRef={d.source_ref} onOpenInMap={onOpenInMap} />

                    {schema && Object.keys(schema).length > 0 && (
                      <details className="text-micro text-ink-secondary">
                        <summary className="cursor-pointer select-none">Schema（挂载时捕获）</summary>
                        <pre className="mt-1 max-h-32 overflow-auto rounded-sm bg-surface-sunken p-1.5 font-mono text-micro">
                          {JSON.stringify(schema, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {ds.hasMore && (
            <button
              type="button"
              onClick={() => {
                void ds.loadMore();
              }}
              className="w-full rounded-sm border border-edge-subtle py-1 text-micro text-ink-secondary hover:bg-surface-sunken"
            >
              加载更多（已加载 {ds.datasets.length}/{ds.total}）
            </button>
          )}
        </div>
      )}

      {visible.length > 5 && (
        <SearchField
          value={filter}
          onChange={setFilter}
          placeholder="筛选数据集…"
          aria-label="筛选数据集"
        />
      )}
    </section>
  );
}
