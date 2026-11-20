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

/** CSS clamp keeps SSR/client attributes identical and responds to viewport resizing. */
export function mapInsetLeftStyle(leftPanelOpen: boolean, sidebarWidth: number): string {
  if (!leftPanelOpen) return '0px';
  return `min(${RAIL_W + sidebarWidth + PANEL_GAP}px, ${MAP_INSET_MAX_VIEWPORT_RATIO * 100}vw)`;
}

/** 地图内部左锚定 chrome（专题图例列、情境条、视觉修复入口）的避让（px）：
 * 面板推挤地图时容器已避开 rail+面板，只需呼吸间距；否则容器从 0 起、
 * 需避开竖排 rail。mobile 档 rail 在底部（railVisible=false），只留间距。 */
export function mapChromeLeft(
  leftPanelOpen: boolean,
  { railVisible = true }: { railVisible?: boolean } = {},
): number {
  if (leftPanelOpen) return 16;
  return railVisible ? RAIL_W + PANEL_GAP : PANEL_GAP;
}

/** 宽视图上限（px）与视口占比：面板最多占半屏，地图始终保留另一半。 */
export const PANEL_WIDE_MAX = 760;
export const PANEL_WIDE_VIEWPORT_RATIO = 0.5;

/**
 * 面板实际渲染宽度：常规为用户的 sidebarWidth；宽视图时取
 * min(760, 视口×0.5 − rail − 间隙)，且不小于常规宽度（窄视口下宽视图退化为常规）。
 */
export function effectivePanelWidth(
  sidebarWidth: number,
  expanded: boolean,
  viewportWidth: number = typeof window === 'undefined' ? 0 : window.innerWidth,
): number {
  if (!expanded || viewportWidth <= 0) return sidebarWidth;
  const wide = Math.min(PANEL_WIDE_MAX, Math.round(viewportWidth * PANEL_WIDE_VIEWPORT_RATIO) - RAIL_W - PANEL_GAP);
  return Math.max(sidebarWidth, wide);
}
