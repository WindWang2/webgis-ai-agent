/**
 * Workspace 布局度量：地图容器与左锚定 chrome 避让左栏的统一计算。
 *
 * ContextPanel（fixed 悬浮卡片）展开时，地图容器不再被遮挡而是真实收缩
 * （page.tsx 的 wrapper 按下述 inset 左移）——MapLibre 的 ResizeObserver
 * 会在容器尺寸变化时自动 resize+redraw，并把地理中心保持在收缩后画布的
 * 中心，即「按地图显示面积重新计算中心」；收起时容器回到全幅。
 */
export const RAIL_W = 48;
export const PANEL_GAP = 12;

/**
 * #999（最小版）：地图容器左内缩的视口比例上限。桌面像素常量
 * （rail 48 + 面板 280–420 + gap 12）在窄视口（平板竖屏/手机）上会吃掉
 * 大半个屏宽、地图不可见；inset 最多占据视口宽的 50%，保证地图始终
 * 至少半屏可见。SSR（无 window）不下限，客户端首帧后生效。
 */
export const MAP_INSET_MAX_VIEWPORT_RATIO = 0.5;

/** 地图容器相对 workspace 的左内缩（px）。 */
export function mapInsetLeft(leftPanelOpen: boolean, sidebarWidth: number): number {
  if (!leftPanelOpen) return 0;
  const inset = RAIL_W + sidebarWidth + PANEL_GAP;
  if (typeof window === 'undefined') return inset;
  return Math.min(inset, window.innerWidth * MAP_INSET_MAX_VIEWPORT_RATIO);
}

/** 地图内部左锚定 chrome（专题图例列）的避让（px）：展开时容器已避开
 * rail+面板，只需呼吸间距；收起时容器从 0 起、需避开 rail。 */
export function mapChromeLeft(leftPanelOpen: boolean): number {
  return leftPanelOpen ? 16 : RAIL_W + PANEL_GAP;
}
