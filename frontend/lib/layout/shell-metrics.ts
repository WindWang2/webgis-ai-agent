/**
 * 壳层度量常量（#2140 F5）—— app/page.tsx 的地图容器 inset 与 TopBar /
 * 状态条 / HUD 高度像素耦合的单一 TS 来源。
 *
 * 与 app/globals.css 的布局令牌（--topH / --stH）同源：CSS 侧经 Tailwind
 * 间距刻度（h-topbar / top-topbar …）消费，JS 侧（inline style 计算）从这里
 * 读。改设计令牌时两处同步，不允许再出现裸魔法数字。
 */
export const TOP_BAR_HEIGHT_PX = 42;
export const STATUS_BAR_HEIGHT_PX = 24;
/** HUD 展开（210px，见 nav-rail/context-panel 避让注释）时，底部地图 chrome
 *  在状态条基线之上再抬升的高度（210 - 状态条 24 + 呼吸间距 10）。 */
export const MAP_CHROME_BOTTOM_HUD_OPEN_PX = 196;
/** HUD 收起时底部 chrome 的呼吸间距。 */
export const MAP_CHROME_BOTTOM_IDLE_PX = 10;
