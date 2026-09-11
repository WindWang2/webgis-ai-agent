/**
 * ops 控制台基础件测试：格式化 / 通道徽标 / Workers 表（含键盘导航）/
 * 五类指标时序（recharts 以 mock 面替换 —— 同 chart-core.test.tsx 模式）。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  return {
    ResponsiveContainer: passthrough,
    BarChart: passthrough,
    LineChart: ({ data }: { data?: unknown }) =>
      React.createElement('div', { 'data-testid': 'core-line-chart', 'data-points': Array.isArray(data) ? data.length : 0 }),
    PieChart: passthrough,
    ScatterChart: passthrough,
    CartesianGrid: passthrough,
    XAxis: passthrough,
    YAxis: passthrough,
    Tooltip: passthrough,
    Legend: passthrough,
    Bar: passthrough,
    Line: passthrough,
    Pie: passthrough,
    Cell: passthrough,
    AreaChart: passthrough,
    Area: passthrough,
    RadialBarChart: passthrough,
    RadialBar: passthrough,
    RadarChart: passthrough,
    Radar: passthrough,
    PolarGrid: passthrough,
    PolarAngleAxis: passthrough,
    PolarRadiusAxis: passthrough,
    Scatter: passthrough,
  };
});

import {
  formatBytes,
  formatDuration,
  formatPercent,
  formatTime,
  ChannelStateBadge,
  MetricTile,
} from './ops-shared';
import { WorkersTable } from './workers-table';
import { MetricsTrend } from './metrics-trend';
import { clusterWorkersFixture, emptyClusterMetricsFixture } from '@/test/fixtures/geocompute-fixtures';
import { buildMetricSeries, type MetricPoint } from '@/test/fixtures/metric-series';
import { toMetricsSample, appendSample } from './metrics-sample';

describe('格式化', () => {
  it('formatBytes 阶梯', () => {
    expect(formatBytes(null)).toBe('—');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(27_140_000_000)).toContain('GB');
  });
  it('formatDuration 阶梯', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(90)).toBe('1m30s');
    expect(formatDuration(3700)).toBe('1h1m');
  });
  it('formatPercent 诚实缺失', () => {
    expect(formatPercent(null)).toBe('—');
    expect(formatPercent(0.538)).toBe('54%');
  });
  it('formatTime 坏时间戳 → —', () => {
    expect(formatTime('not-a-date')).toBe('—');
    expect(formatTime(undefined)).toBe('—');
  });
});

describe('ChannelStateBadge / MetricTile', () => {
  it('权限态与降级态是一等公民词表', () => {
    render(
      <>
        <ChannelStateBadge channel="admin-required" />
        <ChannelStateBadge channel="unavailable" />
        <ChannelStateBadge channel="notfound" />
        <ChannelStateBadge channel="terminal" />
      </>,
    );
    expect(screen.getByText('需要管理员权限')).toBeInTheDocument();
    expect(screen.getByText('集群不可用')).toBeInTheDocument();
    expect(screen.getByText('事件不可用')).toBeInTheDocument();
    expect(screen.getByText('已终态')).toBeInTheDocument();
  });
  it('MetricTile 值可检索', () => {
    render(<MetricTile label="队列深度" value="5" tone="warning" />);
    expect(screen.getByTestId('tile-队列深度')).toHaveTextContent('5');
  });
});

describe('WorkersTable（P3）', () => {
  it('渲染心跳分档与能力诚实缺失', () => {
    render(<WorkersTable workers={clusterWorkersFixture.workers} />);
    expect(screen.getByTestId(`worker-row-${clusterWorkersFixture.workers[0].worker_id}`)).toBeInTheDocument();
    expect(screen.getAllByText('新鲜').length).toBeGreaterThan(0);
    expect(screen.getByText('迟滞')).toBeInTheDocument();
    expect(screen.getByText('失联')).toBeInTheDocument();
    expect(screen.getAllByText('未披露').length).toBe(2); // capability=null ×2
    expect(screen.getByText('协调器')).toBeInTheDocument();
  });
  it('空表 → 无 worker 在线', () => {
    render(<WorkersTable workers={[]} />);
    expect(screen.getByText('无 worker 在线')).toBeInTheDocument();
  });
  it('键盘导航：ArrowDown 焦点移到下一行', () => {
    render(<WorkersTable workers={clusterWorkersFixture.workers} />);
    const rows = screen.getAllByRole('row').filter((r) => r.getAttribute('tabindex') === '0');
    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: 'ArrowDown' });
    expect(rows[1]).toHaveFocus();
    fireEvent.keyDown(rows[1], { key: 'ArrowUp' });
    expect(rows[0]).toHaveFocus();
    fireEvent.keyDown(rows[0], { key: 'End' });
    expect(rows[rows.length - 1]).toHaveFocus();
  });
});

describe('MetricsTrend（五类指标时序）', () => {
  const series: MetricPoint[] = buildMetricSeries(60);
  const samples = series.slice(0, 40).reduce<ReturnType<typeof toMetricsSample>[]>((acc, p) => {
    const t = new Date(Date.parse('2026-09-12T01:00:00Z') + p.minute * 60_000).toISOString();
    return appendSample(acc, { ...toMetricsSample({ ...emptyMetrics(p) }, t), breakerOpen: p.breakerOpen });
  }, []);

  it('渲染五张时序图 + 窗口语义标注', () => {
    render(<MetricsTrend samples={samples} />);
    expect(screen.getByText('传输吞吐（增量/采样）')).toBeInTheDocument();
    expect(screen.getByText('缓存命中（增量/采样）')).toBeInTheDocument();
    expect(screen.getByText('谱系活动（完成+复用 增量）')).toBeInTheDocument();
    expect(screen.getByText('利用率')).toBeInTheDocument();
    expect(screen.getByText('检疫活跃数')).toBeInTheDocument();
    expect(screen.getByText(/客户端观测窗/)).toBeInTheDocument();
    // 40 个采样 → 5 图 × (40-1 有效点)
    expect(screen.getAllByTestId('core-line-chart').length).toBe(5);
  });

  it('时间范围选择器切换窗口', () => {
    render(<MetricsTrend samples={samples} />);
    const r15 = screen.getByRole('radio', { name: '15' });
    fireEvent.click(r15);
    expect(r15).toHaveAttribute('aria-checked', 'true');
  });

  it('断路器跳闸标注叠加为时间带', () => {
    const marks = samples
      .map((s, i) => (s.breakerOpen ? { index: i, label: 'open' } : null))
      .filter((m): m is { index: number; label: string } => m !== null);
    const { container } = render(<MetricsTrend samples={samples} breakerMarks={marks} />);
    // fixture 叙事 34–41 分钟 open；窗口截到 40 → 6 个标注 × 5 张图
    expect(container.querySelectorAll('[data-testid="breaker-mark"]').length).toBe(marks.length * 5);
  });

  it('空采样 → 等待态', () => {
    render(<MetricsTrend samples={[]} />);
    expect(screen.getByText(/暂无采样/)).toBeInTheDocument();
  });
});

/** 用 fixture 采样点拼 ClusterMetrics（最小内联版）。 */
function emptyMetrics(p: MetricPoint) {
  return {
    ...emptyClusterMetricsFixture,
    queue_depth: p.queueDepth,
    inflight: p.inflight,
    workers: { live: p.workersLive, by_role: {}, profile_slots: {}, gpu_workers: 0 },
    spill: { count: p.spillCount, bytes: 0, rehydrate_hits: 0, rehydrate_misses: 0 },
    transfer: { bytes_total: p.transferBytes },
    cache: { worker_cache_hits: p.cacheHits },
    lineage: {
      node_completed: p.nodeCompleted,
      node_reused: p.nodeReused,
      node_lost: p.nodeLost,
      partition_planned: p.partitionPlanned,
      speculative_dispatched: p.speculativeDispatched,
      poison_quarantined: p.poisonQuarantined,
    },
    utilization: { reserved_units: 0, capacity_units: 26, ratio: p.utilization },
  };
}
