import { describe, it, expect } from 'vitest';
import { metersPerPixelAt, EARTH_CIRCUMFERENCE_M } from '@/lib/map-kit/meters-per-pixel';
import { computeScale as decorationsScale } from '@/components/map/map-decorations';
import { computeScale as chromeScale } from '@/components/map/map-spec-chrome';

/**
 * #800: meters-per-pixel 单一来源 —— MapLibre tileSize=512（bundle 内
 * `this._tileSize = 512` 常量，worldSize = tileSize·2^zoom）。旧实现按
 * 256px 瓦片推导（2^(zoom+8) / 156543.03392），所有比例尺/经纬网/查询
 * 半径恰好放大 2 倍。
 */
describe('metersPerPixelAt (#800)', () => {
  it('z0 赤道 ≈ 78271.5 m/px（世界恰为 512px 宽）', () => {
    expect(metersPerPixelAt(0, 0)).toBeCloseTo(EARTH_CIRCUMFERENCE_M / 512, 6);
    expect(metersPerPixelAt(0, 0)).toBeCloseTo(78271.517, 2);
  });

  it('纬度 cos 修正与 zoom 折半关系成立', () => {
    expect(metersPerPixelAt(1, 0)).toBeCloseTo(metersPerPixelAt(0, 0) / 2, 6);
    expect(metersPerPixelAt(0, 60)).toBeCloseTo(
      (EARTH_CIRCUMFERENCE_M / 512) * Math.cos((60 * Math.PI) / 180),
      6,
    );
  });

  it.each([
    ['decorations', decorationsScale],
    ['spec-chrome', chromeScale],
  ])('%s 比例尺条基于 512 瓦片度量', (_name, computeScale) => {
    const z0 = computeScale(0, 0);
    // 100 CSS px 的目标距离 ≈ 7827 km？否 —— target = mpp·100 ≈ 7.83e6 →
    // 候选里最接近的是 5000 km 上限；关键不变量：pixels == meters / mpp
    expect(z0.pixels).toBeCloseTo(z0.meters / metersPerPixelAt(0, 0), 6);
    const z13lat30 = computeScale(13, 30);
    expect(z13lat30.pixels).toBeCloseTo(
      z13lat30.meters / metersPerPixelAt(13, 30), 6);
  });
});
