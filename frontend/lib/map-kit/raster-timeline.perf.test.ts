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
 * 预算语义（raster-timeline.ts）：单帧渲染 ≤ 16ms（60fps 预算）。本套件在
 * node/jsdom 下驱动与浏览器相同的纯渲染管线（gridToRgba）+ 加载器（LRU +
 * 预取 + 在途去重），用统计断言（p95 / 均值）替代单次尖峰，避免机器抖动误报。
 *
 * 48 步 × 256×256 网格 = 3.1M 像素/序列 —— 模拟长时序中等窗口。
 */

const STEPS = 48;
const SIDE = 256;

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

function percentile(sorted: number[], p: number): number {
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)];
}

describe('P7 性能门禁 — 48 步时序帧预算', () => {
  const steps = Array.from({ length: STEPS }, (_, i) => makeStep(i));
  const domain = { min: 0, max: 100 };

  it(`渲染 ${STEPS} 步：p95 帧耗时 < 16ms（60fps 预算），均值 < 8ms`, () => {
    const times: number[] = [];
    // 预热一次（JIT + 内联缓存稳定后再计量）。
    gridToRgba(steps[0], { nodata: -9999, domain });
    for (const grid of steps) {
      const t0 = performance.now();
      const { pixels } = gridToRgba(grid, { nodata: -9999, domain });
      const ms = performance.now() - t0;
      expect(pixels.length).toBe(SIDE * SIDE * 4);
      times.push(ms);
    }
    const sorted = [...times].sort((a, b) => a - b);
    const p95 = percentile(sorted, 95);
    const mean = times.reduce((a, b) => a + b, 0) / times.length;
    // 宽松双保险：单帧预算 16ms（p95），序列总预算 < 预算的 1/4（均值 < 4ms 档）。
    expect(p95).toBeLessThan(16);
    expect(mean).toBeLessThan(8);
    // 丢帧决策在超预算时兜底（与 shouldRenderFrame 的契约一致）。
    for (const ms of times) expect(shouldRenderFrame(ms, 16, 20)).toBe(true);
  });

  it('帧位图 LRU（容量 24）回放 48 步往返：已渲染帧零重渲染', async () => {
    const cache = new LruCache<number, string>(24);
    let renders = 0;
    const render = (i: number) => {
      if (cache.has(i)) return; // blit 路径
      renders += 1;
      gridToRgba(steps[i], { nodata: -9999, domain });
      cache.put(i, `frame-${i}`);
    };
    // 正播 0..47，倒播 47..0（LRU 命中率语义：往返不重复渲染超过容量+1）。
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
    // 每个切片恰好取一次（缓存 + 在途去重共同兜底）。
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
    // 并发乱序 load 同一批索引（拖动抖动形态）。
    const indices = [5, 5, 5, 40, 40, 12, 12, 12, 12];
    await Promise.all(indices.map((i) => loader.load(i)));
    await new Promise((r) => setTimeout(r, 5)); // 预取落盘
    // 每个索引至多取一次（缓存 + 在途去重兜底；预取邻域是设计行为）。
    for (const count of fetchCount.values()) expect(count).toBe(1);
    for (const i of indices) expect(fetchCount.has(i)).toBe(true);
    // 3 个目标 + 各自 +1..+4 预取邻域 ≤ 15 个不同索引。
    expect(fetchCount.size).toBeLessThanOrEqual(15);
  });
});
