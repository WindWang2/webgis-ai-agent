/**
 * 60 分钟合成指标序列（ADR-0142 D4 —— fixture 开发数据，不进生产 bundle）。
 *
 * 形状对齐 /geocompute/cluster/metrics 的五类观测（ADR-0133）+ 控制面计数：
 * 每分钟一个采样点，确定性（LCG 种子）——视觉快照与测试断言可复现。
 * 序列脚本化了一段「健康 → 断路器跳闸 → 恢复」的叙事，供时序图异常标注
 * 与大屏模式使用。
 */

export interface MetricPoint {
  /** 分钟偏移 0..59（相对窗口起点）。 */
  minute: number;
  /** HH:MM 标签（合成时钟，起点 09:00）。 */
  label: string;
  transferBytes: number;
  cacheHits: number;
  nodeCompleted: number;
  nodeReused: number;
  nodeLost: number;
  partitionPlanned: number;
  speculativeDispatched: number;
  poisonQuarantined: number;
  utilization: number | null;
  queueDepth: number;
  inflight: number;
  workersLive: number;
  spillCount: number;
  /** 断路器是否处于 open（跳闸区间标记，供时序异常标注叠加）。 */
  breakerOpen: boolean;
}

/** 确定性伪随机（LCG）——同一 seed 序列完全一致。 */
function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

/** 断路器跳闸区间：第 34–41 分钟 open（含半开试验失败），42 起恢复 closed。 */
export const BREAKER_TRIP_MINUTES: readonly [number, number] = [34, 41];

export function isBreakerOpenAt(minute: number): boolean {
  return minute >= BREAKER_TRIP_MINUTES[0] && minute <= BREAKER_TRIP_MINUTES[1];
}

/** 合成序列（默认 60 点）。seed 固定 → 视觉快照确定性。 */
export function buildMetricSeries(minutes = 60, seed = 20260912): MetricPoint[] {
  const rnd = lcg(seed);
  const points: MetricPoint[] = [];
  let queueDepth = 3;
  for (let m = 0; m < minutes; m += 1) {
    const open = isBreakerOpenAt(m);
    // 跳闸区间：吞吐下滑、丢节点抬升、队列积压；恢复后回摆。
    const dip = open ? 0.35 : 1;
    const recovery = m > BREAKER_TRIP_MINUTES[1] ? 1.25 : 1;
    const transferBytes = Math.round((480_000_000 + rnd() * 240_000_000) * dip * recovery);
    const cacheHits = Math.round((120 + rnd() * 60) * dip);
    const nodeCompleted = Math.round((38 + rnd() * 14) * dip * recovery);
    const nodeReused = Math.round(6 + rnd() * 8);
    const nodeLost = open ? Math.round(2 + rnd() * 4) : rnd() < 0.85 ? 0 : 1;
    const partitionPlanned = Math.round(20 + rnd() * 16);
    const speculativeDispatched = Math.round(1 + rnd() * 4);
    const poisonQuarantined = open && rnd() < 0.4 ? 1 : 0;
    const utilizationBase = 0.55 + rnd() * 0.3;
    const utilization = Math.min(1, Math.max(0.05, utilizationBase * (open ? 0.5 : 1)));
    queueDepth = Math.max(0, Math.min(40, queueDepth + (open ? 3 : -2) + Math.round(rnd() * 3 - 1)));
    const inflight = Math.max(0, Math.round((12 + rnd() * 8) * dip));
    const workersLive = open ? 5 : 6 - (rnd() < 0.1 ? 1 : 0);
    const spillCount = open ? Math.round(4 + rnd() * 6) : Math.round(rnd() * 2);
    const hh = String(9 + Math.floor((m + 0) / 60)).padStart(2, '0');
    const mm = String(m % 60).padStart(2, '0');
    points.push({
      minute: m,
      label: `${hh}:${mm}`,
      transferBytes,
      cacheHits,
      nodeCompleted,
      nodeReused,
      nodeLost,
      partitionPlanned,
      speculativeDispatched,
      poisonQuarantined,
      utilization,
      queueDepth,
      inflight,
      workersLive,
      spillCount,
      breakerOpen: open,
    });
  }
  return points;
}

/** ChartCore 单序列点列（type:'line'）适配。 */
export function toLinePoints(
  series: MetricPoint[],
  pick: (p: MetricPoint) => number | null,
): { name: string; value: number }[] {
  return series
    .map((p) => ({ name: p.label, value: pick(p) }))
    .filter((pt): pt is { name: string; value: number } => pt.value != null);
}
