/**
 * AC-07（ADR-0156）版面数学模块单测：数字比例尺（纬度修正）/ 图廓注记
 * 格式自适应 / 经纬网密度自适应 / 磁偏角近似。
 */
import { describe, expect, it } from 'vitest';

import { EARTH_CIRCUMFERENCE_M } from '@/lib/map-kit/meters-per-pixel';
import {
  PX_PHYSICAL_METERS_96DPI,
  numericScaleAt,
  scaleDisplayMode,
} from './numeric-scale';
import {
  formatGraticuleLabel,
  graticuleLabelFormatForSpan,
  frameAnnotations,
} from './graticule-labels';
import {
  gridLineCount,
  MAX_GRID_LINES,
  MIN_GRID_LINES,
  selectGraticuleInterval,
} from './graticule-density';
import { bboxCenter, magneticDeclinationAt } from './magnetic-declination';

// ── P3 数字比例尺：赤道/中纬/高纬三档误差 ≤5%（对照闭式理论）────────────

const PX = PX_PHYSICAL_METERS_96DPI;

/** 理论闭式：N = C·cos(φ) / (512·2^z · px_physical_meters)。 */
function theoryRatio(zoom: number, lat: number): number {
  const mpp = (EARTH_CIRCUMFERENCE_M * Math.cos((lat * Math.PI) / 180))
    / (512 * Math.pow(2, zoom));
  return mpp / PX;
}

describe.each([
  ['equator', 10, 0],
  ['mid-latitude', 10, 39.9],   // 北京
  ['high-latitude', 10, 71.0],  // 特罗姆瑟
])('numeric scale at %s (z=%s, lat=%s)', (_name, zoom, lat) => {
  it('误差 ≤5%（对照理论闭式）', () => {
    const { ratio } = numericScaleAt(zoom, lat);
    const theory = theoryRatio(zoom, lat);
    // 3 位有效数字取整引入的偏差远小于 5%
    expect(Math.abs(ratio - theory) / theory).toBeLessThanOrEqual(0.05);
  });
});

describe('numeric scale behavior', () => {
  it('纬度修正：高纬分母显著小于赤道（cos 修正生效）', () => {
    const equator = numericScaleAt(10, 0).ratio;
    const n60 = numericScaleAt(10, 60).ratio;
    const n80 = numericScaleAt(10, 80).ratio;
    expect(n60 / equator).toBeLessThan(0.55);
    expect(n60 / equator).toBeGreaterThan(0.45); // ≈ cos(60°)=0.5
    expect(n80 / equator).toBeLessThan(0.2);     // ≈ cos(80°)=0.174
  });

  it('标签格式 1:N（千分位）', () => {
    const { label } = numericScaleAt(10, 0);
    expect(label).toMatch(/^1:[\d,]+$/);
  });

  it('scaleDisplayMode：缺省并存，可配 bar/numeric', () => {
    expect(scaleDisplayMode(undefined)).toBe('both');
    expect(scaleDisplayMode({ scaleDisplay: 'numeric' })).toBe('numeric');
    expect(scaleDisplayMode({ scaleDisplay: 'bar' })).toBe('bar');
    expect(scaleDisplayMode({ scaleDisplay: 'nonsense' })).toBe('both');
  });
});

// ── P4 图廓注记：格式随跨度三档自适应 ────────────────────────────────────

describe('graticule label format tiers', () => {
  it('跨度 ≥10° → deg', () => {
    expect(graticuleLabelFormatForSpan(25)).toBe('deg');
    expect(graticuleLabelFormatForSpan(10)).toBe('deg');
  });
  it('1° ≤ 跨度 <10° → dm', () => {
    expect(graticuleLabelFormatForSpan(9.9)).toBe('dm');
    expect(graticuleLabelFormatForSpan(1)).toBe('dm');
  });
  it('跨度 <1° → dms', () => {
    expect(graticuleLabelFormatForSpan(0.9)).toBe('dms');
    expect(graticuleLabelFormatForSpan(0.01)).toBe('dms');
  });

  it('formatGraticuleLabel 三档输出（含半球后缀）', () => {
    expect(formatGraticuleLabel(116.4, 'deg', false)).toBe('116°E');
    expect(formatGraticuleLabel(116.4, 'deg', true)).toBe('116°N');
    expect(formatGraticuleLabel(116.4, 'dm', false)).toBe('116°24′E');
    expect(formatGraticuleLabel(-39.9, 'dms', true)).toBe('39°54′00″S');
    expect(formatGraticuleLabel(8.15, 'dms', false)).toBe('008°09′00″E');
    expect(formatGraticuleLabel(-39.9, 'deg', true)).toBe('39°S');
  });

  it('frameAnnotations：四角坐标齐备', () => {
    const frame = frameAnnotations(
      { west: 115, south: 39, east: 117, north: 41 },
      'deg',
    );
    expect(frame).toHaveLength(4);
    const positions = frame.map((f) => f.position).sort();
    expect(positions).toEqual(['ne', 'nw', 'se', 'sw']);
    const nw = frame.find((f) => f.position === 'nw')!;
    expect(nw.lngLabel).toBe('115°E');
    expect(nw.latLabel).toBe('41°N');
  });
});

// ── P5 经纬网密度：线数恒落 [3,10] ───────────────────────────────────────

describe('graticule density', () => {
  it.each([
    [0.05, 0.04],   // 街区
    [0.3, 0.2],     // 城区
    [2, 1.5],       // 城市
    [10, 8],        // 都市区
    [60, 40],       // 省域
    [120, 60],      // 国家
  ])('跨度 %s×%s → 两方向线数均落 [3,10]', (lngSpan, latSpan) => {
    const choice = selectGraticuleInterval(lngSpan, latSpan);
    expect(choice.source).toBe('adaptive');
    expect(choice.lineCountLng).toBeGreaterThanOrEqual(MIN_GRID_LINES);
    expect(choice.lineCountLng).toBeLessThanOrEqual(MAX_GRID_LINES);
    expect(choice.lineCountLat).toBeGreaterThanOrEqual(MIN_GRID_LINES);
    expect(choice.lineCountLat).toBeLessThanOrEqual(MAX_GRID_LINES);
  });

  it('极端大跨度（全球）取最粗档如实出超（不虚构、有界披露）', () => {
    const choice = selectGraticuleInterval(360, 180);
    expect(choice.intervalDeg).toBe(30); // 最粗档
    expect(choice.lineCountLng).toBe(13);
    expect(choice.lineCountLat).toBe(7);
  });

  it('显式覆盖优先（用户/spec 指定 interval）', () => {
    const choice = selectGraticuleInterval(10, 8, { explicitIntervalDeg: 2 });
    expect(choice.source).toBe('explicit');
    expect(choice.intervalDeg).toBe(2);
    expect(choice.lineCountLng).toBe(6); // floor(10/2)+1
  });

  it('无 bounds 回退 zoom 表', () => {
    const choice = selectGraticuleInterval(0, 0, { zoom: 4 });
    expect(choice.source).toBe('zoom');
    expect(choice.intervalDeg).toBeGreaterThan(0);
  });

  it('gridLineCount 口径与 snapGraticuleLines 一致（含端点线）', () => {
    expect(gridLineCount(10, 2)).toBe(6);
    expect(gridLineCount(9.9, 2)).toBe(5);
  });
});

// ── P4 磁偏角近似 ────────────────────────────────────────────────────────

describe('magnetic declination (dipole approximation)', () => {
  it('恒标 approximate', () => {
    const d = magneticDeclinationAt(39.9, 116.4);
    expect(d.approximate).toBe(true);
    expect(d.label).toMatch(/approximate/);
  });

  it('输出归一 (-180,180]', () => {
    for (const [lat, lng] of [[0, 0], [39.9, 116.4], [-33.9, 18.4], [61, 25], [-90, 0], [90, 120]]) {
      const d = magneticDeclinationAt(lat, lng);
      expect(d.degrees).toBeGreaterThan(-180);
      expect(d.degrees).toBeLessThanOrEqual(180);
    }
  });

  it('区域合理性：东亚偏西、北大西洋西侧偏西、北欧偏东（偶极子量级）', () => {
    const beijing = magneticDeclinationAt(39.9, 116.4).degrees;   // WMM ~ -7°
    const newYork = magneticDeclinationAt(40.7, -74).degrees;     // WMM ~ -13°
    const helsinki = magneticDeclinationAt(61, 25).degrees;       // WMM ~ +10°
    expect(beijing).toBeLessThan(0);
    expect(beijing).toBeGreaterThan(-20);
    expect(newYork).toBeLessThan(0);
    expect(newYork).toBeGreaterThan(-25);
    expect(helsinki).toBeGreaterThan(0);
    expect(helsinki).toBeLessThan(25);
  });

  it('bboxCenter：算术均值', () => {
    const c = bboxCenter({ west: 100, south: 30, east: 120, north: 50 });
    expect(c).toEqual({ lat: 40, lng: 110 });
  });

  it('非法输入降级 0°（不虚构）', () => {
    const d = magneticDeclinationAt(Number.NaN, 0);
    expect(d.degrees).toBe(0);
    expect(d.approximate).toBe(true);
  });
});
