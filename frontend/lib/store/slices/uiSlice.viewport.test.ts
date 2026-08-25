import { describe, expect, it, afterEach } from 'vitest';
import {
  LEFT_PANEL_MIN_VIEWPORT_PX,
  defaultLeftPanelOpen,
} from './uiSlice';

/**
 * #999（最小版小屏适配）：窄视口（<768px）初始收起 ContextPanel——主工作台
 * 布局是桌面像素常量，平板竖屏/手机上展开的左栏会占满视口。仅初始态，
 * 用户随后的开合不受影响。
 */
describe('defaultLeftPanelOpen (#999)', () => {
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

  it('桌面/宽视口（≥768px）：默认展开（现状不变）', () => {
    setViewportWidth(1024);
    expect(defaultLeftPanelOpen()).toBe(true);
    setViewportWidth(LEFT_PANEL_MIN_VIEWPORT_PX);
    expect(defaultLeftPanelOpen()).toBe(true);
  });

  it('窄视口（<768px）：默认收起，地图与聊天不再互挤', () => {
    setViewportWidth(767);
    expect(defaultLeftPanelOpen()).toBe(false);
    setViewportWidth(375);
    expect(defaultLeftPanelOpen()).toBe(false);
  });
});
