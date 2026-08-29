/**
 * Frontend map-product finalizer（ADR-0081）—— 视口校验/修复契约。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  finalizeViewport,
  finalizationUserNotice,
  isRepairableBbox,
  viewportIntersectsBbox,
} from './finalizer';

function mockMap(view: { w: number; s: number; e: number; n: number }, fitBoundsImpl?: (bbox: unknown, pad?: number) => void) {
  return {
    getBounds: () => ({
      getWest: () => view.w,
      getSouth: () => view.s,
      getEast: () => view.e,
      getNorth: () => view.n,
    }),
    fitBounds: vi.fn(fitBoundsImpl ?? (() => {})),
  } as any;
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

describe('finalizeViewport — 校验 + 有界修复', () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('视口与结果相交 → valid，不动相机', () => {
    const map = mockMap({ w: 104, s: 30, e: 106, n: 32 });
    const out = finalizeViewport(map, [104.5, 30.5, 105.5, 31.5]);
    expect(out).toEqual({ check: 'valid', repaired: false });
    expect(map.fitBounds).not.toHaveBeenCalled();
  });

  it('结果在视野外 → fitBounds 修复一次', () => {
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    const out = finalizeViewport(map, [104, 30, 105, 31]);
    expect(out).toEqual({ check: 'repairable', repaired: true });
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    // navigation.fitBounds 的真实调用形态：bbox + options（padding/maxZoom/时长）
    expect(map.fitBounds).toHaveBeenCalledWith(
      [104, 30, 105, 31],
      expect.objectContaining({ padding: 80, maxZoom: 16 }),
    );
  });

  it('无 bbox（空结果）→ not_applicable，绝不 fit 到空集', () => {
    const map = mockMap({ w: 0, s: 0, e: 1, n: 1 });
    expect(finalizeViewport(map, null)).toEqual({ check: 'not_applicable', repaired: false });
    expect(finalizeViewport(map, [])).toEqual({ check: 'not_applicable', repaired: false });
    expect(map.fitBounds).not.toHaveBeenCalled();
  });

  it('地图未就绪（getBounds throw）→ invalid 不修复', () => {
    const map = { getBounds: () => { throw new Error('not loaded'); }, fitBounds: vi.fn() } as any;
    expect(finalizeViewport(map, [104, 30, 105, 31])).toEqual({ check: 'invalid', repaired: false });
    expect(map.fitBounds).not.toHaveBeenCalled();
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
