/**
 * 集群指标轮询（ADR-0142 D3；require_admin —— 403 是权限态不是故障，D7）。
 */
'use client';

import {
  getClusterMetrics,
  ClusterUnavailableError,
  GeoComputeApiError,
  type ClusterMetrics,
} from '@/lib/api/geocompute';
import {
  useBoundedPoll,
  DEFAULT_CLUSTER_POLL_INTERVAL_MS,
  type UseBoundedPollResult,
} from './use-cluster-poll';

export type ClusterChannel =
  | 'idle'
  | 'loading'
  | 'live'
  | 'admin-required'
  | 'unavailable'
  | 'error'
  | 'paused';

/** typed 错误 → 通道态（权限/降级是一等公民，非普通错误）。 */
export function classifyClusterChannel(
  enabled: boolean,
  result: Pick<UseBoundedPollResult<unknown>, 'loading' | 'data' | 'lastError' | 'status'>,
): ClusterChannel {
  if (!enabled) return 'idle';
  if (result.status.pauseReason === 'hidden') return 'paused';
  if (result.lastError != null) {
    if (result.lastError instanceof GeoComputeApiError && result.lastError.code === 'ADMIN_REQUIRED') {
      return 'admin-required';
    }
    if (result.lastError instanceof ClusterUnavailableError) return 'unavailable';
    return 'error';
  }
  return result.loading && !result.data ? 'loading' : 'live';
}

export interface UseClusterMetricsOptions {
  enabled?: boolean;
  pollIntervalMs?: number;
  ownerToken?: string | null;
}

export type UseClusterMetricsResult = UseBoundedPollResult<ClusterMetrics> & {
  channel: ClusterChannel;
};

export function useClusterMetrics(options: UseClusterMetricsOptions = {}): UseClusterMetricsResult {
  const { enabled = true, pollIntervalMs = DEFAULT_CLUSTER_POLL_INTERVAL_MS, ownerToken } = options;

  const poll = useBoundedPoll<ClusterMetrics>({
    enabled,
    pollIntervalMs,
    fetcher: (signal) => getClusterMetrics({ ownerToken, signal }),
  });

  // 通道分类是纯函数且开销可忽略 —— 不做 memo（poll 引用每渲染必新）。
  const channel = classifyClusterChannel(enabled, poll);

  return { ...poll, channel };
}

export { GeoComputeApiError, ClusterUnavailableError };
