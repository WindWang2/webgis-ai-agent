/**
 * tile-zoom 折中规划器测试（V11 W6.3，ADR-0166）。
 */
import { describe, it, expect } from 'vitest';
import { planTileZoomCapture } from './tile-zoom-plan';

describe('高 DPI 栅格取图规划（W6.3 折中）', () => {
  it('300 DPI 富余 headroom：增益 = log2(ratio)，封顶于 maxZoom', () => {
    const plan = planTileZoomCapture(12, 300, 19);
    expect(plan.zoomGainLevels).toBeCloseTo(Math.log2(300 / 96), 3); // ≈1.644
    expect(plan.captureZoom).toBeCloseTo(12 + Math.log2(300 / 96), 3);
    expect(plan.resampleFactor).toBeCloseTo(300 / 96, 3);
    expect(plan.disclosure).toContain('重采样');
  });

  it('maxZoom 封顶：headroom 不足时增益受限且披露级数', () => {
    const plan = planTileZoomCapture(18.5, 300, 19);
    expect(plan.zoomGainLevels).toBeCloseTo(0.5, 3);
    expect(plan.captureZoom).toBe(19);
  });

  it('无 headroom：增益 0 且如实披露「与 V10 等同」（不静默）', () => {
    const plan = planTileZoomCapture(19, 300, 19);
    expect(plan.zoomGainLevels).toBe(0);
    expect(plan.disclosure).toContain('无 headroom');
    expect(plan.disclosure).toContain('V10');
  });

  it('dpi ≤ 96：ratio 钳为 1（无增益，确定性）', () => {
    const a = planTileZoomCapture(10, 96, 19);
    expect(a.resampleFactor).toBe(1);
    expect(a.zoomGainLevels).toBe(0);
    expect(planTileZoomCapture(10, 96, 19)).toEqual(a);
  });
});
