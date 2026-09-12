'use client';

/**
 * 指标采样模型（P3 时序图的数据底座）。
 *
 * 事实源边界：/cluster/metrics 是**快照端点**，后端没有历史序列查询。
 * 时序图因此是「客户端观测窗」：面板打开期间每次成功轮询追加一个采样点
 * （有界 ≤120 点），时间范围选择器只裁剪这个窗口。窗口起点之前的历史
 * 不存在 —— 图表下方必须如实标注，不得伪装成服务端历史。
 * transfer/cache 的计数是 24h 滑窗累计，时序图展示逐采样增量（速率语义）。
 */
import type { ClusterMetrics } from '@/lib/api/geocompute';

export interface MetricsSample {
  /** 采样接收时间（ISO，客户端钟）。 */
  t: string;
  /** 24h 滑窗累计（时序图取增量）。 */
  transferBytesTotal: number;
  cacheHitsTotal: number;
  lineage: ClusterMetrics['lineage'];
  utilizationRatio: number | null;
  queueDepth: number;
  inflight: number;
  workersLive: number;
  quarantineActive: number;
  spillCount: number;
  /** 断路器跳闸标注通道（fixture 叙事 / 披露留存叠加；生产快照缺省）。 */
  breakerOpen?: boolean;
}

export const MAX_METRICS_SAMPLES = 120;

export function toMetricsSample(metrics: ClusterMetrics, t = new Date().toISOString()): MetricsSample {
  return {
    t,
    transferBytesTotal: metrics.transfer.bytes_total,
    cacheHitsTotal: metrics.cache.worker_cache_hits,
    lineage: metrics.lineage,
    utilizationRatio: metrics.utilization.ratio,
    queueDepth: metrics.queue_depth,
    inflight: metrics.inflight,
    workersLive: metrics.workers.live,
    quarantineActive: metrics.quarantine.filter((q) => q.active).length,
    spillCount: metrics.spill.count,
  };
}

/** 追加采样（有界环形）。 */
export function appendSample(samples: MetricsSample[], next: MetricsSample): MetricsSample[] {
  const merged = [...samples, next];
  return merged.length > MAX_METRICS_SAMPLES ? merged.slice(merged.length - MAX_METRICS_SAMPLES) : merged;
}

/** 逐采样增量（累计计数 → 速率）；首点无前驱 → null（断点过滤）。 */
export function deltas(values: number[]): (number | null)[] {
  return values.map((v, i) => (i === 0 ? null : Math.max(0, v - values[i - 1])));
}
