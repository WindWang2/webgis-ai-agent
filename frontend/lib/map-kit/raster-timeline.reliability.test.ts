import { describe, it, expect, afterEach } from 'vitest';

import { createTimelineLoader } from '@/lib/map-kit/raster-timeline';

/**
 * #1746-1：预取副本的 rejection 必须被接管。
 *
 * createTimelineLoader.request 的 catch 会把 fetchSlice 失败原样 rethrow；
 * load() 主链由调用方接，但滑动窗口预取那一份（`void request(next)`）无人接
 * —— 任一预取切片拉取失败即产生 unhandled rejection（控制台无痕迹，且可能
 * 触发全局 error handler）。
 */

describe('raster-timeline 预取 rejection 接管', () => {
  const seen: Array<{ reason: unknown }> = [];
  const onUnhandled = (reason: unknown) => {
    seen.push({ reason });
  };

  afterEach(() => {
    process.off('unhandledRejection', onUnhandled);
    seen.length = 0;
  });

  it('预取切片失败不产生 unhandled rejection；主链照常把失败交给调用方', async () => {
    process.on('unhandledRejection', onUnhandled);

    const loader = createTimelineLoader({
      total: 8,
      prefetchAhead: 4,
      fetchSlice: async (i) => {
        if (i === 1) throw new Error('slice-1-boom');
        return [[i]];
      },
    });

    // 主链（index 0）成功；index 1..4 全部进入预取，其中 1 失败。
    await expect(loader.load(0)).resolves.toEqual([[0]]);
    // 让预取副本的 rejection 有机会冒泡到 Node 的 unhandled 检测点。
    await new Promise((r) => setTimeout(r, 20));

    expect(seen).toHaveLength(0);
  });

  it('主链自身失败仍然 reject（接管预取副本不能吞掉调用方的失败信号）', async () => {
    process.on('unhandledRejection', onUnhandled);

    const loader = createTimelineLoader({
      total: 8,
      prefetchAhead: 4,
      fetchSlice: async (i) => {
        throw new Error(`boom-${i}`);
      },
    });

    await expect(loader.load(0)).rejects.toThrow('boom-0');
    await new Promise((r) => setTimeout(r, 20));

    // 主链失败由调用方接住；预取的 1..4 失败被 load 内部接管。
    expect(seen).toHaveLength(0);
  });
});
