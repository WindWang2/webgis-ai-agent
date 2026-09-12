# RTL 预研：logical properties 改造清单（ADR-0144 / P8 — v9.1 计划，不强制全量）

状态：预研文档（本线不实施）。目标：未来支持 RTL 语言（ar/he）时，把「物理方向
属性」迁移为「逻辑方向属性」的改动面清单与顺序。

## 现状盘点（本线勘察）

- 全库物理方向类：`left-` / `right-` / `pl-` / `pr-` / `ml-` / `mr-` / `text-left`
  / `text-right` / `-translate-x` 等；内联 `style.left/right`。
- 关键物理耦合点（改造优先级队列）：
  1. `app/page.tsx` 地图容器 `left: mapInsetLeft(...)` 与 `--map-chrome-left`
     —— RTL 下 inset 方向应镜像（左栏在 RTL 中停靠右侧）。
  2. `components/layout/context-panel.tsx`：`translateX(-110%)` 折叠、
     `border-r`、`-right-1` 拖拽手柄。
  3. `components/layout/nav-rail.tsx`：`fixed left-0 top-topbar`、
     `left-[-5px]` 选中指示条。
  4. `components/settings/settings-panel.tsx` / `history-drawer` /
     `template-gallery-v2`：右侧抽屉 `inset-y-0 right-0`。
  5. `components/layout/bottom-sheet.tsx`：底部锚定，天然 RTL 无关 ✅（无需改）。
  6. `components/map/map-toolbar-hud.tsx`：`right-4 top-4` 浮动工具条。

## 改造策略（建议）

1. Tailwind 3.4 已支持逻辑属性前缀 `ps-`/`pe-`/`ms-`/`me-`/`start-`/`end-`
   与 `rtl:`/`ltr:` 变体 —— 逐文件机械替换，不需要运行时开关。
2. 布局 JS 常量（`mapInsetLeft` 返回 `left`）需要方向感知：引入
   `insetInlineStart`（CSS 逻辑属性）替代 `left`，或按 `document.dir` 取反。
3. 顺序：drawer 族（1 天）→ rail/panel（1 天）→ 地图 chrome 与 inset 计算
   （含 MapLibre RTL text 插件评估 `maplibre-gl-rtl-text`，中文/阿语混排）→
   visual corpus 增加 RTL 快照档。

## 明确不做

- 本线不引入 `<html dir>` 切换（语言 ≠ 方向；ar 未在支持列表）。
- 不做全量机械替换（无 RTL 用户前是纯回归风险，零收益）。
