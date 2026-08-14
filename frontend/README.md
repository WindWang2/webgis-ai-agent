# WebGIS AI Agent 前端

具身空间智能引擎前端。视觉体系当前为 **Visual System V4（专业 GIS 工作台）**，
信息架构为 V3 的 NavRail + ContextPanel 工作区外壳。

设计规范见下方 [🎨 设计规范 (Visual System V4)](#-设计规范-visual-system-v4)。

## 🎨 视觉体系沿革

### V4 — 专业 GIS 工作台（当前）
- **语义 token 体系** - 表面/描边/文字/状态/地图挂件一套变量，明暗各自定义
- **地图优先** - 地图永远是视觉主体，抽屉不再盖满地图；底部挂件共用一条基线
- **高信息密度** - 6 级密排字号、24/26/30 三档控件高度、图层行 ~30px
- **去玻璃拟态** - 地图之上不再有 `backdrop-filter`，改用不透明面板 + 极平阴影
- **明暗双一等公民** - `darkMode: 'class'`，对比度由测试直接解析 CSS 计算校验

### V2 遗留
- **玻璃拟态 (Glassmorphism)** - 已由 V4 移除（`.glass` 等类已删除）
- **Agentic 配色** - 绿色主色调保留，但收敛为 `--accent` / `--accent-vivid` 两个角色
- **动态光效** - 思考/执行状态的扫描线动画（受 `prefers-reduced-motion` 门控）

### 重构的组件架构
```
frontend/components/
├── chat/                    # 对话组件
│   ├── collapsible-think.tsx # 可折叠思考链
│   ├── map-action-renderer.tsx # AI 地图指令渲染器
│   ├── plan-card.tsx        # 执行计划卡片
│   ├── suggested-prompts.tsx # 建议提示词
│   ├── task-progress.tsx    # 任务进度条
│   └── tool-call-card.tsx   # 工具调用卡片
├── map/                     # 地图核心
│   ├── map-panel.tsx        # 主地图面板 (MapLibre)
│   ├── map-action-handler.tsx # AI 指令分发器
│   ├── map-canvas.tsx       # 演示画布
│   ├── baselayer-switcher.tsx # 底图切换器
│   ├── floating-legend.tsx  # 浮动图例
│   ├── map-decorations.tsx  # 地图饰件 (指北针/比例尺)
│   ├── export-mask.tsx      # 导出遮罩
│   └── legends/             # 分级/分类/连续图例
├── hud/                     # Agentic HUD 2.0
│   ├── embodied-hud.tsx     # 主 HUD 座舱
│   ├── agent-env-hud.tsx    # Agent 环境感知面板
│   ├── layer-style-panel.tsx # 图层样式面板
│   ├── settings-panel.tsx   # 设置面板
│   └── causal-trace.tsx     # 因果追踪
├── sidebar/                 # 工作区上下文面板标签（UI V3）
│   ├── chat-tab.tsx         # 聊天标签
│   ├── project-tab.tsx      # 项目标签
│   ├── data-sources-tab.tsx # 数据源标签（编排器）
│   ├── data-sources/        # 数据源子模块（hook/卡片/弹窗）
│   ├── layers-tab.tsx       # 图层管理标签
│   ├── analysis-tab.tsx     # 分析标签
│   ├── map-studio-tab.tsx   # 制图工坊标签
│   └── tasks-tab.tsx        # 任务中心标签
├── drawers/                 # 抽屉面板
│   ├── history-drawer.tsx   # 历史记录抽屉
│   └── template-gallery-v2.tsx # 模板库抽屉
├── explorer/                # 空间探索器
│   ├── explorer-progress-panel.tsx
│   ├── reasoning-panel.tsx
│   └── what-if-panel.tsx
├── report/                  # 报告生成器
│   ├── report-generator.tsx
│   └── report-preview.tsx
├── layout/                  # 布局组件
│   ├── top-bar.tsx          # 顶部导航栏
│   ├── nav-rail.tsx         # 主导航竖排图标栏（UI V3）
│   └── context-panel.tsx    # 统一上下文面板（UI V3）
├── overlays/                # 覆盖层
│   └── perception-rings.tsx # 感知环动画
├── panel/                   # 功能面板
│   └── rag-independent-panel.tsx
├── providers/               # Context Providers
├── settings/                # 设置页面
├── shared/                  # 共享组件
├── ui/                      # 基础 UI 组件
├── upload/                  # 上传组件
├── code-highlight/          # 代码高亮
├── layer-card.tsx           # 图层卡片
├── sort-controls.tsx        # 排序控制
└── tweaks-panel.tsx         # UI 调整面板
```

### 核心状态管理
- **Zustand Store** (`lib/store/useHudStore.ts`) - 统一状态管理
- **Theme System** (`lib/theme.ts`) - 主题颜色管理
- **Type Definitions** (`lib/store/hud-types.ts`) - 完整的 TypeScript 类型

## 🚀 快速开始

```bash
# 安装依赖
cd frontend
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build
```

访问 http://localhost:3000 即可查看效果。

## 🎯 功能特性

### 1. 多标签侧边栏
- **聊天** - 与 GeoAgent 对话
- **图层** - 管理地图图层（可见性、排序、不透明度）
- **操作日志** - 查看操作历史
- **导出** - 管理导出的文件

### 2. 地图工具栏
- 🔍 缩放控制 (+/-)
- 🏠 回到首页
- 📍 定位当前位置
- 🎚️ 2D/3D 切换
- 📊 HUD 显示切换
- 💾 导出

### 3. Agent 状态系统
- `idle` - 就绪
- `thinking` - 思考中（扫描线动画）
- `acting` - 执行中
- `done` - 完成
- `error` - 错误

### 4. 演示模式
点击左下角 "Try Demo" 按钮即可体验完整的模拟运行流程，无需后端支持。

## 📝 开发指南

### 新增组件
所有新组件都应遵循：
1. 用语义 token 类（`bg-surface-panel` / `text-ink-muted` / `border-edge-subtle`），
   **不要**读 `lib/theme.ts` 的 JS 颜色对象来做内联 `style`
2. 明暗两套都要能看 —— token 已经在两个主题里各自定义，正常情况下不需要写 `dark:`
3. 提供 TypeScript 类型定义
4. 保持小而精，单一职责

### 状态更新
在 `lib/store/useHudStore.ts` 中添加新的状态字段和方法。
需要跨刷新保留的字段必须加进 `partialize`（未列入的字段会静默丢失）。

### 主题扩展
在 `app/globals.css` 的 `:root` **和** `.dark` 两个块里同时定义新 token，
再在 `tailwind.config.ts` 里绑成 `var(--token)` 形式的工具类。
`test/design-system/tokens.contract.test.ts` 会强制这条双定义规则。

## 🎨 设计规范 (Visual System V4)

定位是**专业 GIS 工作台**：地图永远是视觉主体，工具 UI 克制、紧凑、信息密度高。
不用大面积渐变、不堆玻璃拟态、不加无意义动画。

### 语义 token（唯一色彩来源）

全部定义在 `app/globals.css`，通过 `tailwind.config.ts` 暴露成工具类。
旧的 `--theme-*`、`--agent-*`、shadcn HSL 三套变量现在都 `var()` 指回 V4 token，
所以历史调用点不会漂移。

| 组 | token | 工具类 |
|---|---|---|
| 表面 | `--surface-canvas/panel/raised/overlay/sunken/hover/selected/scrim` | `bg-surface-*` |
| 描边 | `--border-subtle/default/strong` | `border-edge-*` |
| 文字 | `--text-primary/secondary/muted/disabled/on-accent` | `text-ink-*` |
| 状态 | `accent` `success` `info` `warning` `critical` `neutral`（各带 `-soft`/`-border`） | `text-status-*` `bg-status-*-soft` |
| 地图挂件 | `--map-chrome-bg/border/text/…` | `bg-map-chrome` / `.map-chrome` |

- **字体**: DM Sans (正文) + JetBrains Mono (代码/数值/坐标)
- **字号**: 6 级密排刻度 —— `text-micro`(10) `caption`(11) `meta`(12) `body`(13) `title`(14) `heading`(15)
- **圆角**: `rounded-xs`(3) `sm`(4) `md`(6) `lg`(8) `xl`(12) `pill`。刻意偏紧 —— 精密仪器感，不是消费级卡片
- **控件/行高**: `h-control-sm`(24) `md`(26) `lg`(30)；`min-h-row-sm/md/lg`。24px 是 WCAG 2.2 SC 2.5.8 的下限，不要再往下压
- **图标**: `size-icon-sm`(12) `md`(14) `lg`(16)
- **阴影**: `shadow-raised/overlay/drawer/chrome`，刻意很平
- **焦点环**: 靠全局 `*:focus-visible`，别在组件里写 `outline: none`

### 两个 accent，别搞混

- `--accent` —— 文字安全，也是 `--text-on-accent` 底下的填充色
- `--accent-vivid` —— 只用于图例色块/指示点这类非文字标记

用户自选强调色走 `--agent-accent-raw`（store 写入）→ `--agent-accent`（按主题校正后的可用值）。
**读的时候一律读 `--agent-accent`。**

### 地图挂件布局

底部所有挂件从 `--map-chrome-bottom` 这一个基线往上排（HUD 开合时会变）。
新增挂件请挂到这个基线上，不要再写死 `bottom-*`，否则会和图例/比例尺叠在一起。

### 设计系统测试

`test/design-system/` 下三个套件是硬约束，改 token 前先看它们：

- `tokens.contract.test.ts` —— `darkMode: 'class'`、token 双主题定义、工具类必须绑 CSS 变量
- `contrast.test.ts` —— 直接解析 CSS 算真实对比度，正文 4.5:1 / 非文字 3:1
- `visual-system.contract.test.tsx` —— 无障碍与交互行为（可及名称、焦点、inert、键盘重排…）

### 视觉回归

```bash
npm run dev                                    # 另开一个终端
node test/visual/capture.mjs --out .visual/after
```

结果工作台的场景通过真实 SSE 通路播种（stub 掉的 `/chat/stream` 重放
`step_result` 事件）。离线/无外网环境下加一个字体 mock，否则 dev server
首次编译会卡在 Google Fonts 拉取上：

```bash
NEXT_FONT_GOOGLE_MOCKED_RESPONSES='{"DM Sans":{"subsets":["latin"],"weight":["300","400","500","600"],"style":"normal","src":"https://fonts.gstatic.com/s/dmsans.woff2"},"JetBrains Mono":{"subsets":["latin"],"weight":["400","500"],"style":"normal","src":"https://fonts.gstatic.com/s/jetbrainsmono.woff2"}}' npm run dev
```

4 视口 × 明暗 × 全部界面（含 8 个结果工作台场景）落到 `.visual/`
（已 gitignore）；捕获失败时进程以非零码退出。后端全部走内置 fixture，
不需要真的起后端（dev 默认 :8001 与显式 :8000 都会被 stub 覆盖）。

## 📚 相关文档

- [技术方案说明书](../docs/技术方案说明书.md)
- [架构文档](../docs/architecture.md)
- [API 文档](../docs/api-docs.md)
