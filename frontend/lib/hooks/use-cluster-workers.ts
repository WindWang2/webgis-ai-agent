/**
 * Workers 心跳轮询（ADR-0142 D3；require_admin）。心跳新鲜度分档：
 * fresh ≤15s / warm ≤60s / stale >60s（worker 租约侧的硬超时由后端负责，
 * 前端分档仅是展示口径，不参与任何控制决策）。
 */
'use client';

import { getClusterWorkers, type ClusterWorker } from '@/lib/api/geocompute';
import {
  useBoundedPoll,
  DEFAULT_CLUSTER_POLL_INTERVAL_MS,
  type UseBoundedPollResult,
} from './use-cluster-poll';
import { classifyClusterChannel, type ClusterChannel } from './use-cluster-metrics';

export type HeartbeatFreshness = 'fresh' | 'warm' | 'stale';

export function heartbeatFreshness(ageS: number): HeartbeatFreshness {
  if (ageS <= 15) return 'fresh';
  if (ageS <= 60) return 'warm';
  return 'stale';
}

export interface UseClusterWorkersOptions {
  enabled?: boolean;
  pollIntervalMs?: number;
  ownerToken?: string | null;
}

export type UseClusterWorkersResult = UseBoundedPollResult<{
  workers: ClusterWorker[];
  live: number;
}> & {
  channel: ClusterChannel;
  /** 在线率概览卡数据（无 worker 时 null —— 诚实缺失，不虚构 100%）。 */
  overview: { live: number; total: number; ratio: number | null } | null;
};

export function useClusterWorkers(options: UseClusterWorkersOptions = {}): UseClusterWorkersResult {
  const { enabled = true, pollIntervalMs = DEFAULT_CLUSTER_POLL_INTERVAL_MS, ownerToken } = options;

  const poll = useBoundedPoll<{ workers: ClusterWorker[]; live: number }>({
    enabled,
    pollIntervalMs,
    fetcher: (signal) => getClusterWorkers({ ownerToken, signal }),
  });

  // 通道分类与概览聚合开销可忽略 —— 不做 memo（poll 引用每渲染必新）。
  const channel = classifyClusterChannel(enabled, poll);
  const data = poll.data;
  const overview = data
    ? {
        live: data.live,
        total: data.workers.length,
        ratio: data.workers.length > 0 ? data.live / data.workers.length : null,
      }
    : null;

  return { ...poll, channel, overview };
}
