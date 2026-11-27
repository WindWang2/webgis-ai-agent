'use client';

/**
 * PackageExchangeDialog — 工程包导出/导入对话框（05 线 WP5）。
 *
 * 导出侧：检视闭包（零写入）→ 提交 durable job → 按 job 查回执 → 下载；
 * 导入侧：包上传（受控 ID）→ 预检（许可/冲突/分级）→ 提交（默认全新
 * 隔离项目，绝不覆盖现有）→ 按 job 查恢复报告。
 * 与 DataDeliveryDialog 同款骨架（焦点管理/轮询中止/代次守卫）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import {
  blockingConflicts,
  formatBytes,
  getExportReceiptByJob,
  getImportReceiptByJob,
  inspectImportPackage,
  inspectProjectExchange,
  pollJobTerminal,
  submitProjectExport,
  submitProjectImport,
  uploadProjectPackage,
  type ExchangeInspection,
  type ExportReceipt,
  type ImportInspection,
  type ImportReceipt,
  type JobView,
} from '@/lib/api/project-exchange';

export interface PackageExchangeDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  /** 会话上下文（导出产物登记会话面板 / 导入会话引用恢复）。 */
  sessionId?: string | null;
  /** 初始所在页签（工作台入口可直跳导入）。 */
  initialTab?: 'export' | 'import';
}

export function PackageExchangeDialog({
  open,
  onClose,
  projectId,
  sessionId,
  initialTab = 'export',
}: PackageExchangeDialogProps) {
  const t = useT('projectExchange');
  const addToast = useToastStore((s) => s.addToast);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open, containerRef: dialogRef, onEscape: onClose });

  const [tab, setTab] = useState<'export' | 'import'>(initialTab);
  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);

  // ── 导出侧状态 ─────────────────────────────────────────────────────
  const [inspection, setInspection] = useState<ExchangeInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportReceipt, setExportReceipt] = useState<ExportReceipt | null>(null);

  const runInspect = useCallback(async () => {
    setInspecting(true);
    setInspection(null);
    try {
      const result = await inspectProjectExchange(projectId);
      setInspection(result);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('inspectFailed')}: ${msg}`, 'error');
    } finally {
      setInspecting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const runExport = useCallback(async () => {
    setExporting(true);
    setExportReceipt(null);
    try {
      const result = await submitProjectExport(
        projectId, { kind: 'project' }, '', sessionId ?? undefined);
      addToast(t('exportSubmitted'), 'success');
      const job: JobView = await pollJobTerminal(result.job_id);
      if (job.status === 'completed') {
        const receipt = await getExportReceiptByJob(result.job_id);
        setExportReceipt(receipt);
        addToast(t('packageReady'), 'success');
      } else {
        addToast(`${t('jobFailed')}: ${job.error ?? job.message ?? job.status}`, 'error');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('exportFailed')}: ${msg}`, 'error');
    } finally {
      setExporting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, sessionId]);

  // ── 导入侧状态 ─────────────────────────────────────────────────────
  const [uploading, setUploading] = useState(false);
  const [uploadId, setUploadId] = useState('');
  const [precheck, setPrecheck] = useState<ImportInspection | null>(null);
  const [prechecking, setPrechecking] = useState(false);
  const [ack, setAck] = useState(false);
  const [targetName, setTargetName] = useState('');
  const [importing, setImporting] = useState(false);
  const [importReceipt, setImportReceipt] = useState<ImportReceipt | null>(null);

  const runUpload = useCallback(async (chosen: File) => {
    setUploading(true);
    setPrecheck(null);
    setUploadId('');
    setAck(false);
    setImportReceipt(null);
    try {
      const result = await uploadProjectPackage(chosen);
      setUploadId(result.package_upload_id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('importUploadFailed')}: ${msg}`, 'error');
    } finally {
      setUploading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!uploadId) return;
    let cancelled = false;
    setPrechecking(true);
    inspectImportPackage(uploadId)
      .then((result) => {
        if (!cancelled) {
          setPrecheck(result);
          setTargetName((prev) => prev || result.project_name || '');
        }
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : String(e);
        addToast(`${t('importInspectFailed')}: ${msg}`, 'error');
      })
      .finally(() => {
        if (!cancelled) setPrechecking(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadId]);

  const runImport = useCallback(async () => {
    if (!uploadId) return;
    setImporting(true);
    setImportReceipt(null);
    try {
      const conflicts = precheck?.conflicts;
      const acked = conflicts ? blockingConflicts(conflicts) : [];
      const result = await submitProjectImport(
        uploadId, targetName, acked, sessionId ?? undefined);
      addToast(t('importSubmitted'), 'success');
      const job: JobView = await pollJobTerminal(result.job_id);
      if (job.status === 'completed') {
        const receipt = await getImportReceiptByJob(result.job_id);
        setImportReceipt(receipt);
        addToast(t('importDone'), 'success');
      } else {
        addToast(`${t('jobFailed')}: ${job.error ?? job.message ?? job.status}`, 'error');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`${t('importFailed')}: ${msg}`, 'error');
    } finally {
      setImporting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadId, targetName, precheck, sessionId]);

  useEffect(() => {
    if (!open) {
      setInspection(null);
      setExportReceipt(null);

      setUploadId('');
      setPrecheck(null);
      setAck(false);
      setImportReceipt(null);
    }
  }, [open]);

  if (!open) return null;

  const requiredAck = precheck ? blockingConflicts(precheck.conflicts) : [];
  const hasConflicts = requiredAck.length > 0;

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="package-exchange-title"
      tabIndex={-1}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-surface-scrim p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col space-y-4 overflow-y-auto rounded-md border border-edge-subtle bg-surface-overlay p-5 shadow-overlay">
        <div className="flex items-start justify-between">
          <div>
            <h2 id="package-exchange-title" className="text-body font-semibold text-ink">
              {t('title')}
            </h2>
            <p className="text-meta text-ink-secondary">{t('description')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink-secondary transition-colors hover:bg-surface-hover"
          >
            {t('close')}
          </button>
        </div>

        {/* ── 页签 ─────────────────────────────────────────────── */}
        <div className="flex gap-2" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'export'}
            onClick={() => setTab('export')}
            className={`rounded-sm px-3 py-1.5 text-meta ${tab === 'export' ? 'bg-surface-sunken text-ink' : 'text-ink-secondary hover:bg-surface-hover'}`}
          >
            {t('tabExport')}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'import'}
            onClick={() => setTab('import')}
            className={`rounded-sm px-3 py-1.5 text-meta ${tab === 'import' ? 'bg-surface-sunken text-ink' : 'text-ink-secondary hover:bg-surface-hover'}`}
          >
            {t('tabImport')}
          </button>
        </div>

        {tab === 'export' ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void runInspect()}
                disabled={inspecting || exporting}
                className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink transition-colors hover:bg-surface-hover disabled:opacity-50"
              >
                {inspecting ? t('inspecting') : t('inspect')}
              </button>
              <button
                type="button"
                onClick={() => void runExport()}
                disabled={exporting || inspecting}
                className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink transition-colors hover:bg-surface-hover disabled:opacity-50"
              >
                {exporting ? t('exporting') : t('submitExport')}
              </button>
            </div>
            {inspection && (
              <div className="space-y-2 rounded-sm border border-edge-subtle p-3 text-meta">
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>
                    {t('reproLevel')}: <strong>{levelLabel(t, inspection.reproduction_level)}</strong>
                  </span>
                  <span>{t('countsDatasets')}: {inspection.totals.datasets}</span>
                  <span>{t('countsWorkflows')}: {inspection.totals.workflows} ({t('runnableWorkflows')} {inspection.runnable_workflows})</span>
                  <span>{t('countsArtifacts')}: {inspection.totals.artifacts}</span>
                  <span>{t('countsMaps')}: {inspection.totals.maps}</span>
                  <span>{t('estimatedSize')}: {formatBytes(inspection.estimated_payload_bytes)}</span>
                </div>
                <div>
                  <p className="text-ink-secondary">{t('disclosureTitle')}</p>
                  {inspection.disclosure.length === 0 ? (
                    <p className="text-meta">{t('disclosureEmpty')}</p>
                  ) : (
                    <ul className="list-disc pl-5">
                      {inspection.disclosure.slice(0, 20).map((item, i) => (
                        <li key={`${item.kind}-${item.name}-${i}`}>
                          {item.name} — {item.reason}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
            {exportReceipt && (
              <div className="space-y-1 rounded-sm border border-edge-subtle p-3 text-meta">
                <p className="font-semibold text-ink">{t('packageReady')}</p>
                <p>{t('shaLabel')}: <code className="break-all">{exportReceipt.sha256}</code></p>
                <p>{formatBytes(exportReceipt.bytes)} · {exportReceipt.filename}</p>
                <a
                  href={exportReceipt.download_path}
                  className="inline-block rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink hover:bg-surface-hover"
                >
                  {t('downloadPackage')}
                </a>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-meta text-ink-secondary">
              {t('noOverwrite')} · {t('noAutoRecompute')}
            </p>
            <input
              type="file"
              accept=".zip,.wgproject.zip"
              aria-label={t('importUpload')}
              onChange={(e) => {
                const chosen = e.target.files?.[0] ?? null;

                if (chosen) void runUpload(chosen);
              }}
              className="w-full rounded-sm border border-edge-subtle p-2 text-meta"
            />
            {uploading && <p className="text-meta">{t('importUploading')}</p>}
            {prechecking && <p className="text-meta">{t('importInspecting')}</p>}
            {precheck && (
              <div className="space-y-2 rounded-sm border border-edge-subtle p-3 text-meta">
                <p>{t('packageId')}: <code>{precheck.package_id}</code></p>
                <p>
                  {t('originProject')}: {precheck.origin.project_name || precheck.project_name} · {t('reproLevel')}: <strong>{levelLabel(t, precheck.reproduction.level)}</strong>
                </p>
                <p>{t('countsDatasets')}: {precheck.totals.datasets} · {t('countsWorkflows')}: {precheck.totals.workflows} · {t('countsMaps')}: {precheck.totals.maps}</p>
                <div>
                  <p className="text-ink-secondary">{t('licensesTitle')}</p>
                  {precheck.licenses.length === 0 ? (
                    <p>{t('licenseUnknown')}</p>
                  ) : (
                    <ul className="list-disc pl-5">
                      {precheck.licenses.slice(0, 20).map((lic) => (
                        <li key={lic.logical_id}>
                          {lic.name}: {lic.spdx ?? t('licenseUnknown')}
                          {lic.distributable === false ? ` · ${t('licenseNotDistributable')}` : ''}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="text-ink-secondary">{t('conflictsTitle')}</p>
                  {hasConflicts ? (
                    <ul className="list-disc pl-5">
                      {precheck.conflicts.declared_missing.slice(0, 10).map((p) => (
                        <li key={`dm-${p}`}>{t('declaredMissing')}: {p}</li>
                      ))}
                      {precheck.conflicts.duplicate_logical_ids.slice(0, 10).map((p) => (
                        <li key={`dup-${p}`}>{t('duplicateIds')}: {p}</li>
                      ))}
                      {precheck.conflicts.extra_entries.slice(0, 10).map((p) => (
                        <li key={`ee-${p}`}>{t('extraEntries')}: {p}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>{t('noConflicts')}</p>
                  )}
                </div>
                {hasConflicts && (
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={ack}
                      onChange={(e) => setAck(e.target.checked)}
                    />
                    <span>{t('ackConflicts')}</span>
                  </label>
                )}
              </div>
            )}
            {uploadId && !prechecking && (
              <div className="space-y-2">
                <input
                  type="text"
                  value={targetName}
                  onChange={(e) => setTargetName(e.target.value)}
                  placeholder={t('targetName')}
                  aria-label={t('targetName')}
                  className="w-full rounded-sm border border-edge-subtle p-2 text-meta"
                />
                <button
                  type="button"
                  onClick={() => void runImport()}
                  disabled={importing || (hasConflicts && !ack)}
                  className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta text-ink transition-colors hover:bg-surface-hover disabled:opacity-50"
                >
                  {importing ? t('importing') : t('submitImport')}
                </button>
              </div>
            )}
            {importReceipt && (
              <div className="space-y-1 rounded-sm border border-edge-subtle p-3 text-meta">
                <p className="font-semibold text-ink">{t('importDone')}</p>
                <p>{t('newProjectId')}: <code>{importReceipt.project_id}</code></p>
                <p>
                  {t('verification')}: {String((importReceipt.verification as { verdict?: string }).verdict ?? 'not_evaluated')}
                </p>
                {importReceipt.skipped.length > 0 && (
                  <p>{t('skipped')}: {importReceipt.skipped.length}</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function levelLabel(
  t: (key: string) => string,
  level: string,
): string {
  if (level === 'full_offline_recompute') return t('levelFull');
  if (level === 'view_only') return t('levelViewOnly');
  return t('levelPartial');
}
