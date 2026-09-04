'use client';

/**
 * Map Product version workspace (ADR-0092 A6): version timeline + pairwise
 * five-dimension diff inspector.
 *
 * Truth sources: the version ledger (read-only list/detail/diff) and the
 * existing rerun_from_step API for recomputation. Style-only changes show
 * "无需分析重算" and offer NO rerun (the analysis did not change); data/
 * algorithm/parameter changes surface 重算 with the earliest changed step
 * as the rerun seed.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { GitCompare, RefreshCw } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import {
  diffMapProductVersions,
  listMapProductVersions,
  rerunWorkflowRunFromStep,
  type MapProductVersionDiff,
  type MapProductVersionSummary,
} from '@/lib/api/map-product';

const DIMENSIONS: Array<{
  key: keyof Pick<MapProductVersionDiff, 'data_changed' | 'algorithm_changed' | 'parameter_changed' | 'style_changed' | 'output_changed'>;
  label: string;
}> = [
  { key: 'data_changed', label: '数据' },
  { key: 'algorithm_changed', label: '算法' },
  { key: 'parameter_changed', label: '参数' },
  { key: 'style_changed', label: '样式' },
  { key: 'output_changed', label: '输出' },
];

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function shortFp(fp?: string | null, n = 10): string {
  if (!fp) return '—';
  return fp.length > n ? `${fp.slice(0, n)}…` : fp;
}

export interface MapProductVersionsPanelProps {
  projectId: string;
  /** Toast/error channel from the host (kept dumb here). */
  onRerunStarted?: (runId: string | null) => void;
  onRerunError?: (message: string) => void;
}

export function MapProductVersionsPanel({
  projectId,
  onRerunStarted,
  onRerunError,
}: MapProductVersionsPanelProps) {
  const [versions, setVersions] = useState<MapProductVersionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fromNo, setFromNo] = useState<number | null>(null);
  const [toNo, setToNo] = useState<number | null>(null);
  const [diff, setDiff] = useState<MapProductVersionDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [rerunBusy, setRerunBusy] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    setVersions([]);
    setDiff(null);
    setFromNo(null);
    setToNo(null);
    listMapProductVersions(projectId, { limit: 50, signal: ctrl.signal })
      .then((page) => {
        const rows = page.items;
        setVersions(rows);
        // Default selection: two most recent versions (newest as "to").
        if (rows.length >= 2) {
          setFromNo(rows[1].version_no);
          setToNo(rows[0].version_no);
        } else if (rows.length === 1) {
          setToNo(rows[0].version_no);
        }
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : '加载产品版本失败');
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [projectId]);

  const loadDiff = useCallback(
    (from: number | null, to: number | null) => {
      if (from == null || to == null || from === to) {
        setDiff(null);
        return;
      }
      const ctrl = new AbortController();
      setDiffLoading(true);
      setDiffError(null);
      diffMapProductVersions(projectId, from, to, { signal: ctrl.signal })
        .then(setDiff)
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          setDiff(null);
          setDiffError(e instanceof Error ? e.message : '加载版本对比失败');
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setDiffLoading(false);
        });
    },
    [projectId],
  );

  useEffect(() => {
    loadDiff(fromNo, toNo);
  }, [fromNo, toNo, loadDiff]);

  const rerunStep = useMemo(() => {
    if (!diff?.analysis_recomputation_expected) return null;
    const changed = [
      ...diff.details.algorithm_steps.map((s) => s.step_id),
      ...diff.details.parameter_steps.map((s) => s.step_id),
    ];
    return changed.length > 0 ? changed[0] : null;
  }, [diff]);

  const handleRerun = async () => {
    if (!diff || !rerunStep) return;
    const runId = diff.details.workflow_runs.to || diff.details.workflow_runs.from;
    if (!runId) {
      onRerunError?.('该版本未关联工作流运行，无法重跑');
      return;
    }
    setRerunBusy(true);
    try {
      await rerunWorkflowRunFromStep(projectId, runId, rerunStep);
      onRerunStarted?.(runId);
    } catch (e) {
      onRerunError?.(e instanceof Error ? e.message : '重跑失败');
    } finally {
      setRerunBusy(false);
    }
  };

  return (
    <section aria-labelledby="mp-versions-heading" className="space-y-2">
      <h3
        id="mp-versions-heading"
        className="text-micro font-semibold uppercase tracking-wide text-ink-muted"
      >
        产品版本
      </h3>
      {loading ? (
        <LoadingState label="加载产品版本…" />
      ) : error ? (
        <InlineNotice variant="error">{error}</InlineNotice>
      ) : versions.length === 0 ? (
        <EmptyState icon={GitCompare} title="暂无产品版本" />
      ) : (
        <>
          <ul className="space-y-1">
            {versions.map((v) => (
              <li
                key={v.version_no}
                className="flex items-center justify-between gap-2 rounded-md border border-edge-subtle bg-surface-raised px-2.5 py-1.5"
              >
                <span className="min-w-0">
                  <span className="block text-micro font-mono text-ink">
                    V{v.version_no}
                    {v.version_no === versions[0].version_no && (
                      <span className="ml-1.5 rounded-sm bg-surface-sunken px-1 text-micro text-ink-secondary">
                        当前
                      </span>
                    )}
                  </span>
                  <span className="block truncate text-micro text-ink-muted">
                    {formatTime(v.created_at)}
                    {v.recipe_id ? ` · ${v.recipe_id}` : ''}
                  </span>
                </span>
                <span className="font-mono text-micro text-ink-muted" title={v.product_fingerprint}>
                  {shortFp(v.product_fingerprint)}
                </span>
              </li>
            ))}
          </ul>

          {versions.length >= 2 && (
            <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-panel p-2">
              <div className="flex items-center gap-1.5">
                <label className="sr-only" htmlFor="mp-diff-from">
                  对比起点版本
                </label>
                <select
                  id="mp-diff-from"
                  value={fromNo ?? ''}
                  onChange={(e) => setFromNo(Number(e.target.value))}
                  className="min-w-0 flex-1 rounded border px-1.5 py-1 text-micro"
                  style={{ backgroundColor: 'var(--theme-bg-input)', borderColor: 'var(--theme-border)' }}
                >
                  {versions.map((v) => (
                    <option key={v.version_no} value={v.version_no}>
                      V{v.version_no}
                    </option>
                  ))}
                </select>
                <span className="text-micro text-ink-muted">→</span>
                <label className="sr-only" htmlFor="mp-diff-to">
                  对比终点版本
                </label>
                <select
                  id="mp-diff-to"
                  value={toNo ?? ''}
                  onChange={(e) => setToNo(Number(e.target.value))}
                  className="min-w-0 flex-1 rounded border px-1.5 py-1 text-micro"
                  style={{ backgroundColor: 'var(--theme-bg-input)', borderColor: 'var(--theme-border)' }}
                >
                  {versions.map((v) => (
                    <option key={v.version_no} value={v.version_no}>
                      V{v.version_no}
                    </option>
                  ))}
                </select>
              </div>

              {diffLoading ? (
                <LoadingState label="对比版本…" />
              ) : diffError ? (
                <InlineNotice variant="error">{diffError}</InlineNotice>
              ) : diff ? (
                <div className="space-y-2">
                  <ul className="grid grid-cols-5 gap-1" aria-label="五维差异">
                    {DIMENSIONS.map(({ key, label }) => {
                      const changed = diff[key];
                      return (
                        <li
                          key={key}
                          className={`rounded-sm px-1.5 py-1 text-center text-micro ${
                            changed
                              ? 'bg-[color:var(--agent-accent)]/15 text-ink'
                              : 'bg-surface-sunken text-ink-muted'
                          }`}
                        >
                          <span className="block font-semibold">{label}</span>
                          <span className="block">{changed ? '已变更' : '未变'}</span>
                        </li>
                      );
                    })}
                  </ul>
                  <div
                    role="status"
                    className={`rounded-sm px-2 py-1 text-micro ${
                      diff.analysis_recomputation_expected
                        ? 'bg-status-warning/15 text-ink'
                        : 'bg-surface-sunken text-ink-secondary'
                    }`}
                  >
                    {diff.analysis_recomputation_expected
                      ? '分析重算：需要（数据/算法/参数变更）'
                      : '分析重算：不需要（仅样式或无变化）'}
                  </div>
                  {diff.analysis_recomputation_expected && rerunStep && (
                    <button
                      type="button"
                      onClick={() => {
                        void handleRerun();
                      }}
                      disabled={rerunBusy}
                      className="flex w-full items-center justify-center gap-1.5 rounded-sm border border-edge-subtle py-1 text-micro text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
                    >
                      <RefreshCw className={`h-3 w-3 ${rerunBusy ? 'animate-spin' : ''}`} aria-hidden />
                      从分析步骤重跑（{rerunStep}）
                    </button>
                  )}
                  <details className="text-micro text-ink-secondary">
                    <summary className="cursor-pointer select-none">差异明细</summary>
                    <div className="mt-1 space-y-1.5 pl-1">
                      {diff.details.input_dataset_fingerprints.changed_keys.length > 0 && (
                        <div>
                          <span className="font-semibold">数据指纹变更键：</span>
                          <span className="font-mono">
                            {diff.details.input_dataset_fingerprints.changed_keys.join(', ')}
                          </span>
                        </div>
                      )}
                      {diff.details.algorithm_steps.map((s) => (
                        <div key={`algo-${s.step_id}`} className="font-mono">
                          算法[{s.step_id}]：{s.from ?? '∅'} → {s.to ?? '∅'}
                        </div>
                      ))}
                      {diff.details.parameter_steps.map((s) => (
                        <div key={`param-${s.step_id}`} className="font-mono">
                          参数[{s.step_id}]：{JSON.stringify(s.from)} → {JSON.stringify(s.to)}
                        </div>
                      ))}
                      <div className="font-mono">
                        MapSpec：{shortFp(diff.details.mapspec_fingerprint.from, 12)} →{' '}
                        {shortFp(diff.details.mapspec_fingerprint.to, 12)}
                      </div>
                      <div>
                        产物：+{diff.details.artifacts.added.length} / -
                        {diff.details.artifacts.removed.length}（{diff.details.artifacts.unchanged_count} 不变）
                      </div>
                    </div>
                  </details>
                </div>
              ) : (
                <p className="text-micro text-ink-muted">选择两个不同版本进行对比</p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default MapProductVersionsPanel;
