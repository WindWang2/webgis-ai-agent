/**
 * Frontend map-product finalizer（ADR-0081）—— 视口校验/修复契约。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  checkViewport,
  finalizationUserNotice,
  isRepairableBbox,
  viewportIntersectsBbox,
} from './finalizer';

function mockMap(view: { w: number; s: number; e: number; n: number }) {
  const onceHandlers: Record<string, (() => void)[]> = {};
  const map = {
    getBounds: () => ({
      getWest: () => view.w,
      getSouth: () => view.s,
      getEast: () => view.e,
      getNorth: () => view.n,
    }),
    fitBounds: vi.fn(() => {
      // 立即结算（runCameraCommand 等 moveend —— mock 同步触发）
      setTimeout(() => onceHandlers['moveend']?.forEach((h) => h()), 0);
    }),
    once: vi.fn((ev: string, handler: () => void) => {
      (onceHandlers[ev] ??= []).push(handler);
    }),
    stop: vi.fn(),
    getCenter: () => ({ lng: 104.5, lat: 30.5 }),
    getZoom: () => 10,
    getBearing: () => 0,
    getPitch: () => 0,
  };
  return map as any;
}

describe('isRepairableBbox', () => {
  it('接受有序 4 元 bbox', () => {
    expect(isRepairableBbox([104, 30, 105, 31])).toBe(true);
  });
  it('拒绝畸形输入（长度/NaN/经纬倒置）', () => {
    expect(isRepairableBbox([104, 30, 105])).toBe(false);
    expect(isRepairableBbox([104, 30, 105, NaN])).toBe(false);
    expect(isRepairableBbox([105, 30, 104, 31])).toBe(false);
    expect(isRepairableBbox(null)).toBe(false);
    expect(isRepairableBbox(undefined)).toBe(false);
  });
  it('接受退化 bbox（单点 —— 由 navigation 的 minSpan 拓宽）', () => {
    expect(isRepairableBbox([104, 30, 104, 30])).toBe(true);
  });
});

describe('viewportIntersectsBbox', () => {
  const view = { getWest: () => 104, getSouth: () => 30, getEast: () => 106, getNorth: () => 32 };
  it('相交', () => {
    expect(viewportIntersectsBbox(view, [105, 31, 107, 33])).toBe(true);
  });
  it('完全在视野外（东/北方向）', () => {
    expect(viewportIntersectsBbox(view, [110, 35, 112, 37])).toBe(false);
  });
});

describe('checkViewport — 纯校验（map_finalization 命令的消费契约）', () => {
  it('视口与结果相交 → valid（命令侧不动相机）', () => {
    const map = mockMap({ w: 104, s: 30, e: 106, n: 32 });
    expect(checkViewport(map, [104.5, 30.5, 105.5, 31.5])).toBe('valid');
  });

  it('结果在视野外 → repairable（命令侧经 runCameraCommand fitBounds 一次）', () => {
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    expect(checkViewport(map, [104, 30, 105, 31])).toBe('repairable');
  });

  it('无 bbox（空结果）→ not_applicable（绝不 fit 到空集）', () => {
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    expect(checkViewport(map, null)).toBe('not_applicable');
    expect(checkViewport(map, [])).toBe('not_applicable');
  });

  it('地图未就绪（getBounds throw）→ invalid 不修复', () => {
    const map = { getBounds: () => { throw new Error('not loaded'); } } as any;
    expect(checkViewport(map, [104, 30, 105, 31])).toBe('invalid');
  });
});

describe('map_finalization 命令 — viewCommands 集成（review P2 覆盖缺口）', () => {
  it('requiredParams：status 缺席拒绝、在场接受', async () => {
    const { viewCommands } = await import('@/lib/map-commands/viewCommands');
    expect(viewCommands['map_finalization'].requiredParams({})).toBe(false);
    expect(viewCommands['map_finalization'].requiredParams({ status: 'complete' })).toBe(true);
  });

  it('视口相交 → 立即 succeeded，不触发相机命令', async () => {
    const { viewCommands } = await import('@/lib/map-commands/viewCommands');
    const map = mockMap({ w: 104, s: 30, e: 106, n: 32 });
    const result = await viewCommands['map_finalization'].run({
      map,
      params: { status: 'complete', bbox: [104.5, 30.5, 105.5, 31.5] },
    } as any);
    expect(result).toMatchObject({ status: 'succeeded' });
    expect((result as any).result).toMatchObject({ viewport: 'valid', repaired: false });
    expect(map.fitBounds).not.toHaveBeenCalled();
  });

  it('无 bbox → not_applicable 立即结算（不空转队列）', async () => {
    const { viewCommands } = await import('@/lib/map-commands/viewCommands');
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    const result = await viewCommands['map_finalization'].run({
      map,
      params: { status: 'pending' },
    } as any);
    expect(result).toMatchObject({
      status: 'succeeded',
      result: { viewport: 'not_applicable', repaired: false },
    });
  });

  it('视野外 → fitBounds 经 runCameraCommand 执行（moveend 结算）', async () => {
    const { viewCommands } = await import('@/lib/map-commands/viewCommands');
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    const resultPromise = viewCommands['map_finalization'].run({
      map,
      params: { status: 'complete', bbox: [104, 30, 105, 31] },
    } as any);
    // navigation.fitBounds 会动画 —— mock 触发 moveend 一次（runCameraCommand
    // 在其上结算）
    setTimeout(() => {
      (map as any)._fireMoveEnd?.();
    }, 10);
    const result = await resultPromise;
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    expect(map.fitBounds).toHaveBeenCalledWith(
      [104, 30, 105, 31],
      expect.objectContaining({ padding: 80, maxZoom: 16 }),
    );
    expect(result).toMatchObject({ status: 'succeeded' });
  });

  it('手势等待期间用户已移到结果区 → execute 内重评，不再抢相机（review D-1）', async () => {
    const { viewCommands } = await import('@/lib/map-commands/viewCommands');
    // getBounds 第 1 次（命令入口校验）：视野外 → repairable；
    // 第 2 次（execute 内重评，手势等待之后）：用户已把地图拖到结果区 →
    // valid → 跳过 fitBounds（不得在用户刚拖完后 yank 相机）。
    let boundsCalls = 0;
    const onceHandlers: Record<string, (() => void)[]> = {};
    const map = {
      getBounds: () => {
        boundsCalls += 1;
        const v = boundsCalls === 1
          ? { w: 0, s: 0, e: 1, n: 1 }
          : { w: 104, s: 30, e: 106, n: 32 };
        return {
          getWest: () => v.w,
          getSouth: () => v.s,
          getEast: () => v.e,
          getNorth: () => v.n,
        };
      },
      fitBounds: vi.fn(),
      once: vi.fn((ev: string, handler: () => void) => {
        (onceHandlers[ev] ??= []).push(handler);
      }),
      stop: vi.fn(),
      getCenter: () => ({ lng: 104.5, lat: 30.5 }),
      getZoom: () => 10,
      getBearing: () => 0,
      getPitch: () => 0,
    } as any;
    const resultPromise = viewCommands['map_finalization'].run({
      map,
      params: { status: 'complete', bbox: [104, 30, 105, 31] },
    } as any);
    // 跳过 fitBounds 时无自然 moveend —— 手动结算
    setTimeout(() => onceHandlers['moveend']?.forEach((h) => h()), 10);
    const result = await resultPromise;
    expect(boundsCalls).toBeGreaterThanOrEqual(2); // 重评确实发生
    expect(map.fitBounds).not.toHaveBeenCalled();
    expect(result).toMatchObject({ status: 'succeeded' });
  });
});

describe('finalizationUserNotice — 轻量披露', () => {
  it('完成态零噪声', () => {
    expect(finalizationUserNotice({ status: 'complete' })).toBeNull();
    expect(finalizationUserNotice({ status: 'pending' })).toBeNull();
  });
  it('异常态有明确文案', () => {
    expect(finalizationUserNotice({ status: 'needs_repair' })).toContain('需要关注');
    expect(finalizationUserNotice({ status: 'failed' })).toContain('未能完成');
  });
});
