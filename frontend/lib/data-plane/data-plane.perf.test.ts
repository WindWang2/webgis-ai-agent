import { describe, it, expect } from 'vitest';

import { buildLoadPlan } from '@/lib/data-plane/plan';
import { computeFilterThin } from '@/lib/data-plane/async-viewport-compute';
import { estimateFeatureCollectionBytes, RefDataCache } from '@/lib/data-plane/cache';

/**
 * 数据面规模基准（extreme-scale v2，合成 workload；本地基线棘轮，非 SLO）。
 *
 * 规模维度：图层元数据到 10⁶ 要素（planner O(layers)，不吃要素本体）、
 * 真实要素集合到 10⁵（bbox 过滤+抽稀 O(n) 主线程上限）、调度吞吐 10⁴
 * 请求。所有绝对时序断言先测纯内存吞吐地板 —— 低于地板 = 进程被并行
 * worker 争抢，此时显式 SKIP（打印原因，不静默）。
 */

/** 纯内存写吞吐地板（MB/s）：低于它 = CPU 被争抢，绝对时序不可信。 */
const THROUGHPUT_FLOOR_MB_PER_MS = 0.2;

function throughputFloorOk(): boolean {
  const buf = new Float64Array(1 << 20); // 8 MB
  const t0 = performance.now();
  for (let i = 0; i < buf.length; i += 1) buf[i] = i * 0.5;
  const ms = performance.now() - t0;
  const mbPerMs = (buf.byteLength / (1024 * 1024)) / ms;
  return mbPerMs >= THROUGHPUT_FLOOR_MB_PER_MS;
}

function p95(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
}

function pointFeature(i: number) {
  return {
    type: 'Feature' as const,
    id: i,
    geometry: { type: 'Point' as const, coordinates: [100 + (i % 1000) * 0.01, 30 + Math.floor(i / 1000) * 0.01] },
    properties: { i },
  };
}

function makeFc(n: number) {
  return {
    type: 'FeatureCollection' as const,
    features: Array.from({ length: n }, (_, i) => pointFeature(i)),
  };
}

describe('data-plane scale — planner is O(layers), survives 10⁶-feature metadata', () => {
  it('plans 10 layers whose metadata sums to 1M features in <5ms (p95 of 200 runs)', () => {
    if (!throughputFloorOk()) {
      console.warn('[SKIP-absolute-timing] CPU contended; planner p95 assertion deferred to dedicated run');
      return;
    }
    const layers = [
      { layerId: 'l-1m', visible: true, refId: 'ref:m1', hasTileUrl: true, descriptor: { feature_count: 1_000_000, mvt_capable: true, estimated_bytes: 200_000_000, bbox: [100, 30, 101, 31] as [number, number, number, number] } },
      ...Array.from({ length: 9 }, (_, i) => ({
        layerId: `l-${i}`,
        visible: i % 2 === 0,
        refId: `ref:${i}`,
        hasTileUrl: i < 3,
        descriptor: { feature_count: 10_000, mvt_capable: true, estimated_bytes: 2_000_000, bbox: null as [number, number, number, number] | null },
      })),
    ];
    const durations: number[] = [];
    let plan = buildLoadPlan(layers, { bounds: [100, 30, 101, 31], zoom: 12 });
    for (let i = 0; i < 200; i += 1) {
      const t0 = performance.now();
      plan = buildLoadPlan(layers, { bounds: [100, 30, 101, 31], zoom: 12 });
      durations.push(performance.now() - t0);
    }
    expect(plan.decisions).toHaveLength(10);
    expect(p95(durations)).toBeLessThan(5);
  });

  it('10k-layer plan completes at all (session-scale upper bound) in <50ms', () => {
    if (!throughputFloorOk()) {
      console.warn('[SKIP-absolute-timing] CPU contended; 10k-layer plan assertion deferred');
      return;
    }
    const layers = Array.from({ length: 10_000 }, (_, i) => ({
      layerId: `layer-${i}`,
      visible: true,
      refId: `ref:${i}`,
      descriptor: { feature_count: 100, mvt_capable: false, estimated_bytes: 20_000, bbox: null },
    }));
    const t0 = performance.now();
    const plan = buildLoadPlan(layers, { bounds: [0, 0, 1, 1], zoom: 10 });
    const ms = performance.now() - t0;
    expect(plan.decisions).toHaveLength(10_000);
    expect(ms).toBeLessThan(50);
  });
});

describe('data-plane scale — viewport compute 10k/100k real features', () => {
  it('100k-feature bbox filter + thin deterministic op < 60ms (fallback upper bound)', () => {
    if (!throughputFloorOk()) {
      console.warn('[SKIP-absolute-timing] CPU contended; viewport compute assertion deferred');
      return;
    }
    const fc = makeFc(100_000);
    const view: [number, number, number, number] = [100, 30, 103.5, 40]; // ~35% in-bbox
    // warm（JIT/形状缓存公平性）
    computeFilterThin(fc, view, 5000);
    const durations: number[] = [];
    for (let i = 0; i < 5; i += 1) {
      const t0 = performance.now();
      const trimmed = computeFilterThin(fc, view, 5000);
      durations.push(performance.now() - t0);
      expect(trimmed.features.length).toBeLessThanOrEqual(5000);
    }
    // 本地棘轮（实测 ~45ms/i7，非 SLO）：≥20k 的生产路径走 viewport.worker
    // 离主线程（M6），这个同步预算只约束「worker 不可用回退」的最坏 jank。
    expect(p95(durations)).toBeLessThan(60);
  });
});

describe('data-plane scale — scheduler throughput 10k synthetic requests', () => {
  it('enqueues+settles 10k cached-key requests without unbounded memory', async () => {
    const cache = new RefDataCache({ maxBytes: 4 * 1024 * 1024 });
    const fcSmall = makeFc(20); // ~4.5KB/条 by estimate
    const keys = Array.from({ length: 10_000 }, (_, i) => `s::ref:${i}`);
    const t0 = performance.now();
    keys.forEach((k) => cache.set(k, fcSmall));
    const elapsed = performance.now() - t0;
    // 4MB 预算 → 大量逐出发生，但总账永不超预算（可预测逐出 Oracle 的规模面）。
    expect(cache.totalBytes).toBeLessThanOrEqual(4 * 1024 * 1024);
    expect(elapsed).toBeLessThan(500);
  });

  it('estimate bytes stays monotonic at 10k/100k/1M synthetic features', () => {
    const b10k = estimateFeatureCollectionBytes(makeFc(10_000));
    const b100k = estimateFeatureCollectionBytes(makeFc(100_000));
    const b1m = estimateFeatureCollectionBytes(makeFc(1_000_000));
    expect(b10k).toBeGreaterThan(0);
    expect(b100k).toBeGreaterThan(b10k);
    expect(b1m).toBeGreaterThan(b100k);
  });
});
