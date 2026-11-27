/**
 * oversample：boost 公式的数值健壮性（spec #268）。NaN/负 dpi 不得穿透
 * clamp —— 否则 z=NaN、瓦片网格为空、底图静默消失。
 */
import { describe, expect, it } from 'vitest';
import {
  computeOversampleBoost,
  getOversampledZoom,
  resolveOversampledTileGrid,
} from './oversample';

const TEMPLATE = 'https://tiles.test/{z}/{x}/{y}.png';

describe('computeOversampleBoost', () => {
  it('boost = log2(dpi/96) clamped to [0,2]', () => {
    expect(computeOversampleBoost(96)).toBe(0);
    expect(computeOversampleBoost(192)).toBe(1);
    expect(computeOversampleBoost(300)).toBe(2);
    expect(computeOversampleBoost(600)).toBe(2);
    expect(computeOversampleBoost(48)).toBe(0);
  });

  it('NaN / negative dpi falls back to no boost instead of NaN z', () => {
    // 旧实现 Math.min(2, Math.max(0, Math.round(Math.log2(dpi/96)))) 对
    // NaN 输入产出 NaN（min/max 不拦截 NaN）→ z=NaN → 网格为空。
    expect(computeOversampleBoost(Number.NaN)).toBe(0);
    expect(computeOversampleBoost(-100)).toBe(0);
    expect(getOversampledZoom(10, Number.NaN)).toBe(10);
    expect(Number.isFinite(getOversampledZoom(10, -1))).toBe(true);
  });
});

describe('resolveOversampledTileGrid', () => {
  it('produces a finite non-empty grid for a NaN targetDpi (basemap must not vanish)', () => {
    const items = resolveOversampledTileGrid({
      bounds: [116, 39, 117, 40],
      width: 800,
      height: 600,
      padding: 0,
      targetDpi: Number.NaN,
      tileUrlTemplate: TEMPLATE,
      baseZoom: 10,
    });
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) {
      expect(Number.isFinite(item.z)).toBe(true);
      expect(Number.isFinite(item.x)).toBe(true);
      expect(Number.isFinite(item.y)).toBe(true);
    }
  });

  it('clamps z into [0,19] with a template grid', () => {
    const items = resolveOversampledTileGrid({
      bounds: [116, 39, 117, 40],
      width: 800,
      height: 600,
      padding: 0,
      targetDpi: 300,
      tileUrlTemplate: TEMPLATE,
      baseZoom: 19,
    });
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) expect(item.z).toBeLessThanOrEqual(19);
  });
});
