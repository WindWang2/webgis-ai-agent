import { describe, expect, it } from 'vitest';
import { RAIL_W, PANEL_GAP, mapInsetLeft, mapChromeLeft } from './workspace-inset';

/**
 * 左栏展开时地图容器真实收缩（ContextPanel 不再悬浮遮挡地图）：
 * MapLibre 的 ResizeObserver 随容器尺寸自动 resize 并保持地理中心位于
 * 新画布中心 —— 视口按显示面积重算。这里锁定两个布局度量的契约。
 */
describe('workspace-inset', () => {
  it('mapInsetLeft: 面板展开时让出 rail+面板宽+间隙，收起时容器回到全幅', () => {
    expect(mapInsetLeft(true, 320)).toBe(RAIL_W + 320 + PANEL_GAP); // 380
    expect(mapInsetLeft(true, 420)).toBe(48 + 420 + 12); // 拖到最宽也同步收缩
    expect(mapInsetLeft(false, 320)).toBe(0); // 收起：地图全幅（rail 仍悬浮其上）
  });

  it('mapChromeLeft: 展开时容器已避开面板只需呼吸间距，收起时避开 rail', () => {
    expect(mapChromeLeft(true)).toBe(16);
    expect(mapChromeLeft(false)).toBe(RAIL_W + PANEL_GAP); // 60
  });
});
