/**
 * annotation 地理锚定 callout 的引线方向回归（P2 坐标系符号）：
 * SVG viewBox y 向下为正 —— flipY（锚点落在视口下半 → 卡片向上避让）时
 * 引线终点 y 必须小于锚点 y，否则引线恒背向卡片。象限规则与导出侧
 * drawAnchoredCalloutBox（by + (flipY ? boxH : 0)）同源。
 */
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import './annotation';
import { getComponentRenderer } from './registry';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

const BOUNDS = { west: 104, south: 30, east: 105, north: 31 };

function renderCallout(anchor: [number, number]) {
  const renderer = getComponentRenderer('annotation');
  expect(renderer).toBeTruthy();
  const component = {
    id: 'ann-callout',
    type: 'annotation',
    variant: 'callout',
    options: { text: '测试注记', anchor },
  } as unknown as MapSpecComponent;
  const { container } = render(
    <>{renderer!(component, { spec: null, zoom: 9, centerLat: 30.6, bearing: 0, bounds: BOUNDS })}</>,
  );
  const line = container.querySelector('line');
  expect(line).toBeTruthy();
  return {
    x1: Number(line!.getAttribute('x1')),
    y1: Number(line!.getAttribute('y1')),
    x2: Number(line!.getAttribute('x2')),
    y2: Number(line!.getAttribute('y2')),
  };
}

describe('AnchoredCallout 引线终点指向卡片所在侧（P2）', () => {
  it('flipY（锚点在下半 → 卡片向上）：终点 y 小于锚点 y', () => {
    // fx≈0.1（不 flipX）、fy≈0.1（flipY）
    const l = renderCallout([104.1, 30.1]);
    // 修复前：endFy = (1-fy)+0.06 → 终点落到锚点下方，引线背向卡片。
    expect(l.y2).toBeLessThan(l.y1);
  });

  it('非 flipY（锚点在上半 → 卡片向下）：终点 y 大于锚点 y', () => {
    const l = renderCallout([104.1, 30.9]);
    expect(l.y2).toBeGreaterThan(l.y1);
  });

  it('flipX（锚点在右半 → 卡片向左）：终点 x 小于锚点 x', () => {
    const l = renderCallout([104.9, 30.9]);
    expect(l.x2).toBeLessThan(l.x1);
  });
});
