'use client';

/**
 * 五类观测指标时序图（P3，ADR-0133 transfer/cache/lineage/utilization/quarantine）。
 *
 * - chart-core 主题化（line/kpi 词表内）；时间范围选择器（15/30/60 采样点）；
 * - 数据是「客户端观测窗」（见 metrics-sample.ts），窗口语义在图题如实标注；
 * - 断路器跳闸标注：把披露留存（P6）或 fixture 叙事里的 open 区间叠加为
 *   图表底部的时间带（§3 加深杠杆），纯装饰层 aria-hidden。
 */
import { useMemo, useState } from 'react';
import { ChartCore } from '@/components/chat/chart-core';
import type { ChartData } from '@/lib/types';
import type { MetricsSample } from './metrics-sample';
import { deltas } from './metrics-sample';
import { OpsCard, formatBytes, formatPercent, formatTime } from './ops-shared';

const RANGES = [
  { label: '15', value: 15 },
  { label: '30', value: 30 },
  { label: '60', value: 60 },
] as const;

export interface BreakerMark {
  /** 采样索引（metrics 窗口内）。 */
  index: number;
  label: string;
}

export function MetricsTrend({
  samples,
  breakerMarks = [],
  height = 120,
}: {
  samples: MetricsSample[];
  breakerMarks?: BreakerMark[];
  height?: number;
}) {
  const [range, setRange] = useState<number>(30);

  const windowed = useMemo(
    () => (samples.length > range ? samples.slice(samples.length - range) : samples),
    [samples, range],
  );

  const charts = useMemo(() => {
    if (windowed.length === 0) return [];
    const labels = windowed.map((s) => formatTime(s.t));
    const transfer = deltas(windowed.map((s) => s.transferBytesTotal));
    const cache = deltas(windowed.map((s) => s.cacheHitsTotal));
    const completed = deltas(windowed.map((s) => s.lineage.node_completed));
    const reused = deltas(windowed.map((s) => s.lineage.node_reused));
    const utilization = windowed.map((s) => s.utilizationRatio);
    const quarantine = windowed.map((s) => s.quarantineActive);

    const line = (values: (number | null)[], title: string, yLabel: string): ChartData => ({
      type: 'line',
      title,
      y_label: yLabel,
      data: values
        .map((v, i) => ({ name: labels[i], value: v }))
        .filter((pt): pt is { name: string; value: number } => pt.value != null),
    });

    return [
      line(transfer, '传输吞吐（增量/采样）', 'bytes'),
      line(cache, '缓存命中（增量/采样）', 'hits'),
      line(completed.map((v, i) => (v == null ? null : v + (reused[i] ?? 0))), '谱系活动（完成+复用 增量）', 'nodes'),
      line(utilization, '利用率', 'ratio'),
      line(quarantine, '检疫活跃数', 'rows'),
    ];
  }, [windowed]);

  const latest = windowed[windowed.length - 1];

  return (
    <OpsCard
      title="观测指标时序"
      sub={`客户端观测窗（自面板打开起 ${samples.length} 个采样）——非服务端历史`}
      actions={
        <div role="radiogroup" aria-label="时间范围（采样点数）" className="flex items-center gap-0.5">
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              role="radio"
              aria-checked={range === r.value}
              onClick={() => setRange(r.value)}
              className={`rounded-sm px-1.5 py-0.5 text-micro font-medium transition-colors ${
                range === r.value
                  ? 'bg-status-accent-soft text-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      }
      testId="ops-metrics-trend"
    >
      {charts.length === 0 ? (
        <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
          暂无采样 —— 等待第一次轮询成功
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {charts.map((chart) => (
            <div key={chart.title} className="min-w-0">
              <p className="mb-0.5 text-micro font-medium text-ink-secondary">{chart.title}</p>
              <div className="relative">
                {/* 断路器跳闸时间带（装饰层，纯底纹 + 底部说明） */}
                {breakerMarks.length > 0 && (
                  <div aria-hidden className="pointer-events-none absolute inset-0" data-testid="breaker-marks">
                    {breakerMarks.map((m) => (
                      <div
                        key={m.index}
                        data-testid="breaker-mark"
                        className="absolute inset-y-0 w-1.5 bg-status-critical/20"
                        style={{ left: `${(m.index / Math.max(1, windowed.length - 1)) * 100}%` }}
                      />
                    ))}
                  </div>
                )}
                <ChartCore chart={chart} height={height} />
              </div>
            </div>
          ))}
        </div>
      )}
      {latest && (
        <p className="text-micro text-ink-muted">
          最新采样 {formatTime(latest.t)} · 传输累计 {formatBytes(latest.transferBytesTotal)} · 利用率{' '}
          {formatPercent(latest.utilizationRatio)} · 队列 {latest.queueDepth} · 在飞 {latest.inflight}
        </p>
      )}
    </OpsCard>
  );
}
