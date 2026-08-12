/**
 * 任务中心 / Running Jobs（ADR-0052，规范 §28 / §29 / §30）。
 *
 * 轻量面板，不改动既有 UI 结构：作为左侧上下文面板的一个 tab，活动流模式
 * 沿用原 ops-log 模式，进度条沿用 explorer-progress-panel 的 a11y 实现。
 *
 * 同时显示 agent task 与后台 durable GIS job；后者会标出它属于哪个 agent 步骤，
 * 因此用户不会看到两条互不相关的条目。
 *
 * UI V3：顶栏头部由 context-panel 的 PanelHeader 统一提供；本 tab 只保留一条
 * 细工具栏（活跃任务数 + 刷新）。状态徽标 / 错误横幅 / 空态 / 首屏加载全部收敛
 * 到 shared primitives，颜色走主题变量。
 */
'use client';

import { ListChecks, RefreshCw, RotateCcw, X } from 'lucide-react';
import { useMemo } from 'react';

import type { JobStatus, JobView } from '@/lib/api/jobs';
import { useJobCenter } from '@/lib/hooks/use-job-center';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';

interface TasksTabProps {
  sessionId?: string | null;
  ownerToken?: string | null;
  accentColor?: string;
}

const KIND_LABELS: Record<string, string> = {
  agent: 'AI 任务',
  analysis: '空间分析',
  workflow: '工作流',
  explorer: '数据探索',
};

/** 已运行时长。终态用 finished-started，活跃用 now-started。 */
function formatElapsed(job: JobView, now: number): string {
  const startedAt = job.started_at ?? job.created_at;
  if (!startedAt) return '—';
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return '—';
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function JobCard({
  job,
  isCancelling,
  onCancel,
  onRetry,
  now,
  accentColor,
}: {
  job: JobView;
  isCancelling: boolean;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  now: number;
  accentColor: string;
}) {
  // 「取消中」以本地乐观状态或后端状态任一为准；但「已取消」只认后端终态
  const displayStatus: JobStatus =
    isCancelling && job.active && job.status !== 'cancelling' ? 'cancelling' : job.status;
  const showProgress = job.active && job.progress !== null;
  const indeterminate = job.active && job.progress === null;

  return (
    <div
      className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-3 backdrop-blur-sm"
      data-testid={`job-card-${job.id}`}
      aria-busy={job.active}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-[var(--theme-text-primary)]">{job.name}</div>
          <div className="mt-0.5 text-xs text-[var(--theme-text-muted)]">
            {KIND_LABELS[job.kind] ?? job.kind}
            {job.attempt > 1 && ` · 第 ${job.attempt} 次尝试`}
            {job.agent_step_id && ` · 来自 ${job.agent_step_id}`}
          </div>
        </div>
        <StatusBadge status={displayStatus} />
      </div>

      {showProgress && (
        <div className="mt-2">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-[var(--theme-bg-muted)]"
            role="progressbar"
            aria-valuenow={job.progress ?? 0}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${job.name} 进度`}
          >
            <div
              className="h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${job.progress ?? 0}%`, backgroundColor: accentColor }}
            />
          </div>
          <div className="mt-1 flex justify-between text-xs text-[var(--theme-text-muted)]">
            <span className="truncate">{job.message ?? ''}</span>
            <span className="shrink-0 pl-2">{job.progress}%</span>
          </div>
        </div>
      )}

      {indeterminate && (
        // 不确定进度：明确显示为「进行中」而不是编造一个 99% 然后卡住
        <div className="mt-2 text-xs text-[var(--theme-text-muted)]" data-testid={`job-indeterminate-${job.id}`}>
          {job.message ?? '进行中…'}
        </div>
      )}

      {job.error && <div className="mt-2 text-xs text-red-600 dark:text-red-400">{job.error}</div>}

      <div className="mt-2 flex items-center justify-between text-xs text-[var(--theme-text-muted)]">
        <span>已用 {formatElapsed(job, now)}</span>
        <div className="flex items-center gap-2">
          {job.result_ref && !job.active && (
            <span className="max-w-[10rem] truncate text-[var(--theme-text-muted)]" title={job.result_ref}>
              {job.result_ref.split('/').pop()}
            </span>
          )}
          {job.cancellable && (
            <button
              type="button"
              onClick={() => onCancel(job.id)}
              disabled={isCancelling || displayStatus === 'cancelling'}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[var(--theme-text-muted)] transition-colors hover:bg-[var(--theme-bg-hover)] hover:text-[var(--theme-text-primary)] disabled:opacity-40"
              aria-label={`取消 ${job.name}`}
            >
              <X className="h-3 w-3" />
              取消
            </button>
          )}
          {job.retryable && (
            <button
              type="button"
              onClick={() => onRetry(job.id)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[var(--theme-text-muted)] transition-colors hover:bg-[var(--theme-bg-hover)] hover:text-[var(--theme-text-primary)]"
              aria-label={`重试 ${job.name}`}
            >
              <RotateCcw className="h-3 w-3" />
              重试
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function TasksTab({ sessionId, ownerToken, accentColor = '#16a34a' }: TasksTabProps) {
  const { jobs, loading, error, cancelling, refresh, cancel, retry } = useJobCenter({
    sessionId,
    ownerToken,
  });

  // 单次读取的时钟基准：同一渲染内所有卡片用同一个 now，避免逐张 Date.now()
  // 造成「已用时长」互相错开一两秒
  const now = Date.now();

  const { active, finished } = useMemo(() => {
    const a: JobView[] = [];
    const f: JobView[] = [];
    for (const job of jobs) (job.active ? a : f).push(job);
    return { active: a, finished: f };
  }, [jobs]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 细工具栏：活跃任务数（仅 >0 时显示）+ 刷新 */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--theme-border)] px-3 py-1.5">
        {active.length > 0 && (
          <span className="text-[11px] font-medium text-[var(--theme-text-secondary)]">
            {active.length} 个活跃任务
          </span>
        )}
        <IconButton label="刷新任务" icon={RefreshCw} iconSize={13} onClick={() => void refresh()} />
      </div>

      {error && (
        <InlineNotice variant="error" className="mx-3 mt-2">
          {error}
        </InlineNotice>
      )}

      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {jobs.length === 0 && loading ? (
          <LoadingState label="加载任务…" />
        ) : jobs.length === 0 ? (
          <EmptyState icon={ListChecks} title="暂无后台任务" description="开始一次对话后即可查看任务" />
        ) : (
          <>
            {active.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                isCancelling={cancelling.has(job.id)}
                onCancel={cancel}
                onRetry={retry}
                now={now}
                accentColor={accentColor}
              />
            ))}

            {finished.length > 0 && active.length > 0 && (
              <div className="pt-2 text-xs text-[var(--theme-text-subtle)]">已结束</div>
            )}

            {finished.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                isCancelling={cancelling.has(job.id)}
                onCancel={cancel}
                onRetry={retry}
                now={now}
                accentColor={accentColor}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export default TasksTab;
