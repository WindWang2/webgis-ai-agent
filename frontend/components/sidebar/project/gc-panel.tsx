'use client';


/**
 * DataGcPanel — 数据回收（ADR-0143 P6）。
 *
 * 后端契约（勘察报告 §2.7）：data-usage（只读配额）/ data-gc/plan（dry-run）/
 * data-gc/execute（confirm:true，false 或缺省 400）。全部同步、无 job 句柄，
 * 执行进度以 busy 态呈现。后端无 staging/回滚端点——宽限观察以
 * `grace_hours` / `upcoming_candidates` 字段如实展示（协调点见 PR）。
 */

import { useEffect, useState } from 'react';
import { Trash2, FileSearch } from 'lucide-react';

import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import { useDataGc } from '@/lib/hooks/use-project-assets';
import { formatBytes } from './format';
import { useT } from '@/lib/i18n/useT';

export interface DataGcPanelProps {
  projectId: string;
  authed: boolean;
  /** 回收候选产物 → 产物中心定位（P7 交叉导航）。 */
  onLocateArtifact?: (artifactId: string) => void;
}

export function DataGcPanel({ projectId, authed, onLocateArtifact }: DataGcPanelProps) {
  const t = useT('project');
  const gc = useDataGc(projectId);
  const addToast = useToastStore((s) => s.addToast);
  const [planOpen, setPlanOpen] = useState(false);

  useEffect(() => {
    void gc.loadUsage();
    // 仅随项目切换加载一次；写操作后由 hook 内部强制刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const usage = gc.usage;
  const quotaRatio =
    usage && usage.limits.max_bytes > 0
      ? Math.min(1, usage.usage.bytes / usage.limits.max_bytes)
      : null;

  const handleExecute = async () => {
    const result = await gc.execute();
    if (result) {
      addToast(
        `回收完成：释放 ${formatBytes(result.retention.bytes_freed)}（修订 ${result.retention.deleted_revision_count} / 对象 ${result.retention.deleted_blob_count}）`,
        'success',
      );
    }
  };

  return (
    <section aria-labelledby="gc-heading" className="space-y-2">
      <h3 id="gc-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
        <Trash2 size={14} className="text-ink-muted" aria-hidden /> {t('kfwkdo2')}</h3>

      {!authed && <InlineNotice variant="warning">{t('k1pc3nil')}</InlineNotice>}
      {gc.error && <InlineNotice variant="error">{gc.error}</InlineNotice>}

      {gc.usageLoading && !usage ? (
        <LoadingState label={t('kb2641m')} />
      ) : usage ? (
        <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2 text-micro">
          <div className="flex items-center justify-between">
            <span className="text-ink-secondary">{t('keabldj')}</span>
            <span className="font-mono text-ink">
              {formatBytes(usage.usage.bytes)} / {formatBytes(usage.limits.max_bytes)}
            </span>
          </div>
          {quotaRatio != null && (
            <div
              role="progressbar"
              aria-valuenow={Math.round(quotaRatio * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={t('kb9ew54')}
              className="h-1.5 overflow-hidden rounded-full bg-surface-sunken"
            >
              <div
                className={`h-full ${quotaRatio > 0.9 ? 'bg-status-critical' : quotaRatio > 0.7 ? 'bg-status-warning' : 'bg-status-success'}`}
                style={{ width: `${Math.round(quotaRatio * 100)}%` }}
              />
            </div>
          )}
          <div className="flex items-center justify-between text-ink-secondary">
            <span>
              {t('p0P1P2P32', { p0: usage.usage.artifact_count, p1: usage.limits.max_artifact_count, p2: ' ', p3: formatBytes(usage.usage.revision_bytes) })}</span>
            <StatusBadge status={usage.quota.allowed ? 'completed' : 'failed'} label={usage.quota.allowed ? '配额内' : '超限'} />
          </div>
          <p className="text-ink-muted">
            {t('p0P1P2', { p0: Number(usage.retention.policy?.grace_hours ?? 0) || '—', p1: usage.retention.upcoming_candidates, p2: usage.retention.upcoming_candidate_blobs })}</p>
        </div>
      ) : (
        <EmptyState icon={Trash2} title={t('k14yv19z')} />
      )}

      <button
        type="button"
        onClick={() => {
          setPlanOpen(true);
          void gc.runPlan();
        }}
        disabled={!authed || gc.planLoading}
        className="flex w-full items-center justify-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-raised py-1.5 text-meta font-medium text-ink hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60"
      >
        <FileSearch size={13} aria-hidden />
        {gc.planLoading ? '生成回收计划…' : planOpen ? '重新生成回收计划（dry-run）' : '生成回收计划（dry-run）'}
      </button>

      {gc.plan && planOpen && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2 text-micro">
          {gc.plan.retention.disabled ? (
            <InlineNotice variant="info">{t('k16qpeaj')}</InlineNotice>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-1.5">
                <div className="rounded-sm bg-surface-sunken px-1.5 py-1">
                  <div className="text-ink-muted">{t('kcv1yqa')}</div>
                  <div className="font-mono text-ink">{gc.plan.retention.candidate_revision_count}</div>
                </div>
                <div className="rounded-sm bg-surface-sunken px-1.5 py-1">
                  <div className="text-ink-muted">{t('kcv45do')}</div>
                  <div className="font-mono text-ink">{gc.plan.retention.candidate_blob_count}</div>
                </div>
                <div className="rounded-sm bg-surface-sunken px-1.5 py-1">
                  <div className="text-ink-muted">{t('knl9y0s')}</div>
                  <div className="font-mono text-ink">{formatBytes(gc.plan.retention.candidate_blob_bytes)}</div>
                </div>
              </div>

              {gc.plan.promotion_store_gc.grace_hours != null && (
                <p className="text-ink-muted">
                  {t('p0P1P22', { p0: gc.plan.promotion_store_gc.grace_hours, p1: gc.plan.promotion_store_gc.deletable_count, p2: formatBytes(gc.plan.promotion_store_gc.deletable_bytes) })}</p>
              )}
              {gc.plan.retention.protection_scan_truncated && (
                <InlineNotice variant="warning">{t('krth4pt')}</InlineNotice>
              )}

              {gc.plan.retention.candidate_revisions.length > 0 && (
                <details open>
                  <summary className="cursor-pointer select-none text-ink-secondary">
                    {t('k1dbinqi', { p0: gc.plan.retention.candidate_revisions.length })}</summary>
                  <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto">
                    {gc.plan.retention.candidate_revisions.map((r) => (
                      <li key={`${r.artifact_id}-r${r.revision_no}`} className="flex justify-between gap-2 text-ink-secondary">
                        <span className="truncate font-mono">
                          {r.artifact_id.slice(0, 12)} · r{r.revision_no} · {r.age_days}{t('khm1')}</span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          <span className="font-mono">{formatBytes(r.byte_size)}</span>
                          {onLocateArtifact && (
                            <button
                              type="button"
                              onClick={() => onLocateArtifact(r.artifact_id)}
                              className="text-status-accent underline-offset-2 hover:underline"
                              aria-label={`在产物中心定位 ${r.artifact_id}`}
                            >
                              {t('kh0rr')}</button>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {Object.keys(gc.plan.retention.protected_counts).length > 0 && (
                <p className="text-ink-muted">
                  {t('p0P15', { p0: ' ', p1: Object.entries(gc.plan.retention.protected_counts)
                    .map(([k, v]) => `${k}=${v}`)
                    .join('、') })}</p>
              )}

              <div className="rounded-sm border border-status-critical-border bg-status-critical-soft px-2 py-1.5">
                <p className="font-medium text-status-critical">{t('kdeiudf')}</p>
                <p className="mt-0.5 text-ink-secondary">
                  {t('staging')}</p>
                <ConfirmAction
                  label={t('kfm26nb')}
                  confirmLabel={t('k9uxigz')}
                  onConfirm={() => {
                    void handleExecute();
                  }}
                  disabled={!authed || gc.executing}
                  className="mt-1 border border-status-critical-border bg-status-critical-soft text-status-critical hover:brightness-110"
                />
                {gc.executing && <LoadingState label={t('kubzomc')} />}
              </div>
            </>
          )}
        </div>
      )}

      {gc.executed && (
        <div className="space-y-1 rounded-md border border-status-success-border bg-status-success-soft px-panel py-2 text-micro">
          <p className="font-medium text-ink">{t('kdp1yhl')}</p>
          <p className="text-ink-secondary">
            {t('p0P1P2P3P4', { p0: formatBytes(gc.executed.retention.bytes_freed), p1: ' ', p2: gc.executed.retention.deleted_revision_count, p3: gc.executed.retention.deleted_blob_count, p4: gc.executed.orphan_revisions.deleted_count })}</p>
          {gc.executed.skipped_protected.length > 0 && (
            <details>
              <summary className="cursor-pointer select-none text-ink-secondary">
                {t('k5vblvo', { p0: gc.executed.skipped_protected.length })}</summary>
              <ul className="mt-1 max-h-24 space-y-0.5 overflow-auto text-ink-secondary">
                {gc.executed.skipped_protected.slice(0, 20).map((s) => (
                  <li key={s.key} className="truncate font-mono">
                    {s.key}: {s.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
