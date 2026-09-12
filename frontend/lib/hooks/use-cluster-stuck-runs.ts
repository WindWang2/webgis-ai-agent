/**
 * Stuck runs 干预数据源（ADR-0142 D3/D7）。
 *
 * - 轮询 GET /cluster/runs/stuck（require_admin）；
 * - 干预动作 POST /cluster/runs/{id}/reset（outcome: requeued|failed）：
 *   epoch 守卫（面板重挂/账号切换后到达的响应不得写 UI）、动作后立刻补拉、
 *   409 RUN_NOT_RESETABLE 透传为可读回执（stuck 是暂态，别人可能已处理）。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getStuckRuns,
  resetStuckRun,
  GeoComputeApiError,
  ClusterUnavailableError,
  type ClusterRunRow,
  type ResetRunResult,
} from '@/lib/api/geocompute';
import { useBoundedPoll, type UseBoundedPollResult } from './use-cluster-poll';
import { classifyClusterChannel, type ClusterChannel } from './use-cluster-metrics';

/** 卡住时长（s）—— 用 lease 过期时间与当前钟差近似；缺时间戳则 null。 */
export function stuckDurationS(row: ClusterRunRow, nowMs: number): number | null {
  const expiry = row.lease_expires_at ? Date.parse(row.lease_expires_at) : NaN;
  if (Number.isFinite(expiry)) return Math.max(0, (nowMs - expiry) / 1000);
  const started = row.started_at ? Date.parse(row.started_at) : NaN;
  if (Number.isFinite(started)) return Math.max(0, (nowMs - started) / 1000);
  return null;
}

export interface RunResetReceipt {
  runId: string;
  outcome: 'requeued' | 'failed';
  at: string;
  /** 后端拒绝（409 等）时的可读原因。 */
  warning?: string;
}

export interface UseStuckRunsOptions {
  enabled?: boolean;
  pollIntervalMs?: number;
  ownerToken?: string | null;
}

export type UseStuckRunsResult = UseBoundedPollResult<{ runs: ClusterRunRow[]; count: number }> & {
  channel: ClusterChannel;
  /** 重置请求已发出、后端尚未回执的 run（UI 禁重复点击）。 */
  resetting: Set<string>;
  receipts: RunResetReceipt[];
  resetRun: (runId: string, outcome: 'requeued' | 'failed', reason?: string) => Promise<void>;
};

export function useStuckRuns(options: UseStuckRunsOptions = {}): UseStuckRunsResult {
  const { enabled = true, pollIntervalMs, ownerToken } = options;

  const poll = useBoundedPoll<{ runs: ClusterRunRow[]; count: number }>({
    enabled,
    pollIntervalMs: pollIntervalMs ?? 4000,
    fetcher: (signal) => getStuckRuns({ ownerToken, signal }),
  });

  const channel = classifyClusterChannel(enabled, poll);

  const [resetting, setResetting] = useState<Set<string>>(new Set());
  const [receipts, setReceipts] = useState<RunResetReceipt[]>([]);
  const epochRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  const { refresh: refreshStuck } = poll;

  const resetRun = useCallback(
    async (runId: string, outcome: 'requeued' | 'failed', reason?: string) => {
      const epoch = epochRef.current;
      const isStale = () => !mountedRef.current || epoch !== epochRef.current;
      setResetting((prev) => new Set(prev).add(runId));
      try {
        const res: ResetRunResult = await resetStuckRun(runId, {
          reason:
            reason ??
            (outcome === 'requeued'
              ? 'ops console: requeue stuck run'
              : 'ops console: evict stuck run'),
          ownerToken,
        });
        if (isStale()) return;
        setReceipts((prev) =>
          [
            { runId: res.run_id, outcome: res.outcome, at: new Date().toISOString() },
            ...prev,
          ].slice(0, 20),
        );
      } catch (err) {
        if (isStale()) return;
        // 409 = run 已被并发转移（终态/出队）—— 是回执不是故障
        const warning =
          err instanceof GeoComputeApiError && err.code === 'RUN_NOT_RESETABLE'
            ? '该 run 已离开 stuck 状态（可能已被并发处理）'
            : err instanceof GeoComputeApiError || err instanceof ClusterUnavailableError
              ? err.message
              : '干预请求失败';
        setReceipts((prev) =>
          [{ runId, outcome, at: new Date().toISOString(), warning }, ...prev].slice(0, 20),
        );
      } finally {
        if (!isStale()) {
          setResetting((prev) => {
            const next = new Set(prev);
            next.delete(runId);
            return next.size === prev.size ? prev : next;
          });
          refreshStuck();
        }
      }
    },
    [ownerToken, refreshStuck],
  );

  return { ...poll, channel, resetting, receipts, resetRun };
}
