'use client';


/**
 * DatasetManager — 项目数据集管理面板（ADR-0143 P2）。
 *
 * 后端契约（勘察报告 §2.2）：datasets 族仅 attach / detach / list 三端点。
 * - 无 rename 端点 → 不提供重命名（协调点，见 PR 描述）。
 * - schema_profile 仅在 attach 响应返回一次 → 本会话内捕获展示。
 * - detach 为软删除且后端不做引用检查 → 删除确认带依赖警示。
 * - source_type=upload 走真实上传：文件选择器 → lib/api/upload uploadFile
 *   （POST /api/v1/upload multipart，既有传输合同）→ 以返回 ref 挂载
 *   （#1555：主上传链必须有真实 UI 入口，e2e 不得 API 直打）。
 */

import { useRef, useState } from 'react';
import {
  Plus,
  Database,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Lock,
  Upload,
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
import { uploadFile } from '@/lib/api/upload';
import type { DatasetSourceType } from '@/lib/api/project-assets';
import type { ProjectDataset } from '@/lib/api/project';
import { formatCrs, shortId } from '@/lib/workflow/recovery';
import { formatIso } from './format';
import { DatasetPreview } from './dataset-preview';
import { useT } from '@/lib/i18n/useT';

const SOURCE_TYPES: Array<{ value: DatasetSourceType; labelKey: string }> = [
  { value: 'layer', labelKey: 'layer' },
  { value: 'upload', labelKey: 'upload' },
  { value: 'external', labelKey: 'external' },
  { value: 'vector', labelKey: 'vector' },
  { value: 'raster', labelKey: 'raster' },
];

export interface DatasetManagerProps {
  projectId: string;
  authed: boolean;
  /** layer 型数据集跳主地图（交叉导航 P7）。 */
  onOpenInMap?: (datasetId: string) => void;
}

export function DatasetManager({ projectId, authed, onOpenInMap }: DatasetManagerProps) {
  const t = useT('project');
  const ds = useProjectDatasets(projectId);
  const addToast = useToastStore((s) => s.addToast);
  const [showAttach, setShowAttach] = useState(false);
  const [expandedId, setExpandedId] = useState('');
  const [filter, setFilter] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [uploadedRef, setUploadedRef] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<{
    name: string;
    source_type: DatasetSourceType;
    source_ref: string;
    crs: string;
  }>({ name: '', source_type: 'layer', source_ref: '', crs: '' });

  const visible = filter
    ? ds.datasets.filter((d) => d.name.toLowerCase().includes(filter.toLowerCase()))
    : ds.datasets;

  /** 上传腿：pick 文件 → POST /api/v1/upload → 记录返回 ref（挂载用它）。 */
  const handleFilePicked = async (file: File | undefined) => {
    if (!file) return;
    setForm((f) => ({
      ...f,
      source_type: 'upload',
      name: f.name || file.name.replace(/\.[^.]+$/, ''),
    }));
    setUploading(true);
    setUploadPct(0);
    try {
      const resp = await uploadFile(file, undefined, (pct) => setUploadPct(pct));
      const ref = resp.session_ref || String(resp.id);
      setUploadedRef(ref);
      setForm((f) => ({ ...f, source_ref: ref }));
      addToast(t('dataset.uploadDone', { name: resp.original_name }), 'success');
    } catch (err) {
      addToast(
        `${t('dataset.uploadFailed')}: ${err instanceof Error ? err.message : String(err)}`,
        'error',
      );
    } finally {
      setUploading(false);
    }
  };

  const handleAttach = async () => {
    if (!form.name.trim()) return;
    if (form.source_type === 'upload' && !uploadedRef && !form.source_ref.trim()) return;
    const attached = await ds.attach({
      name: form.name.trim(),
      source_type: form.source_type,
      source_ref: form.source_ref.trim() || undefined,
      crs: form.crs.trim() || undefined,
    });
    if (attached) {
      addToast(t('dataset.attached', { name: attached.name }), 'success');
      setForm({ name: '', source_type: 'layer', source_ref: '', crs: '' });
      setUploadedRef('');
      if (fileInputRef.current) fileInputRef.current.value = '';
      setShowAttach(false);
      setExpandedId(attached.id);
    }
  };

  const handleDetach = async (d: ProjectDataset) => {
    const ok = await ds.detach(d.id);
    if (ok) {
      addToast(t('dataset.detached', { name: d.name }), 'success');
      if (expandedId === d.id) setExpandedId('');
    }
  };

  return (
    <section aria-labelledby="ds-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 id="ds-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
          <Database size={14} className="text-ink-muted" aria-hidden /> {t('kv4zwgd', { p0: ds.total })}</h3>
        <span className="flex gap-1">
          <IconButton
            label={t('k2k90i3')}
            icon={RefreshCw}
            iconSize={13}
            disabled={ds.loading}
            onClick={() => {
              void ds.reload({ forceRefresh: true });
            }}
          />
          <IconButton
            label={t('km7h60z')}
            icon={Plus}
            iconSize={15}
            active={showAttach}
            disabled={!authed}
            title={authed ? undefined : t('auth.loginRequiredSettings')}
            onClick={() => setShowAttach(!showAttach)}
          />
        </span>
      </div>

      {!authed && (
        <p className="flex items-center gap-1.5 text-caption text-ink-muted">
          <Lock size={12} aria-hidden /> {t('k6ztlgw')}</p>
      )}

      {ds.error && <InlineNotice variant="error">{ds.error}</InlineNotice>}

      {showAttach && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          <SField label={t('kfvz1')} value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder={t('k1x9x7pj')} />
          <label className="block space-y-1">
            <span className="block text-meta font-medium text-ink-secondary">{t('kg9bj97')}</span>
            <select
              value={form.source_type}
              onChange={(e) => {
                const next = e.target.value as DatasetSourceType;
                if (next !== 'upload') {
                  setUploadedRef('');
                  if (fileInputRef.current) fileInputRef.current.value = '';
                }
                setForm({ ...form, source_type: next });
              }}
              className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
            >
              {SOURCE_TYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {t(item.labelKey)}
                </option>
              ))}
            </select>
          </label>
          <SField
            label={t('sourceRef')}
            value={form.source_ref}
            onChange={(v) => setForm({ ...form, source_ref: v })}
            placeholder={t('idId')}
            hint={t('kh7ga0q')}
          />
          <SField
            label={t('crs')}
            value={form.crs}
            onChange={(v) => setForm({ ...form, crs: v })}
            placeholder={t('kkz1059')}
          />
          {form.source_type === 'upload' && (
            <div className="space-y-1">
              <input
                ref={fileInputRef}
                type="file"
                aria-label={t('dataset.pickFile')}
                data-testid="dataset-upload-input"
                disabled={!authed || uploading}
                onChange={(e) => {
                  void handleFilePicked(e.target.files?.[0]);
                }}
                className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink file:mr-2 file:rounded-sm file:border-0 file:bg-status-accent file:px-2 file:py-0.5 file:text-micro file:text-ink-on-accent focus:outline-none focus:ring-1 focus:ring-status-accent"
              />
              {uploading && (
                <p className="text-micro text-ink-muted" role="status">
                  {t('dataset.uploading', { p0: uploadPct })}
                </p>
              )}
              {!uploading && uploadedRef && (
                <p className="text-micro text-ink-muted" data-testid="dataset-upload-done">
                  {t('dataset.uploadReady')}
                </p>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={() => {
              void handleAttach();
            }}
            disabled={!authed || ds.busyId === 'attach' || !form.name.trim()
              || (form.source_type === 'upload' && uploading)
              || (form.source_type === 'upload' && !uploadedRef && !form.source_ref.trim())}
            className="flex w-full items-center justify-center gap-1.5 rounded-sm bg-status-accent py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Upload size={12} aria-hidden />
            {ds.busyId === 'attach' ? t('dataset.attaching') : t('dataset.confirmAttach')}
          </button>
        </div>
      )}

      {ds.loading && ds.datasets.length === 0 ? (
        <LoadingState label={t('k78ugfb')} />
      ) : visible.length === 0 ? (
        <EmptyState icon={Database} title={filter ? t('dataset.noMatch') : t('dataset.noneAttached')} />
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
                      label={t('kpnv8')}
                      confirmLabel={t('k1mbk9c5')}
                      onConfirm={() => {
                        void handleDetach(d);
                      }}
                      disabled={!authed || ds.busyId === d.id}
                      title={authed ? t('dataset.detachTitle') : t('auth.needLogin')}
                    />
                  </span>
                </div>

                {expanded && (
                  <div className="space-y-2 border-t border-edge-subtle px-panel py-2">
                    <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-micro text-ink-secondary">
                      <dt>ID</dt>
                      <dd className="truncate font-mono" title={d.id}>{shortId(d.id, 16)}</dd>
                      <dt>{t('kjbth')}</dt>
                      <dd className="truncate font-mono" title={d.source_ref ?? ''}>{shortId(d.source_ref, 20)}</dd>
                      <dt>{t('ke48f7')}</dt>
                      <dd>{formatIso(d.created_at)}</dd>
                    </dl>

                    {datasetDependencyWarning(d) && (
                      <InlineNotice variant="warning">{datasetDependencyWarning(d)}</InlineNotice>
                    )}

                    <DatasetPreview datasetId={d.id} sourceRef={d.source_ref} onOpenInMap={onOpenInMap} />

                    {schema && Object.keys(schema).length > 0 && (
                      <details className="text-micro text-ink-secondary">
                        <summary className="cursor-pointer select-none">{t('schema')}</summary>
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
              {t('p0P16', { p0: ds.datasets.length, p1: ds.total })}</button>
          )}
        </div>
      )}

      {visible.length > 5 && (
        <SearchField
          value={filter}
          onChange={setFilter}
          placeholder={t('kap7g7i')}
          aria-label={t('k2h5sq0')}
        />
      )}
    </section>
  );
}
