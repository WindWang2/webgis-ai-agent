/**
 * G47 图面几何纯函数：等比 contain / 命中 / 键盘顺序 / 面积比自检。
 *
 * 这些断言是"面积∝数量"读图基础的前端半边：只要 fit 变成非等比，
 * 圆就会变椭圆，测试必须先失败。
 */
import { describe, it, expect } from 'vitest';
import {
  areaRatioError, fitContain, hitTest, nextFeatureId, ringPath, toDiagramPoint,
} from './geometry';

describe('fitContain', () => {
  it('宽容器：等比缩放并按高度居中（不留横向变形）', () => {
    const fit = fitContain(1000, { width: 800, height: 400 });
    expect(fit.scale).toBeCloseTo(0.4, 10);
    expect(fit.offsetX).toBeCloseTo((800 - 400) / 2, 10);
    expect(fit.offsetY).toBeCloseTo(0, 10);
  });

  it('高容器：等比缩放并按宽度居中', () => {
    const fit = fitContain(1000, { width: 300, height: 900 });
    expect(fit.scale).toBeCloseTo(0.3, 10);
    expect(fit.offsetY).toBeCloseTo((900 - 300) / 2, 10);
  });

  it('x/y 用同一个 scale（否则圆会变椭圆）', () => {
    const fit = fitContain(1000, { width: 640, height: 480 });
    const pxPerUnit = fit.scale;
    expect(pxPerUnit).toBeCloseTo(0.48, 10);
  });

  it('非法 extent/box 回退到安全值而不是 NaN', () => {
    const fit = fitContain(Number.NaN, { width: 0, height: 0 });
    expect(Number.isFinite(fit.scale)).toBe(true);
    expect(fit.scale).toBeGreaterThan(0);
  });
});

describe('toDiagramPoint + hitTest', () => {
  const box = { width: 500, height: 500 };
  it('像素 → 示意坐标再命中真实圆', () => {
    const p = toDiagramPoint(250, 250, 1000, box);
    expect(p.x).toBeCloseTo(500, 6);
    expect(p.y).toBeCloseTo(500, 6);
    expect(hitTest([{ feature_id: 'a', x: 500, y: 500, radius: 50 }], p.x, p.y)).toBe('a');
  });

  it('重叠时选较小圆（小圆在上）', () => {
    const id = hitTest([
      { feature_id: 'big', x: 500, y: 500, radius: 100 },
      { feature_id: 'small', x: 500, y: 500, radius: 10 },
    ], 505, 500);
    expect(id).toBe('small');
  });

  it('圆外返回 null', () => {
    expect(hitTest([{ feature_id: 'a', x: 10, y: 10, radius: 5 }], 100, 100)).toBeNull();
  });
});

describe('nextFeatureId', () => {
  const ids = ['c', 'a', 'b'];
  it('按稳定 id 排序循环，不按布局坐标', () => {
    expect(nextFeatureId(ids, null, 1)).toBe('a');
    expect(nextFeatureId(ids, 'a', 1)).toBe('b');
    expect(nextFeatureId(ids, 'c', 1)).toBe('a');
    expect(nextFeatureId(ids, 'a', -1)).toBe('c');
  });
  it('空集合返回 null', () => {
    expect(nextFeatureId([], 'a', 1)).toBeNull();
  });
});

describe('areaRatioError', () => {
  it('1:4 数量 + 1:2 半径 → 误差为 0', () => {
    expect(areaRatioError([
      { value: 1, radius: 10 },
      { value: 4, radius: 20 },
    ])).toBeCloseTo(0, 12);
  });
  it('按半径线性编码会被检出（错误的比例）', () => {
    expect(areaRatioError([
      { value: 1, radius: 10 },
      { value: 4, radius: 40 },
    ])).toBeGreaterThan(0.5);
  });
});

describe('ringPath', () => {
  it('闭合 path', () => {
    expect(ringPath([[0, 0], [10, 0], [10, 10], [0, 0]]))
      .toBe('M 0 0 L 10 0 L 10 10 L 0 0 Z');
  });
  it('点数不足返回空串', () => {
    expect(ringPath([[0, 0], [1, 1]])).toBe('');
  });
});
