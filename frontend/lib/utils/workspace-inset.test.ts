import { describe, expect, it, afterEach } from 'vitest';
import {
  RAIL_W,
  PANEL_GAP,
  MAP_INSET_MAX_VIEWPORT_RATIO,
  mapInsetLeft,
  mapChromeLeft,
} from './workspace-inset';

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

/**
 * #999（最小版小屏适配）：mapInsetLeft 的视口比例上限——桌面像素常量
 * （rail 48 + 面板 280–420 + gap 12）在窄视口上最多吃掉视口宽的 50%，
 * 保证地图在平板竖屏/手机上至少半屏可见。
 */
describe('workspace-inset — narrow viewport clamp (#999)', () => {
  const originalWidth = window.innerWidth;

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      value: originalWidth,
      configurable: true,
      writable: true,
    });
  });

  function setViewportWidth(px: number) {
    Object.defineProperty(window, 'innerWidth', {
      value: px,
      configurable: true,
      writable: true,
    });
  }

  it('窄视口：展开面板时 inset 被钳到视口宽 × 50%', () => {
    setViewportWidth(600);
    expect(mapInsetLeft(true, 330)).toBe(600 * MAP_INSET_MAX_VIEWPORT_RATIO); // 300
    // 拖到最宽（480px inset）也一样被钳住
    expect(mapInsetLeft(true, 420)).toBe(300);
  });

  it('宽视口：桌面像素常量原样生效（不受上限影响）', () => {
    setViewportWidth(1440);
    expect(mapInsetLeft(true, 330)).toBe(RAIL_W + 330 + PANEL_GAP);
    expect(mapInsetLeft(true, 420)).toBe(RAIL_W + 420 + PANEL_GAP);
  });

  it('收起时恒为 0（窄视口也一样）', () => {
    setViewportWidth(375);
    expect(mapInsetLeft(false, 420)).toBe(0);
  });
});
