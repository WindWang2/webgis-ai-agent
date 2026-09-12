# 触控基础审计（ADR-0144 / P7）

基线：origin/master `8b5b8375`。范围：触屏（pointer: coarse）主流程可用性。

## 1. 44px 命中区审计表

WCAG 2.5.5（AAA 44×44）/ Apple HIG（44pt）与 Android Material（48dp）取交集 44px。
实现：`app/globals.css` 新增 `@media (pointer: coarse) { .touch-target { min-width/min-height: 44px } }`
—— 类名显式标注，避免全局 button 提升破坏 chip/徽标布局。

| 交互目标 | 文件 | 现状桌面 | 触屏提升 | 状态 |
|---|---|---|---|---|
| NavRail 模式切换按钮 ×3 | components/layout/nav-rail.tsx | 36×36 | 44×44（.touch-target） | ✅ 本线 |
| NavRail 工作区 tab ×9 | 同上 | 36×36 | 44×44（.touch-target） | ✅ 本线 |
| NavRail 模板/折叠 ×2 | 同上 | 36×36 | 44×44（.touch-target） | ✅ 本线 |
| MapToolbarHUD 测量/绘制按钮 | components/map/map-toolbar-hud.tsx | 32–36 | ⏳ 归零计划 v9.1（按钮密度高，提升需压缩间距方案） | ⏳ |
| ContextPanel 拖拽手柄 | components/layout/context-panel.tsx | 8px 宽（desktop 专属） | mobile 档不渲染（sheet 化） | ✅ 本线（P5） |
| BottomSheet 把手 | components/layout/bottom-sheet.tsx | —（新组件） | 44px 高（含把手内边距） | ✅ 本线 |
| Tab 键（chat 域发送/停止） | components/sidebar/chat-tab.tsx | ≥36 | ⏳ 归零计划 v9.1 | ⏳ |
| 绘制工具顶点命中半径 | lib/map/…（MapLibre queryRenderedFeatures 容差） | 默认 | ⏳ 顶点 hit 容差待参数化 | ⏳ |

## 2. 草图工具：绘制 vs 平移手势消歧

- 现状：草图编辑接管 `map.dragPan.disable()`（edit 会话期间），顶点拖拽用
  MapLibre pointer 事件（单指针语义）—— 触屏单指拖顶点与平移不冲突。
- 本线新增（components/map/sketch-editor.tsx）：编辑会话期间
  `touchZoomRotate.disableRotation()` + `touchPitch.disable()`，
  退出会话恢复 —— 触屏双指常被识别为旋转/俯仰，会打断顶点拖拽；
  平移保留（单指拖图仍是预期）。退出路径（:408/:446 两处）对称恢复。
- 单指绘制开关模式（draw-lock）：归零计划 v9.1（需与 agent 驱动的
  draw 命令语义对齐，属 J 线协调面）。

## 3. 双指缩放/旋转与 MapLibre 手势

- 现状基线：`touchZoomRotate` 默认开启（缩放+旋转），无 cooperativeGestures。
- 冲突点：页面滚动 vs 地图接管的单指滚动手势。移动档（P5 mobile）地图容器
  全屏、无页面滚动共存，冲突面收敛为 sheet 内容滚动（overscroll-contain 隔离，
  见 bottom-sheet.tsx）。
- cooperativeGestures（要求双指才缩放）：列入 v9.1（需 UX 决策，涉及桌面
  触屏笔记本用户回退路径）。

## 4. 长按上下文菜单（图层/要素）

归零计划 v9.1：长按（pointerdown 500ms 无位移）触发与右键等价的要素上下文菜单。
依赖 map-panel 的要素选中管线，触控版入口本线未引入新交互面。

## 5. 键盘/触控双通道 a11y

- 既有键盘 a11y 契约不回退：nav-rail roving tabindex / 方向键、settings
  WAI-APG tablist、context-panel 手柄 ArrowLeft/Right 调宽（desktop 档）全部保留。
- mobile 档 sheet 焦点圈闭：`test/layout-responsive.test.tsx`（Escape 关闭/初始聚焦）。
- visual-system 契约（对比度）不受触屏提升影响（仅尺寸变化）。
