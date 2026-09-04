'use client';

/**
 * Map Product version workspace (ADR-0092 A6 + ADR-0099 lifecycle V2):
 * version timeline + pairwise five-dimension diff inspector + lifecycle
 * operations (open / fork / restore-style / merge).
 *
 * Truth sources: the version ledger (read-only list/detail/diff/open) and
 * the lifecycle APIs. Style-only changes show “无需分析重算” and offer NO
 * rerun; restore style-only applies the version snapshot's presentation to
 * the live session (never triggers analysis). Merge is constrained —
 * dimension conflicts are refused by the backend and surfaced here.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { GitCompare, GitBranch, RotateCcw, RefreshCw, GitMerge, FolderOpen } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import {
  diffMapProductVersions,
  forkMapProductVersion,
  listMapProductVersions,
  mergeMapProductVersions,
  openMapProductVersion,
  restoreMapProductVersion,
  rerunWorkflowRunFromStep,
  type MapProductLineageKind,
  type MapProductVersionDiff,
  type MapProductVersionOpen,
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

const LINEAGE_LABEL: Record<NonNullable<MapProductLineageKind>, string> = {
  linear: '',
  fork: '分叉',
  restore: '恢复',
  merge: '合并',
  rerun: '重跑',
  auto: '自动',
};

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
  /** 活会话（style-only restore 的目标）；缺席时恢复入口降级隐藏。 */
  sessionId?: string | null;
  /** Toast/error channel from the host (kept dumb here). */
  onRerunStarted?: (runId: string | null) => void;
  onRerunError?: (message: string) => void;
}

export function MapProductVersionsPanel({
  projectId,
  sessionId,
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
  const [openVersion, setOpenVersion] = useState<number | null>(null);
  const [openDetail, setOpenDetail] = useState<MapProductVersionOpen | null>(null);
  const [lifecycleBusy, setLifecycleBusy] = useState<string | null>(null);
  const [lifecycleNotice, setLifecycleNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    listMapProductVersions(projectId, { limit: 50, signal: ctrl.signal })
      .then((page) => {
        if (ctrl.signal.aborted) return;
        const rows = page.items;
        setVersions(rows);
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

  useEffect(() => {
    setVersions([]);
    setDiff(null);
    setFromNo(null);
    setToNo(null);
    reload();
  }, [projectId, reload]);

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

  const handleOpen = async (versionNo: number) => {
    if (openVersion === versionNo) {
      setOpenVersion(null);
      setOpenDetail(null);
      return;
    }
    setOpenVersion(versionNo);
    setOpenDetail(null);
    try {
      const detail = await openMapProductVersion(projectId, versionNo);
      setOpenDetail(detail);
    } catch (e) {
      setLifecycleNotice(e instanceof Error ? e.message : '打开版本失败');
      setOpenVersion(null);
    }
  };

  const handleFork = async (versionNo: number) => {
    setLifecycleBusy(`fork-${versionNo}`);
    setLifecycleNotice(null);
    try {
      await forkMapProductVersion(projectId, versionNo);
      setLifecycleNotice(`已从 V${versionNo} 创建分叉（新版本行已记录谱系）`);
      reload();
    } catch (e) {
      setLifecycleNotice(e instanceof Error ? e.message : '分叉失败');
    } finally {
      setLifecycleBusy(null);
    }
  };

  const handleRestoreStyle = async (versionNo: number) => {
    if (!sessionId) return;
    setLifecycleBusy(`restore-${versionNo}`);
    setLifecycleNotice(null);
    try {
      const result = await restoreMapProductVersion(projectId, versionNo, sessionId, 'style_only');
      const proof = result.style_only_proof;
      setLifecycleNotice(
        `已恢复 V${versionNo} 的样式态（新版本 V${result.restored_version_no}）` +
          (proof?.analysis_executed === false ? ' — 未触发分析重算' : ''),
      );
      reload();
    } catch (e) {
      setLifecycleNotice(e instanceof Error ? e.message : '恢复失败');
    } finally {
      setLifecycleBusy(null);
    }
  };

  const handleMerge = async () => {
    if (fromNo == null || toNo == null) return;
    setLifecycleBusy('merge');
    setLifecycleNotice(null);
    try {
      const merged = await mergeMapProductVersions(projectId, fromNo, toNo);
      setLifecycleNotice(`已合并 V${fromNo} + V${toNo} → V${merged.version_no}`);
      reload();
    } catch (e) {
      setLifecycleNotice(e instanceof Error ? e.message : '合并被拒绝');
    } finally {
      setLifecycleBusy(null);
    }
  };

  const styleOnlyRestorable = useCallback(
    (versionNo: number) => {
      if (!sessionId) return false;
      const v = versions.find((x) => x.version_no === versionNo);
      return Boolean(v && (v as MapProductVersionSummary & { snapshot_available?: boolean }).snapshot_available);
    },
    [sessionId, versions],
  );

  return (
    <section aria-labelledby="mp-versions-heading" className="space-y-2">
      <h3
        id="mp-versions-heading"
        className="text-micro font-semibold uppercase tracking-wide text-ink-muted"
      >
        产品版本
      </h3>
      {lifecycleNotice ? (
        <InlineNotice variant="info" data-testid="mp-lifecycle-notice">
          {lifecycleNotice}
        </InlineNotice>
      ) : null}
      {loading ? (
        <LoadingState label="加载产品版本…" />
      ) : error ? (
        <InlineNotice variant="error">{error}</InlineNotice>
      ) : versions.length === 0 ? (
        <EmptyState icon={GitCompare} title="暂无产品版本" />
      ) : (
        <>
          <ul className="space-y-1">
            {versions.map((v) => {
              const lineage = (v as MapProductVersionSummary & { lineage_kind?: MapProductLineageKind }).lineage_kind;
              const restorable = styleOnlyRestorable(v.version_no);
              return (
                <li
                  key={v.version_no}
                  className="rounded-md border border-edge-subtle bg-surface-raised px-2.5 py-1.5"
                  data-version={v.version_no}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0">
                      <span className="block text-micro font-mono text-ink">
                        V{v.version_no}
                        {v.version_no === versions[0].version_no && (
                          <span className="ml-1.5 rounded-sm bg-surface-sunken px-1 text-micro text-ink-secondary">
                            当前
                          </span>
                        )}
                        {lineage ? (
                          <span
                            className="ml-1.5 rounded-sm bg-[color:var(--agent-accent)]/15 px-1 text-micro text-ink-secondary"
                            data-lineage={lineage}
                          >
                            {LINEAGE_LABEL[lineage]}
                          </span>
                        ) : null}
                      </span>
                      <span className="block truncate text-micro text-ink-muted">
                        {formatTime(v.created_at)}
                        {v.recipe_id ? ` · ${v.recipe_id}` : ''}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => void handleOpen(v.version_no)}
                        aria-expanded={openVersion === v.version_no}
                        aria-label={`检视版本 V${v.version_no}`}
                        className="rounded p-1 text-ink-secondary hover:bg-surface-sunken hover:text-ink"
                      >
                        <FolderOpen className="h-3 w-3" aria-hidden />
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleFork(v.version_no)}
                        disabled={lifecycleBusy === `fork-${v.version_no}`}
                        aria-label={`从 V${v.version_no} 分叉`}
                        className="rounded p-1 text-ink-secondary hover:bg-surface-sunken hover:text-ink disabled:opacity-50"
                      >
                        <GitBranch className="h-3 w-3" aria-hidden />
                      </button>
                      {restorable ? (
                        <button
                          type="button"
                          onClick={() => void handleRestoreStyle(v.version_no)}
                          disabled={lifecycleBusy === `restore-${v.version_no}`}
                          aria-label={`恢复 V${v.version_no} 的样式态`}
                          className="rounded p-1 text-ink-secondary hover:bg-surface-sunken hover:text-ink disabled:opacity-50"
                        >
                          <RotateCcw className="h-3 w-3" aria-hidden />
                        </button>
                      ) : null}
                    </span>
                  </div>
                  {openVersion === v.version_no ? (
                    openDetail ? (
                      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 border-t border-edge-subtle pt-1 text-micro text-ink-secondary" data-testid="mp-version-open">
                        <dt className="font-medium">指纹</dt>
                        <dd className="truncate font-mono" title={openDetail.product_fingerprint}>
                          {shortFp(openDetail.product_fingerprint, 16)}
                        </dd>
                        <dt className="font-medium">快照</dt>
                        <dd>{openDetail.snapshot_available ? '在场（可恢复样式态）' : '缺席（仅可对比）'}</dd>
                        <dt className="font-medium">来源</dt>
                        <dd>
                          {openDetail.workflow_run_id ? `运行 ${shortFp(openDetail.workflow_run_id, 10)}` : '无绑定运行'}
                        </dd>
                        <dt className="font-medium">谱系</dt>
                        <dd>
                          {LINEAGE_LABEL[openDetail.lineage_kind ?? 'linear'] || '线性'}
                          {openDetail.parent_version_no ? ` ← V${openDetail.parent_version_no}` : ''}
                        </dd>
                        <dt className="font-medium">证明</dt>
                        <dd>
                          输入 {Object.keys(openDetail.provenance.input_dataset_fingerprints).length} 项 ·
                          计划 {openDetail.provenance.plan_steps} 步 ·
                          产物 {openDetail.provenance.artifact_count} 件
                        </dd>
                        {openDetail.restore_modes.map((m) => (
                          <dt key={m.mode} className="font-medium">
                            {m.mode === 'style_only' ? '样式恢复' : '完整恢复'}
                            <dd className={m.available ? '' : 'text-ink-disabled'}>
                              {m.available ? '可用' : `不可用 — ${m.note}`}
                            </dd>
                          </dt>
                        ))}
                      </dl>
                    ) : (
                      <div className="mt-1 border-t border-edge-subtle pt-1 text-micro text-ink-muted">检视中…</div>
                    )
                  ) : null}
                </li>
              );
            })}
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
                  {/* ADR-0099 constrained merge：样式-only × 分析-only 可合；
                      同维冲突由后端拒绝（409），文案如实呈现。 */}
                  <button
                    type="button"
                    onClick={() => void handleMerge()}
                    disabled={lifecycleBusy === 'merge' || fromNo === toNo}
                    className="flex w-full items-center justify-center gap-1.5 rounded-sm border border-edge-subtle py-1 text-micro text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
                  >
                    <GitMerge className="h-3 w-3" aria-hidden />
                    合并两版本（样式侧 × 分析侧）
                  </button>
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
