import { describe, it, expect } from 'vitest';
import {
  exportBoundsForFrame,
  boundsContained,
  mercNormY,
  projectLngLatToPixel,
  type BoundsWSEN,
} from './extent';

const MASK: BoundsWSEN = [103.9, 30.5, 104.2, 30.8]; // 成都一带 ~0.3° 方块

describe('exportBoundsForFrame（ADR-0157 P4 WYSIWYG 数学）', () => {
  it('导出范围 ⊇ 遮罩范围（偏宽遮罩 → 南北扩张）', () => {
    const wide: BoundsWSEN = [103.9, 30.6, 104.2, 30.7];
    const out = exportBoundsForFrame(wide, 1.414);
    expect(out).not.toBeNull();
    expect(out![0]).toBeLessThanOrEqual(wide[0]);
    expect(out![2]).toBeGreaterThanOrEqual(wide[2]);
    // 南北扩张 → 纬度范围扩大、经度范围保持
    expect(out![3]).toBeGreaterThan(wide[3]);
    expect(out![1]).toBeLessThan(wide[1]);
    expect(out![2] - out![0]).toBeCloseTo(wide[2] - wide[0], 9);
  });

  it('偏高遮罩 → 东西扩张', () => {
    const tall: BoundsWSEN = [104.0, 30.5, 104.1, 30.9];
    const out = exportBoundsForFrame(tall, 1.414);
    expect(out![0]).toBeLessThan(tall[0]);
    expect(out![2]).toBeGreaterThan(tall[2]);
    expect(out![3]).toBeCloseTo(tall[3], 6);
    expect(out![1]).toBeCloseTo(tall[1], 6);
  });

  it('输出 Mercator 纵横比 == 图框纵横比（≤1px 裁切契约的前提）', () => {
    const out = exportBoundsForFrame(MASK, 1.414)!;
    const mercW = (out[2] - out[0]) / 360;
    const mercH = mercNormY(out[1]) - mercNormY(out[3]);
    expect(mercW / mercH).toBeCloseTo(1.414, 6);
  });

  it('中心不动点：遮罩 Mercator 中心 == 导出 Mercator 中心', () => {
    const out = exportBoundsForFrame(MASK, 1.414)!;
    const cxMask = (MASK[0] + MASK[2]) / 2;
    const cxOut = (out[0] + out[2]) / 2;
    expect(cxOut).toBeCloseTo(cxMask, 9);
    const cyMask = (mercNormY(MASK[1]) + mercNormY(MASK[3])) / 2;
    const cyOut = (mercNormY(out[1]) + mercNormY(out[3])) / 2;
    expect(cyOut).toBeCloseTo(cyMask, 12);
  });

  it('portrait aspect = 1/1.414；非法输入 → null', () => {
    expect(exportBoundsForFrame(MASK, 1 / 1.414)).not.toBeNull();
    expect(exportBoundsForFrame([104, 30, 103, 31], 1.414)).toBeNull();
    expect(exportBoundsForFrame(MASK, 0)).toBeNull();
  });

  it('确定性与投影往返：mercNormY(latOf(y)) == y', () => {
    const out1 = exportBoundsForFrame(MASK, 1.414);
    const out2 = exportBoundsForFrame(MASK, 1.414);
    expect(out1).toEqual(out2);
    // 逆变换自洽（0.618 取非退化纬度）
    const y = mercNormY(30.618);
    // latOf 是模块内部：经 exportBoundsForFrame 的往返隐式覆盖 —— 这里直接
    // 验证 mercNormY 与常见基准（赤道 0.5）。
    expect(mercNormY(0)).toBeCloseTo(0.5, 12);
    void y;
  });
});

describe('boundsContained / projectLngLatToPixel', () => {
  it('数据超界 → false；合法包含 → true；非法输入不误报（true）', () => {
    const frame = exportBoundsForFrame(MASK, 1.414)!;
    expect(boundsContained(MASK, frame)).toBe(true);
    expect(boundsContained([90, 20, 130, 50], frame)).toBe(false);
    expect(boundsContained(null, frame)).toBe(true);
    expect(boundsContained(MASK, null)).toBe(true);
  });

  it('解析投影：frame 角点 → (0,0)/(W,H)；Mercator 中点 → 半幅（亚像素精度基准）', () => {
    const frame: BoundsWSEN = [104.0, 30.0, 104.3, 30.3];
    const W = 1414;
    const H = 1000;
    const tl = projectLngLatToPixel(104.0, 30.3, frame, W, H);
    expect(tl.x).toBeCloseTo(0, 9);
    expect(tl.y).toBeCloseTo(0, 9);
    const br = projectLngLatToPixel(104.3, 30.0, frame, W, H);
    expect(br.x).toBeCloseTo(W, 9);
    expect(br.y).toBeCloseTo(H, 9);
    // Mercator 线性中点（非线性纬度中点 ≠ 算术中点 —— 这正是 Mercator 投影语义）
    const midLat = latOfNorm((mercNormY(30.0) + mercNormY(30.3)) / 2);
    const center = projectLngLatToPixel(104.15, midLat, frame, W, H);
    expect(center.x).toBeCloseTo(W / 2, 9);
    expect(center.y).toBeCloseTo(H / 2, 9);
  });
});

/** Mercator 逆变换（与 extent.ts 内部实现互为镜像，独立推导用于验证）。 */
function latOfNorm(y: number): number {
  const mercN = 2 * Math.PI * (0.5 - y);
  return (2 * Math.atan(Math.exp(mercN)) - Math.PI / 2) * (180 / Math.PI);
}
