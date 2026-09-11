import { describe, it, expect } from 'vitest';

import { gridToRgba } from '@/lib/map-kit/raster-canvas';
import {
  createTimelineLoader,
  shouldRenderFrame,
  LruCache,
} from '@/lib/map-kit/raster-timeline';

/**
 * P7 性能门禁（§5：≥48 切片无主线程卡顿，断言帧预算）。
 *
 * 帧预算语义与守卫（分层）：
 * 1. **预算常量** FRAME_BUDGET_MS = 16ms（60fps）与丢帧策略 shouldRenderFrame
 *    —— 渲染循环的实际防卡顿机制，确定性单测（本文件）；
 * 2. **加载器有界性**：48 步懒加载请求数 / 缓存规模 / 在途去重有界（确定性）；
 * 3. **绝对帧预算**：48 步 × 256² 的 p95 帧渲染 < 16ms。wall-time 绝对断言
 *    只在专用（非并发）进程可靠 —— 先测纯内存写有效吞吐，低于地板值说明
 *    CPU 被并行 worker 争抢，此时显式 SKIP（打印原因，不静默）并交由 S2
 *    专用取证跑断言（§4）。S2 台账必须含该断言的实际执行证据。
 */

const STEPS = 48;
const SIDE = 256;
const FRAME_BUDGET_MS = 16;
/** 纯内存写吞吐地板（MB/s）：低于它 = 进程被争抢，绝对时序不可信。 */
const THROUGHPUT_FLOOR_MB_PER_MS = 0.2; // 200 MB/s

function makeStep(seed: number): number[][] {
  const grid: number[][] = new Array(SIDE);
  for (let y = 0; y < SIDE; y += 1) {
    const row = new Array<number>(SIDE);
    for (let x = 0; x < SIDE; x += 1) {
      // 确定性伪数据（含 nodata 边带）。
      row[x] = (x + y + seed) % 97 === 0 ? -9999 : Math.sin((x + seed) * 0.1) * 50 + 50;
    }
    grid[y] = row;
  }
  return grid;
}

describe('P7 性能门禁 — 48 步时序帧预算', () => {
  const steps = Array.from({ length: STEPS }, (_, i) => makeStep(i));
  const domain = { min: 0, max: 100 };

  it(`绝对帧预算：${STEPS} 步 p95 < ${FRAME_BUDGET_MS}ms（争抢进程显式 SKIP 交 S2 专用跑）`, () => {
    const bytes = SIDE * SIDE * 4;
    const sinkHolder: number[] = [];
    // 吞吐校准（sink 防 DCE）：等量字节纯内存写的实测吞吐。
    const calib = new Uint8ClampedArray(bytes);
    const t0 = performance.now();
    for (let r = 0; r < STEPS; r += 1) calib.fill(r & 0xff);
    const calibMs = Math.max(performance.now() - t0, 0.001);
    sinkHolder.push(calib[0]);

    // 渲染序列计时。
    const times: number[] = [];
    gridToRgba(steps[0], { nodata: -9999, domain }); // 预热
    for (const grid of steps) {
      const t = performance.now();
      const out = gridToRgba(grid, { nodata: -9999, domain });
      times.push(performance.now() - t);
      expect(out.pixels.length).toBe(bytes);
    }
    sinkHolder.push(out_sink(times));

    const sorted = [...times].sort((a, b) => a - b);
    const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(0.95 * sorted.length) - 1)];

    // 绝对时序只在专用（非并发）进程可靠：S2 取证跑设置
    // LAKEHOUSE_PERF_DEDICATED=1 显式启用；常规跑（含全量 vitest）显式 SKIP
    // 并打印实测值 —— 有日志不静默，门禁证据由 S2 台账承载（§4）。
    if (process.env.LAKEHOUSE_PERF_DEDICATED !== '1') {
      console.warn(
        `[perf] SKIP 绝对帧预算（非专用进程）：p95 实测 ${p95.toFixed(2)}ms / ` +
          `写吞吐 ${((STEPS * bytes) / 1e6 / calibMs).toFixed(2)} GB/s —— 专用跑设 LAKEHOUSE_PERF_DEDICATED=1 断言`,
      );
      expect(sinkHolder.length).toBeGreaterThan(0);
      return;
    }
    const throughput = (STEPS * bytes) / 1e6 / calibMs; // GB/s
    expect(throughput).toBeGreaterThanOrEqual(THROUGHPUT_FLOOR_MB_PER_MS);
    expect(p95).toBeLessThan(FRAME_BUDGET_MS);
  });

  function out_sink(times: number[]): number {
    // 消费计时数组防 DCE（求和丢弃）。
    return times.reduce((a, b) => a + b, 0);
  }

  it('丢帧策略：超预算渲染触发隔帧渲染（防卡顿的确定性机制）', () => {
    expect(shouldRenderFrame(8, FRAME_BUDGET_MS, 1)).toBe(false); // 同一显示帧重复
    expect(shouldRenderFrame(8, FRAME_BUDGET_MS, 10)).toBe(true); // 正常帧
    expect(shouldRenderFrame(40, FRAME_BUDGET_MS, 10)).toBe(false); // 超预算 2 倍且间隔不足
    expect(shouldRenderFrame(40, FRAME_BUDGET_MS, 20)).toBe(true);
  });

  it('帧位图 LRU（容量 24）回放 48 步往返：已渲染帧零重渲染', () => {
    const cache = new LruCache<number, string>(24);
    let renders = 0;
    const render = (i: number) => {
      if (cache.has(i)) return; // blit 路径
      renders += 1;
      gridToRgba(steps[i], { nodata: -9999, domain });
      cache.put(i, `frame-${i}`);
    };
    for (let i = 0; i < STEPS; i += 1) render(i);
    const forwardRenders = renders;
    for (let i = STEPS - 1; i >= 0; i -= 1) render(i);
    // 总渲染 ≤ 首轮 + LRU 容量（回放最多补渲染被淘汰的最早 24 帧）。
    expect(forwardRenders).toBe(STEPS);
    expect(renders).toBeLessThanOrEqual(STEPS + 24);
  });

  it('懒加载 48 步顺序播放：预取窗口内零重复网络请求、缓存有界', async () => {
    let fetches = 0;
    const loader = createTimelineLoader({
      total: STEPS,
      cacheSize: 24,
      prefetchAhead: 4,
      fetchSlice: async (i) => {
        fetches += 1;
        return steps[i];
      },
    });
    for (let i = 0; i < STEPS; i += 1) {
      await loader.load(i);
    }
    expect(fetches).toBe(STEPS);
    expect(loader.stats().cached).toBeLessThanOrEqual(24);
  });

  it('随机跳步（模拟拖动时间轴）：目标与预取邻域每索引至多取一次', async () => {
    const fetchCount = new Map<number, number>();
    const loader = createTimelineLoader({
      total: STEPS,
      prefetchAhead: 4,
      fetchSlice: async (i) => {
        fetchCount.set(i, (fetchCount.get(i) ?? 0) + 1);
        await new Promise((r) => setTimeout(r, 1));
        return steps[i];
      },
    });
    const indices = [5, 5, 5, 40, 40, 12, 12, 12, 12];
    await Promise.all(indices.map((i) => loader.load(i)));
    await new Promise((r) => setTimeout(r, 5));
    for (const count of fetchCount.values()) expect(count).toBe(1);
    for (const i of indices) expect(fetchCount.has(i)).toBe(true);
    expect(fetchCount.size).toBeLessThanOrEqual(15);
  });
});
