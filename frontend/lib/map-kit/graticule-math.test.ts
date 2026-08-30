import { describe, expect, it } from 'vitest';
import {
  GRATICULE_INTERVALS,
  graticuleIntervalForZoom,
  graticuleLngLines,
  graticuleLatLines,
  snapGraticuleLines,
} from './graticule-math';

/**
 * P3 经纬网共享数学契约：live 渲染器与导出 _drawGraticules 单一语义源 ——
 * 间隔表 / zoom 映射 / 吸附 / 标签格式锁定（改导出侧必须同步 live）。
 */
describe('graticule-math（live ↔ export 单一语义源）', () => {
  it('interval table matches the export-side table', () => {
    expect(GRATICULE_INTERVALS).toEqual([
      30, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01,
    ]);
  });

  it('zoom → interval mapping (floor((zoom-1)/2), clamped)', () => {
    expect(graticuleIntervalForZoom(1)).toBe(30);
    expect(graticuleIntervalForZoom(2)).toBe(30);
    expect(graticuleIntervalForZoom(3)).toBe(20);
    expect(graticuleIntervalForZoom(9)).toBe(2); // floor(8/2)=4
    expect(graticuleIntervalForZoom(11)).toBe(1); // floor(10/2)=5
    expect(graticuleIntervalForZoom(30)).toBe(0.01); // clamp high
    expect(graticuleIntervalForZoom(-5)).toBe(30); // clamp low
    expect(graticuleIntervalForZoom(NaN)).toBe(0.01);
  });

  it('snaps lines to the interval grid within range (and no float drift loss)', () => {
    // start = floor(103.95/0.1)*0.1 = 103.9 → 103.9 / 104 / 104.1 都在界内
    const lines = snapGraticuleLines(103.95, 104.15, 0.1);
    expect(lines).toEqual([103.9, 104, 104.1]);
    // 前导边界恰好命中时包含
    expect(snapGraticuleLines(104, 104.5, 0.5)).toEqual([104, 104.5]);
  });

  it('degenerate ranges produce no lines (bounded, no infinite loops)', () => {
    expect(snapGraticuleLines(10, 5, 1)).toEqual([]);
    expect(snapGraticuleLines(0, 1, 0)).toEqual([]);
    expect(snapGraticuleLines(0, 1, NaN)).toEqual([]);
  });

  it('lng lines carry E/W labels and viewport fractions', () => {
    const lines = graticuleLngLines(103.9, 104.3, 0.2);
    // interval<1 → 1 位小数（与导出侧 toFixed(1) 同格式）
    expect(lines.map((l) => l.label)).toEqual(['103.8°E', '104.0°E', '104.2°E']);
    const first = lines[0];
    expect(first.fraction).toBeCloseTo((103.8 - 103.9) / 0.4, 6);
  });

  it('lat lines carry N/S labels (negative south)', () => {
    const lines = graticuleLatLines(-30.5, -29.8, 0.5);
    expect(lines.map((l) => l.label)).toEqual(['30.5°S', '30.0°S']);
  });
});
