'use client';

/**
 * Stuck runs 干预面板（P3）。
 *
 * - 列表：卡住时长（lease 过期近似）/ 最后心跳 / 优先级 / 所属 profile；
 * - 动作：重派（outcome=requeued）/ 隔离驱逐（outcome=failed）—— 一律经
 *   ConfirmDialog 二次确认（不可逆干预），动作后回执走 role=status live
 *   region（a11y），并提供「任务中心」联动入口；
 * - 409 RUN_NOT_RESETABLE 是并发回执不是故障（hook 已折叠为 warning）。
 */
import { useState } from 'react';
import { TimerReset, ShieldBan, ClipboardList } from 'lucide-react';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { StatusBadge } from '@/components/shared/status-badge';
import { useHudStore } from '@/lib/store/useHudStore';
import { stuckDurationS, type UseStuckRunsResult } from '@/lib/hooks/use-cluster-stuck-runs';
import { OpsCard, formatDuration } from './ops-shared';

type PendingAction = { runId: string; outcome: 'requeued' | 'failed' } | null;

export function StuckRunsPanel({ stuck }: { stuck: UseStuckRunsResult }) {
  const [pending, setPending] = useState<PendingAction>(null);
  const setLeftTab = useHudStore((s) => s.setActiveLeftTab);
  const runs = stuck.data?.runs ?? [];
  const nowMs = Date.now();

  return (
    <OpsCard
      title="Stuck Runs"
      sub={`占用租约且过期 >5s 的 run（${stuck.data?.count ?? 0}）`}
      actions={
        stuck.status.lastFetchedAt && (
          <span className="text-micro text-ink-muted">更新于 {formatDuration((nowMs - Date.parse(stuck.status.lastFetchedAt)) / 1000)}前</span>
        )
      }
      testId="ops-stuck-runs"
    >
      {runs.length === 0 ? (
        <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
          {stuck.loading && !stuck.data ? '正在加载…' : '当前无卡住 run'}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5" aria-label="卡住 run 列表">
          {runs.map((row) => {
            const dur = stuckDurationS(row, nowMs);
            const busy = stuck.resetting.has(row.run_id);
            return (
              <li
                key={row.run_id}
                data-testid={`stuck-row-${row.run_id}`}
                className="flex flex-col gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-micro text-ink" title={row.run_id}>
                    {row.run_id}
                  </span>
                  <StatusBadge status={row.status} />
                </div>
                <div className="flex items-center gap-2 text-micro text-ink-muted">
                  <span>
                    卡住 <span className="font-medium text-status-critical">{formatDuration(dur)}</span>
                  </span>
                  <span aria-hidden>·</span>
                  <span>心跳 {formatDuration(row.heartbeat_at ? (nowMs - Date.parse(row.heartbeat_at)) / 1000 : null)}前</span>
                  <span aria-hidden>·</span>
                  <span>尝试 {row.attempts} 次 / epoch {row.lease_epoch}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    disabled={busy}
                    data-testid={`requeue-${row.run_id}`}
                    onClick={() => setPending({ runId: row.run_id, outcome: 'requeued' })}
                    className="flex items-center gap-1 rounded-sm border border-status-info-border bg-status-info-soft px-1.5 py-0.5 text-micro font-medium text-status-info transition-opacity hover:opacity-85 disabled:opacity-50"
                  >
                    <TimerReset size={11} aria-hidden />
                    {busy ? '处理中…' : '重派'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    data-testid={`evict-${row.run_id}`}
                    onClick={() => setPending({ runId: row.run_id, outcome: 'failed' })}
                    className="flex items-center gap-1 rounded-sm border border-status-critical-border bg-status-critical-soft px-1.5 py-0.5 text-micro font-medium text-status-critical transition-opacity hover:opacity-85 disabled:opacity-50"
                  >
                    <ShieldBan size={11} aria-hidden />
                    {busy ? '处理中…' : '隔离驱逐'}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* 回执 live region：干预结果（含并发 409 警告）在此播报 */}
      <div role="status" aria-live="polite" data-testid="stuck-receipts" className="flex flex-col gap-1">
        {stuck.receipts.slice(0, 3).map((r, i) => (
          <div
            key={`${r.runId}-${r.at}-${i}`}
            className={`flex items-center justify-between gap-2 rounded-sm border px-2 py-1 text-micro ${
              r.warning
                ? 'border-status-warning-border bg-status-warning-soft text-status-warning'
                : 'border-status-success-border bg-status-success-soft text-status-success'
            }`}
          >
            <span className="truncate">
              {r.warning ?? `已${r.outcome === 'requeued' ? '重新入队' : '标记失败'}：${r.runId}`}
            </span>
            <button
              type="button"
              onClick={() => setLeftTab('tasks')}
              className="flex shrink-0 items-center gap-1 rounded-sm px-1 py-0.5 font-medium text-ink-secondary hover:bg-surface-hover"
            >
              <ClipboardList size={11} aria-hidden />
              任务中心
            </button>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.outcome === 'requeued' ? '确认重派该 run？' : '确认隔离驱逐该 run？'}
        description={
          pending?.outcome === 'requeued'
            ? '重派会把 run 放回队列重新调度（attempts+1）。卡住原因若未消除可能再次卡住。'
            : '驱逐会把该 run 标记为终态失败并释放租约，不可恢复。'
        }
        confirmLabel={pending?.outcome === 'requeued' ? '重派' : '驱逐'}
        onConfirm={() => {
          if (pending) void stuck.resetRun(pending.runId, pending.outcome);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
    </OpsCard>
  );
}
