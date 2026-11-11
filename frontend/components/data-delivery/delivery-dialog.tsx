'use client';

/**
 * DataDeliveryDialog — 数据成果交付对话框（G18 WP10）。
 *
 * 流程：源（调用方预绑定 ref/revision）→ 格式/范围/CRS/文件名 → 预览
 * （损失/阻断/完整性）→ 确认损失并提交（durable job）→ 轮询状态 → 回执
 * （行数/校验和/成员）+ 认证下载。取消复用统一任务中心 DELETE /tasks/jobs。
 *
 * 诚实性约定（与后端契约一致）：
 * - 范围标签只描述「当前快照」（行数 + 截断/采样披露），不宣称源全集；
 * - 阻断项（缺 CRS/格式不可用/选择截断）必须先解决才能提交；
 * - 损失确认逐项勾选（acknowledged_losses 覆盖全部 required codes）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { InlineNotice } from '@/components/shared/inline-notice';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { cancelJob } from '@/lib/api/jobs';
import {
  DeliveryFormat,
  DeliveryJobView,
  DeliveryPreview,
  DeliverySourceRef,
  DeliverySpec,
  fetchDeliveryCapabilities,
  getDeliveryJob,
  listDeliveryJobs,
  previewDataDelivery,
  submitDataDelivery,
} from '@/lib/api/data-delivery';

export interface DataDeliveryDialogProps {
  open: boolean;
  onClose: () => void;
  /** 预绑定的交付源（调用方来自 artifact/dataset 上下文）。 */
  source: DeliverySourceRef | null;
  /** 源显示名（artifact 名 / 数据集名）。 */
  sourceLabel?: string;
  /** 项目归属（登记交付成果；历史列表过滤）。 */
  projectId?: string | null;
}

const FORMAT_OPTIONS: { value: DeliveryFormat; labelKey: string }[] = [
  { value: 'geojson', labelKey: 'fmtGeojson' },
  { value: 'gpkg', labelKey: 'fmtGpkg' },
  { value: 'geoparquet', labelKey: 'fmtGeoparquet' },
  { value: 'csv', labelKey: 'fmtCsv' },
  { value: 'shapefile_zip', labelKey: 'fmtShp' },
  { value: 'flatgeobuf', labelKey: 'fmtFgb' },
];

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'stale']);

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function DataDeliveryDialog({
  open,
  onClose,
  source,
  sourceLabel,
  projectId,
}: DataDeliveryDialogProps) {
  const t = useT('dataDelivery');
  const addToast = useToastStore((s) => s.addToast);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open, containerRef: dialogRef, onEscape: onClose });

  const [format, setFormat] = useState<DeliveryFormat>('geojson');
  const [filename, setFilename] = useState('delivery');
  const [targetCrs, setTargetCrs] = useState('');
  const [allowUnknownCrs, setAllowUnknownCrs] = useState(false);
  const [precision, setPrecision] = useState('');
  const [grouping, setGrouping] = useState<'single' | 'by_type'>('by_type');
  const [spreadsheetSafe, setSpreadsheetSafe] = useState(false);

  const [preview, setPreview] = useState<DeliveryPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<DeliveryJobView | null>(null);
  const [history, setHistory] = useState<DeliveryJobView[]>([]);
  const [epoch, setEpoch] = useState(0);

  const spec: DeliverySpec | null = source
    ? {
        source,
        format,
        filename: filename || 'delivery',
        crs: {
          ...(targetCrs ? { target_crs: targetCrs } : {}),
          allow_unknown_source_crs: allowUnknownCrs,
          ...(precision ? { coordinate_precision: Number(precision) } : {}),
        },
        ...(format === 'shapefile_zip' ? { geometry_grouping: grouping } : {}),
        ...(format === 'csv' ? { spreadsheet_safe: spreadsheetSafe } : {}),
      }
    : null;

  const runPreview = useCallback(async () => {
    if (!spec) return;
    const myEpoch = epoch + 1;
    setEpoch(myEpoch);
    setPreviewing(true);
    setPreview(null);
    try {
      const p = await previewDataDelivery(spec);
      if (myEpoch !== epoch + 1) return; // 过期响应不覆盖
      setPreview(p);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('previewFailed')}: ${msg}`, 'error');
    } finally {
      if (myEpoch === epoch + 1) setPreviewing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec?.source?.ref, spec?.source?.artifact_id, format, filename,
      targetCrs, allowUnknownCrs, precision, grouping, spreadsheetSafe]);

  const pollJob = useCallback(
    async (jobId: string) => {
      for (let i = 0; i < 600; i += 1) {
        try {
          const view = await getDeliveryJob(jobId);
          setJob(view);
          if (TERMINAL.has(view.status)) return view;
        } catch {
          return null;
        }
        await new Promise((r) => setTimeout(r, 1200));
      }
      return null;
    },
    [],
  );

  const submit = useCallback(async () => {
    if (!spec || !preview) return;
    setSubmitting(true);
    try {
      const res = await submitDataDelivery(
        spec, preview.required_acknowledgments, projectId ?? undefined);
      addToast(res.message || t('submitted'));
      const view = await pollJob(res.job_id);
      if (view?.status === 'completed') {
        addToast(t('done'), 'success');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('submitFailed')}: ${msg}`, 'error');
    } finally {
      setSubmitting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, preview, projectId]);

  useEffect(() => {
    if (!open || !source) return;
    let cancelled = false;
    listDeliveryJobs(projectId ?? undefined)
      .then((jobs) => {
        if (!cancelled) setHistory(jobs.slice(0, 10));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open, source, projectId]);

  useEffect(() => {
    if (open && source) {
      setPreview(null);
      setJob(null);
    }
  }, [open, source]);

  if (!open || !source) return null;

  const ackRequired = preview?.required_acknowledgments ?? [];
  const blocking = preview?.blocking_issues ?? [];
  const losses = preview?.losses ?? [];
  const receipt = job?.receipt ?? null;

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="data-delivery-title"
      tabIndex={-1}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-surface-scrim p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col space-y-4 overflow-y-auto rounded-md border border-edge-subtle bg-surface-overlay p-5 shadow-overlay">
        <div className="flex items-start justify-between">
          <div>
            <h2 id="data-delivery-title" className="text-body font-semibold text-ink">
              {t('title')}
            </h2>
            <p className="text-meta text-ink-secondary">
              {sourceLabel || source.ref || source.artifact_id}
              {source.revision_no ? ` · ${t('revision')} ${source.revision_no}` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink-secondary transition-colors hover:bg-surface-hover"
          >
            {t('close')}
          </button>
        </div>

        {/* ── 配置 ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-meta text-ink-secondary">
            <span>{t('format')}</span>
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value as DeliveryFormat)}
              className="w-full rounded-sm border border-edge-subtle bg-surface px-2 py-1.5 text-body text-ink"
            >
              {FORMAT_OPTIONS.map((f) => (
                <option key={f.value} value={f.value}>
                  {t(f.labelKey)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-meta text-ink-secondary">
            <span>{t('filename')}</span>
            <input
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              maxLength={100}
              className="w-full rounded-sm border border-edge-subtle bg-surface px-2 py-1.5 text-body text-ink"
            />
          </label>
          <label className="space-y-1 text-meta text-ink-secondary">
            <span>{t('targetCrs')}</span>
            <input
              value={targetCrs}
              placeholder={t('crsPlaceholder')}
              onChange={(e) => setTargetCrs(e.target.value)}
              className="w-full rounded-sm border border-edge-subtle bg-surface px-2 py-1.5 text-body text-ink"
            />
          </label>
          {format === 'geojson' || format === 'csv' ? (
            <label className="space-y-1 text-meta text-ink-secondary">
              <span>{t('precision')}</span>
              <input
                value={precision}
                placeholder={t('precisionHint')}
                onChange={(e) => setPrecision(e.target.value)}
                inputMode="numeric"
                className="w-full rounded-sm border border-edge-subtle bg-surface px-2 py-1.5 text-body text-ink"
              />
            </label>
          ) : null}
          {format === 'shapefile_zip' ? (
            <label className="space-y-1 text-meta text-ink-secondary">
              <span>{t('geomGrouping')}</span>
              <select
                value={grouping}
                onChange={(e) => setGrouping(e.target.value as 'single' | 'by_type')}
                className="w-full rounded-sm border border-edge-subtle bg-surface px-2 py-1.5 text-body text-ink"
              >
                <option value="by_type">{t('groupByType')}</option>
                <option value="single">{t('groupSingle')}</option>
              </select>
            </label>
          ) : null}
          {format === 'csv' ? (
            <label className="flex items-center gap-2 text-meta text-ink-secondary">
              <input
                type="checkbox"
                checked={spreadsheetSafe}
                onChange={(e) => setSpreadsheetSafe(e.target.checked)}
              />
              {t('spreadsheetSafe')}
            </label>
          ) : null}
          <label className="flex items-center gap-2 text-meta text-ink-secondary">
            <input
              type="checkbox"
              checked={allowUnknownCrs}
              onChange={(e) => setAllowUnknownCrs(e.target.checked)}
            />
            {t('allowUnknownCrs')}
          </label>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={runPreview}
            disabled={previewing}
            className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta font-medium text-ink transition-colors hover:bg-surface-hover disabled:opacity-50"
          >
            {previewing ? t('previewing') : t('preview')}
          </button>
          {preview && blocking.length === 0 ? (
            <button
              type="button"
              onClick={submit}
              disabled={submitting || (job != null && !TERMINAL.has(job.status))}
              className="rounded-sm px-3 py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? t('submitting') : t('submit')}
            </button>
          ) : null}
          {job && !TERMINAL.has(job.status) ? (
            <button
              type="button"
              onClick={async () => {
                try {
                  await cancelJob(job.job_id);
                  addToast(t('cancelSent'));
                } catch {
                  addToast(t('cancelFailed'), 'error');
                }
              }}
              className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink-secondary transition-colors hover:bg-surface-hover"
            >
              {t('cancel')}
            </button>
          ) : null}
        </div>

        {/* ── 预览 ─────────────────────────────────────────────── */}
        {preview ? (
          <div className="space-y-2 rounded-sm border border-edge-subtle p-3">
            <div className="text-meta text-ink-secondary">
              {t('sourceRows')}: <span className="text-ink">{preview.source.row_count}</span>
              {' · '}
              {preview.source.is_table ? t('tableSource') : t('vectorSource')}
              {' · '}
              CRS: <span className="text-ink">{preview.source.crs || t('crsUnknown')}</span>
              {preview.source.geometry && preview.source.geometry.types.length > 1
                ? ` · ${t('mixedGeometry')}` : ''}
              {preview.scope_note ? (
                <>
                  {' · '}
                  {preview.scope_note}
                </>
              ) : null}
            </div>
            {blocking.length > 0 ? (
              <InlineNotice variant="error">
                <div className="font-medium">{t('blockingTitle')}</div>
                <ul className="list-disc space-y-1 pl-4">
                  {blocking.slice(0, 8).map((b) => (
                    <li key={b.code}>{b.message}</li>
                  ))}
                </ul>
              </InlineNotice>
            ) : null}
            {losses.length > 0 ? (
              <div className="space-y-1">
                <div className="text-meta font-medium text-ink">{t('lossesTitle')}</div>
                <ul className="list-disc space-y-1 pl-4 text-meta text-ink-secondary">
                  {losses.slice(0, 12).map((l) => (
                    <li key={l.code}>
                      {l.message}
                      {l.requires_ack ? `（${t('ackRequired')}）` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="text-meta text-ink-secondary">{t('noLosses')}</div>
            )}
            {preview.field_name_map_applied &&
            Object.keys(preview.field_name_map_applied).length > 0 ? (
              <div className="text-meta text-ink-secondary">
                {t('fieldMap')}:{' '}
                {Object.entries(preview.field_name_map_applied)
                  .slice(0, 8)
                  .map(([k, v]) => `${k}→${v}`)
                  .join(', ')}
              </div>
            ) : null}
            {ackRequired.length > 0 ? (
              <InlineNotice variant="info">
                <div className="font-medium">{t('ackTitle')}</div>
                {t('ackBody')}
              </InlineNotice>
            ) : null}
          </div>
        ) : null}

        {/* ── 状态 / 回执 ─────────────────────────────────────── */}
        {job ? (
          <div className="space-y-2 rounded-sm border border-edge-subtle p-3">
            <div className="flex items-center justify-between">
              <div className="text-meta text-ink">
                {t('status')}: {job.status}
                {job.progress_message ? ` · ${job.progress_message}` : ''}
              </div>
              {job.status === 'completed' && job.download_url ? (
                <button
                  type="button"
                  onClick={() => {
                    downloadWithAuth(job.download_url ?? '', {
                      filename: receipt?.filename,
                    }).catch(() => addToast(t('downloadFailed'), 'error'));
                  }}
                  className="rounded-sm px-3 py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90"
                >
                  {t('download')}
                </button>
              ) : null}
            </div>
            {job.error_code ? (
              <InlineNotice variant="error">
                <div className="font-medium">{job.error_code}</div>
                {job.error ?? ''}
              </InlineNotice>
            ) : null}
            {receipt ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-meta">
                <div className="text-ink-secondary">
                  {t('rows')}
                  <dd className="text-ink">{receipt.row_count}</dd>
                </div>
                <div className="text-ink-secondary">
                  {t('bytes')}
                  <dd className="text-ink">{formatBytes(receipt.bytes_total)}</dd>
                </div>
                <div className="text-ink-secondary">
                  {t('sha256')}
                  <dd className="truncate font-mono text-ink" title={receipt.content_sha256 ?? ''}>
                    {(receipt.content_sha256 ?? '').slice(0, 16)}…
                  </dd>
                </div>
                <div className="text-ink-secondary">
                  {t('crsEvidence')}
                  <dd className="text-ink">
                    {receipt.crs.source ?? '?'} → {receipt.crs.target ?? '?'}
                    {receipt.crs.transformed ? ` (${t('transformed')})` : ''}
                  </dd>
                </div>
                {receipt.losses_applied.length > 0 ? (
                  <div className="col-span-2 text-ink-secondary">
                    {t('lossesApplied')}
                    <dd className="text-ink">{receipt.losses_applied.join(', ')}</dd>
                  </div>
                ) : null}
              </dl>
            ) : null}
          </div>
        ) : null}

        {/* ── 历史交付（server state 恢复）────────────────────── */}
        {history.length > 0 ? (
          <div className="space-y-1">
            <div className="text-meta font-medium text-ink">{t('history')}</div>
            <ul className="space-y-1">
              {history.slice(0, 6).map((h) => (
                <li key={h.job_id} className="flex items-center justify-between text-meta">
                  <span className="truncate text-ink-secondary">
                    {h.filename || h.format} · {h.status}
                    {h.receipt ? ` · ${h.receipt.row_count} ${t('rowsUnit')}` : ''}
                  </span>
                  <span className="flex gap-2">
                    <button
                      type="button"
                      className="text-ink underline-offset-2 hover:underline"
                      onClick={() => setJob(h)}
                    >
                      {t('view')}
                    </button>
                    {h.status === 'completed' && h.download_url ? (
                      <button
                        type="button"
                        className="text-ink underline-offset-2 hover:underline"
                        onClick={() => {
                          downloadWithAuth(h.download_url ?? '', {
                            filename: h.receipt?.filename,
                          }).catch(() => addToast(t('downloadFailed'), 'error'));
                        }}
                      >
                        {t('download')}
                      </button>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default DataDeliveryDialog;
