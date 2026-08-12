'use client';

import { useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import type {
  LineageGraph,
  ReplayMode,
  RunComparison,
  WorkflowRunDetail,
  WorkflowRunSummary,
  WorkflowSummary,
} from '@/lib/api/project';
import {
  formatCrs,
  isPartialRun,
  shortId,
} from '@/lib/workflow/recovery';
import { ComparePanel } from './compare-panel';
import { LineageList } from './lineage-list';
import { RecoveryActions } from './recovery-actions';

export interface RunInspectorProps {
  workflow: WorkflowSummary | null;
  run: WorkflowRunDetail | null;
  runs: WorkflowRunSummary[];
  loading: boolean;
  actionBusy: boolean;
  actionError: string | null;
  compare: RunComparison | null;
  compareError?: string | null;
  compareBusy?: boolean;
  comparePeerId: string;
  onComparePeerChange: (id: string) => void;
  onCompare: () => void;
  lineageByArtifact: Record<string, LineageGraph | 'loading' | 'empty' | 'error'>;
  onLoadLineage: (artifactId: string) => void;
  onBack: () => void;
  onReplay: (mode: ReplayMode) => void;
  onResume: () => void;
}

function kv(label: string, value: string) {
  return (
    <div className="flex min-w-0 justify-between gap-2 text-[11px]">
      <span className="shrink-0 text-[var(--theme-text-muted)]">{label}</span>
      <span className="min-w-0 break-all text-right font-mono text-[var(--theme-text-primary)]">{value}</span>
    </div>
  );
}

export function RunInspector({
  workflow,
  run,
  runs,
  loading,
  actionBusy,
  actionError,
  compare,
  compareError,
  compareBusy,
  comparePeerId,
  onComparePeerChange,
  onCompare,
  lineageByArtifact,
  onLoadLineage,
  onBack,
  onReplay,
  onResume,
}: RunInspectorProps) {
  const [showManifest, setShowManifest] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);

  if (loading && !run) return <LoadingState label="加载运行…" />;
  if (!run) {
    return <InlineNotice variant="error">未找到运行详情</InlineNotice>;
  }

  const manifest = run.run_manifest;
  const steps = manifest?.steps ?? [];
  const artifacts = manifest?.artifacts ?? [];
  const fingerprints = run.input_dataset_fingerprints ?? {};
  const fpEntries = Object.entries(fingerprints);
  const partial = isPartialRun(run);
  const perf = run.cost_perf_summary ?? {};

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <IconButton label="返回运行列表" icon={ArrowLeft} onClick={onBack} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-semibold text-[var(--theme-text-primary)]">
            {workflow?.name ?? '工作流'} · {shortId(run.id, 10)}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={run.status} />
            {partial && (
              <span className="text-[10px] text-[var(--theme-text-muted)]">
                部分完成 {run.completed_steps.length} 步
              </span>
            )}
          </div>
        </div>
      </div>

      {run.error_message && <InlineNotice variant="error">{run.error_message}</InlineNotice>}

      <section aria-labelledby="wf-identity-heading" className="space-y-1">
        <h3 id="wf-identity-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
          身份
        </h3>
        {kv('修订', run.workflow_revision_id ? shortId(run.workflow_revision_id, 12) : '—')}
        {kv('图指纹', manifest?.graph_fingerprint ? shortId(manifest.graph_fingerprint, 12) : '—')}
        {kv('运行指纹', run.run_fingerprint ? shortId(run.run_fingerprint, 12) : '—')}
      </section>

      <section aria-labelledby="wf-inputs-heading" className="space-y-1">
        <h3 id="wf-inputs-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
          输入数据集版本
        </h3>
        {fpEntries.length === 0 ? (
          <p className="text-[11px] text-[var(--theme-text-muted)]">未记录数据集指纹</p>
        ) : (
          <ul className="space-y-1">
            {fpEntries.map(([id, fp]) => (
              <li key={id} className="flex justify-between gap-2 font-mono text-[10px] text-[var(--theme-text-secondary)]">
                <span className="truncate">{shortId(id, 10)}</span>
                <span>{shortId(String(fp), 10)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="wf-steps-heading" className="space-y-1">
        <h3 id="wf-steps-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
          步骤 / 工具
        </h3>
        {steps.length === 0 ? (
          <p className="text-[11px] text-[var(--theme-text-muted)]">清单中无步骤</p>
        ) : (
          <ol className="space-y-1">
            {steps.map((s) => (
              <li
                key={s.step_id ?? s.tool_name}
                className="flex items-center justify-between gap-2 rounded border border-[var(--theme-border)] px-2 py-1"
              >
                <span className="min-w-0 truncate text-[11px] text-[var(--theme-text-primary)]">
                  {s.step_id} · {s.tool_name}
                  {s.tool_version ? ` @${s.tool_version}` : ''}
                </span>
                {s.status && <StatusBadge status={s.status} />}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="wf-arts-heading" className="space-y-1">
        <h3 id="wf-arts-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
          产物
        </h3>
        {artifacts.length === 0 ? (
          <p className="text-[11px] text-[var(--theme-text-muted)]">无产物</p>
        ) : (
          <ul className="space-y-1.5">
            {artifacts.map((art, idx) => {
              const aid = art.id;
              const missing = !aid;
              return (
                <li key={aid ?? `missing-${idx}`} className="rounded border border-[var(--theme-border)] px-2 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-[var(--theme-text-primary)]">
                      {art.producing_step || art.artifact_type || '产物'}
                    </span>
                    <span className="text-[10px] text-[var(--theme-text-muted)]">CRS {formatCrs(art.crs)}</span>
                  </div>
                  {missing ? (
                    <p className="text-[10px] text-red-600 dark:text-red-300">产物缺失</p>
                  ) : (
                    <>
                      <div className="font-mono text-[10px] text-[var(--theme-text-muted)]">
                        {shortId(aid, 12)}
                        {art.content_fingerprint ? ` · ${shortId(art.content_fingerprint, 8)}` : ''}
                      </div>
                      <button
                        type="button"
                        className="mt-1 text-[11px] text-[var(--theme-text-secondary)] underline-offset-2 hover:underline"
                        onClick={() => {
                          setOpenArtifact(aid);
                          onLoadLineage(aid);
                        }}
                      >
                        {openArtifact === aid ? '血统' : '查看血统'}
                      </button>
                      {openArtifact === aid && (
                        <div className="mt-1">
                          <LineageList
                            artifactId={aid}
                            artifactCrs={art.crs}
                            state={lineageByArtifact[aid]}
                            onLoad={onLoadLineage}
                          />
                        </div>
                      )}
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="wf-runtime-heading" className="space-y-1">
        <h3 id="wf-runtime-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
          运行指标
        </h3>
        {Object.keys(perf).length === 0 ? (
          <p className="text-[11px] text-[var(--theme-text-muted)]">无运行指标</p>
        ) : (
          <div className="space-y-0.5 text-[11px] text-[var(--theme-text-secondary)]">
            {perf.total_steps != null && <div>步骤 {String(perf.total_steps)}</div>}
            {perf.total_duration_seconds != null && (
              <div>耗时 {String(perf.total_duration_seconds)}s</div>
            )}
            {perf.elapsed_ms != null && <div>耗时 {String(perf.elapsed_ms)}ms</div>}
          </div>
        )}
      </section>

      <RecoveryActions
        run={run}
        busy={actionBusy}
        error={actionError}
        onReplay={onReplay}
        onResume={onResume}
      />

      <ComparePanel
        runs={runs}
        selectedRunId={run.id}
        peerId={comparePeerId}
        onPeerChange={onComparePeerChange}
        onCompare={onCompare}
        result={compare}
        busy={compareBusy}
        error={compareError}
      />

      <details
        className="rounded border border-[var(--theme-border)] px-2 py-1.5"
        open={showManifest}
        onToggle={(e) => setShowManifest((e.target as HTMLDetailsElement).open)}
      >
        <summary className="cursor-pointer text-[11px] text-[var(--theme-text-secondary)]">原始清单</summary>
        <pre className="mt-1 max-h-40 overflow-auto font-mono text-[10px] text-[var(--theme-text-muted)]">
          {JSON.stringify(manifest ?? run, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export default RunInspector;
