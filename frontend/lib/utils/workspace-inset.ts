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

/** 地图容器相对 workspace 的左内缩（px）。 */
export function mapInsetLeft(leftPanelOpen: boolean, sidebarWidth: number): number {
  return leftPanelOpen ? RAIL_W + sidebarWidth + PANEL_GAP : 0;
}

/** 地图内部左锚定 chrome（专题图例列）的避让（px）：展开时容器已避开
 * rail+面板，只需呼吸间距；收起时容器从 0 起、需避开 rail。 */
export function mapChromeLeft(leftPanelOpen: boolean): number {
  return leftPanelOpen ? 16 : RAIL_W + PANEL_GAP;
}
