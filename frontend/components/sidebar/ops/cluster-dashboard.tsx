'use client';

/**
 * 集群仪表盘（P3 核心屏，ADR-0142 D1/D3/D7）。
 *
 * 组成：在线率/队列/在飞/利用率/溢出 概览卡 → Workers 表 → 五类观测指标
 * 时序 → Stuck runs 干预 → 活跃 runs。admin 通道（metrics/workers/stuck）
 * 各自呈现权限/降级诚实态；指标采样在每次成功轮询后追加入客户端观测窗。
 */
import { useEffect, useMemo, useState } from 'react';
import { Users, Layers3 } from 'lucide-react';
import { useClusterMetrics } from '@/lib/hooks/use-cluster-metrics';
import { useClusterWorkers } from '@/lib/hooks/use-cluster-workers';
import { useStuckRuns, stuckDurationS } from '@/lib/hooks/use-cluster-stuck-runs';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';
import {
  getClusterRun,
  listClusterRuns,
  type ClusterMetrics,
  type ClusterRunRow,
} from '@/lib/api/geocompute';
import { appendSample, toMetricsSample, type MetricsSample } from './metrics-sample';
import { MetricsTrend, type BreakerMark } from './metrics-trend';
import { WorkersTable } from './workers-table';
import { StuckRunsPanel } from './stuck-runs-panel';
import {
  ChannelStateBadge,
  MetricTile,
  OpsCard,
  PermissionNotice,
  UnavailableNotice,
  formatBytes,
  formatDuration,
  formatPercent,
  formatTime,
} from './ops-shared';

export interface ClusterDashboardProps {
  ownerToken?: string | null;
  /** 大屏模式复用：干预面板退化为只读摘要。 */
  variant?: 'console' | 'wallboard';
  /** 断路器跳闸标注（披露留存联动，P6 → P3）。 */
  breakerMarks?: BreakerMark[];
}

export function ClusterDashboard({
  ownerToken,
  variant = 'console',
  breakerMarks = [],
}: ClusterDashboardProps) {
  const metrics = useClusterMetrics({ ownerToken });
  const workers = useClusterWorkers({ ownerToken });
  const stuck = useStuckRuns({ ownerToken });

  // 客户端观测窗：每次成功的指标轮询追加一个采样（有界 ≤120）。
  const [samples, setSamples] = useState<MetricsSample[]>([]);
  const metricsData: ClusterMetrics | null = metrics.data;
  useEffect(() => {
    if (metricsData) setSamples((prev) => appendSample(prev, toMetricsSample(metricsData)));
  }, [metricsData]);
  const latest = samples.length > 0 ? samples[samples.length - 1] : null;

  // runs 列表（owner 域）：概览「活跃 run」口径。
  const runs = useBoundedPoll<{ runs: ClusterRunRow[] }>({
    enabled: true,
    pollIntervalMs: 6000,
    fetcher: (signal) => listClusterRuns({ ownerToken, signal, limit: 20 }),
  });
  const activeRuns = useMemo(() => {
    const list = runs.data?.runs ?? [];
    return list.filter((r) => ['queued', 'leased', 'running'].includes(r.status));
  }, [runs.data]);

  // 焦点 run 详情：读 progress 投影（后端尽力字段，缺省不展示）。
  const focusRunId = activeRuns[0]?.run_id ?? null;
  const focusRun = useBoundedPoll<{ run: { progress?: { done: number; total: number } } | null }>({
    enabled: focusRunId != null,
    resetKey: focusRunId ?? 'none',
    pollIntervalMs: 8000,
    fetcher: async (signal) => {
      try {
        const run = await getClusterRun(focusRunId as string, { ownerToken, signal });
        return { run: run as { progress?: { done: number; total: number } } };
      } catch {
        return { run: null };
      }
    },
  });
  const progress = focusRun.data?.run?.progress;
  const progressLabel =
    progress && progress.total > 0 ? `${progress.done}/${progress.total} 结算` : null;

  const anyAdminLocked = metrics.channel === 'admin-required' && workers.channel === 'admin-required';
  const nowMs = Date.now();

  return (
    <div className="flex flex-col gap-3" data-testid="ops-cluster-dashboard">
      {/* 概览卡 */}
      {anyAdminLocked ? (
        <OpsCard title="集群概览" sub="require_admin 控制面">
          <PermissionNotice />
        </OpsCard>
      ) : metrics.channel === 'unavailable' ? (
        <OpsCard title="集群概览" sub="require_admin 控制面">
          <UnavailableNotice />
        </OpsCard>
      ) : (
        <OpsCard
          title="集群概览"
          sub={`require_admin 控制面 · ${metrics.status.lastFetchedAt ? `采样 ${formatTime(metrics.status.lastFetchedAt)}` : '等待首采'}`}
          actions={
            <button
              type="button"
              onClick={() => {
                metrics.refresh();
                workers.refresh();
                stuck.refresh();
              }}
              className="rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
            >
              刷新
            </button>
          }
          testId="ops-overview"
        >
          <div className="grid grid-cols-3 gap-1.5">
            <MetricTile
              label="worker 在线"
              value={
                workers.overview
                  ? workers.overview.ratio == null
                    ? `${workers.overview.live}/${workers.overview.total}`
                    : `${workers.overview.live}/${workers.overview.total}（${formatPercent(workers.overview.ratio)}）`
                  : '—'
              }
              hint={workers.channel === 'admin-required' ? '明细需管理员权限' : undefined}
              tone={workers.overview && workers.overview.ratio != null && workers.overview.ratio < 0.5 ? 'warning' : 'success'}
            />
            <MetricTile
              label="队列深度"
              value={latest ? String(latest.queueDepth) : '—'}
              hint="queued+preempted"
              tone={latest && latest.queueDepth > 10 ? 'warning' : 'info'}
            />
            <MetricTile label="在飞" value={latest ? String(latest.inflight) : '—'} hint="leased+running" tone="info" />
            <MetricTile label="利用率" value={latest ? formatPercent(latest.utilizationRatio) : '—'} hint="reserved/capacity" />
            <MetricTile
              label="spill / 重水化"
              value={latest ? `${latest.spillCount}` : '—'}
              hint={metricsData ? `${formatBytes(metricsData.spill.bytes)} · 命中 ${metricsData.spill.rehydrate_hits}` : undefined}
            />
            <MetricTile label="活跃 run" value={String(activeRuns.length)} hint={progressLabel ?? 'owner 域可见'} tone="neutral" />
          </div>
        </OpsCard>
      )}

      {/* 指标时序 */}
      <MetricsTrend samples={samples} breakerMarks={breakerMarks} height={variant === 'wallboard' ? 150 : 120} />

      {/* Workers 表 */}
      <OpsCard
        title="Workers"
        sub="心跳 ≤15s 新鲜 / ≤60s 迟滞 / >60s 失联（展示口径）"
        actions={<ChannelStateBadge channel={workers.channel} />}
        testId="ops-workers"
      >
        {workers.channel === 'admin-required' ? (
          <PermissionNotice description="worker 心跳与能力明细位于 require_admin 端点。" />
        ) : workers.channel === 'unavailable' ? (
          <UnavailableNotice />
        ) : workers.data ? (
          <WorkersTable workers={workers.data.workers} />
        ) : (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">正在加载…</p>
        )}
      </OpsCard>

      {/* Stuck 干预（大屏只读摘要） */}
      {variant === 'console' ? (
        <StuckRunsPanel stuck={stuck} />
      ) : (
        <OpsCard
          title="Stuck 队列"
          sub={`${stuck.data?.count ?? 0} 个卡住 run · 只读`}
          actions={<ChannelStateBadge channel={stuck.channel} />}
        >
          {stuck.data && stuck.data.runs.length > 0 ? (
            <ul className="flex flex-col gap-1" aria-label="卡住 run 摘要">
              {stuck.data.runs.map((row) => (
                <li key={row.run_id} className="flex items-center justify-between text-micro text-ink-secondary">
                  <span className="truncate font-mono">{row.run_id}</span>
                  <span className="shrink-0 text-status-critical">
                    卡住 {formatDuration(stuckDurationS(row, nowMs))}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-2 py-2 text-center text-meta text-ink-muted" role="status">无卡住 run</p>
          )}
        </OpsCard>
      )}

      {/* 活跃 runs 摘要 */}
      {variant === 'console' && activeRuns.length > 0 && (
        <OpsCard
          title="活跃 runs"
          sub="owner 域可见的进行中 run"
          actions={
            <span className="flex items-center gap-1 text-micro text-ink-muted">
              <Users size={11} aria-hidden />
              {activeRuns.length}
            </span>
          }
          testId="ops-active-runs"
        >
          <ul className="flex flex-col gap-1" aria-label="活跃 run 列表">
            {activeRuns.slice(0, 8).map((row) => (
              <li key={row.run_id} className="flex items-center justify-between gap-2 text-micro text-ink-secondary">
                <span className="truncate font-mono" title={row.run_id}>{row.run_id}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <span className="text-ink-muted">{row.required_profiles?.[0] ?? '—'}</span>
                  <StatusForRun status={row.status} />
                </span>
              </li>
            ))}
          </ul>
          <p className="flex items-center gap-1 text-micro text-ink-muted">
            <Layers3 size={11} aria-hidden />
            共 {activeRuns.length} 个进行中（列表上限 20）
          </p>
        </OpsCard>
      )}
    </div>
  );
}

function StatusForRun({ status }: { status: string }) {
  return <ChannelStateBadge channel={['queued', 'leased', 'running'].includes(status) ? 'live' : 'terminal'} />;
}
