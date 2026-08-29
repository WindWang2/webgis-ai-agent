# WebGIS AI Agent 前端

具身空间智能工作台前端:Next.js 14 + MapLibre GL,视觉体系为 **Visual System V4(专业 GIS 工作台)**,信息架构为 V3 的 NavRail + ContextPanel 外壳。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17

## 目录

- [快速开始](#快速开始)
- [页面与组件架构](#页面与组件架构)
- [核心模块](#核心模块)
- [功能特性](#功能特性)
- [开发指南](#开发指南)
- [设计规范(Visual System V4)](#设计规范visual-system-v4)
- [测试与视觉回归](#测试与视觉回归)
- [相关文档](#相关文档)

## 快速开始

```bash
cd frontend
pnpm install

# 后端地址(按实际后端端口调整;manage.py dev / dev compose 为 18000)
echo 'NEXT_PUBLIC_API_URL=http://localhost:18000' >> .env.local
echo 'NEXT_PUBLIC_WS_URL=ws://localhost:18000' >> .env.local

npm run dev        # http://localhost:3000
```

| 脚本 | 用途 |
|---|---|
| `npm run dev` / `build` / `start` | 开发 / 构建 / 生产启动 |
| `npm run test` / `test:watch` / `test:coverage` | vitest(覆盖率闸 75/70/75/60) |
| `npm run typecheck` | 双 tsconfig 严格类型检查(源码 + 测试) |
| `npm run lint` | ESLint,0 warning 门槛 |

没有后端也能体验:点击左下角 **Try Demo** 进入演示模式,走完整模拟流程。

环境变量(完整见 `.env.example`):`NEXT_PUBLIC_API_URL`、`NEXT_PUBLIC_WS_URL`、`NEXT_PUBLIC_TIANDITU_TOKEN`、`NEXT_PUBLIC_APP_VERSION`。

## 页面与组件架构

页面(App Router,共 2 个):

- `/` — 主工作台:TopBar + NavRail + ContextPanel + MapPanel + EmbodiedHud + 各抽屉
- `/story?session_id=` — StoryMap 叙事回放:会话消息 + map-state 还原地图,markdown 叙事流

```
frontend/components/
├── layout/          # top-bar · nav-rail · context-panel(V3 信息架构外壳)
├── sidebar/         # ContextPanel 标签:chat / project / data-sources / layers /
│                    #   analysis / map-studio / results / tasks / workflow
├── chat/            # 消息渲染:思考链折叠、地图指令渲染器、制图/H3-LISA/等时线结果卡、
│                    #   chart-renderer、mini-md、建议提示词
├── map/             # map-panel(主面板)· map-action-handler(指令分发)·
│                    #   baselayer-switcher · export-mask · map-decorations ·
│                    #   spatial-crosshair · map-status-readout · legends/
├── hud/             # embodied-hud · agent-env-hud · layer-style-panel · causal-trace
├── drawers/         # history-drawer · template-gallery-v2 · map-combinator · telemetry
├── explorer/        # explorer-progress-panel · reasoning-panel · what-if-panel
├── report/          # report-generator · report-preview
├── settings/        # V2 设置面板:账户 / LLM / 地图 / RAG / skills-hub
├── shared/ ui/      # confirm-dialog、status-badge 等原语 · toast
├── upload/ panel/ providers/ overlays/ code-highlight/
└── layer-card.tsx / sort-controls.tsx / tweaks-panel.tsx
```

## 核心模块

| 模块 | 位置 | 职责 |
|---|---|---|
| 状态管理 | `lib/store/useHudStore.ts` | Zustand slice 组合(layers/results/settings/task/ui);跨刷新字段须进 `partialize` |
| 地图内核 | `lib/map-kit/` | 非 React 的 MapLibre 命令式封装:setData 引用缓存、视口裁剪、渲染去抖、瓦片凭据注入(tile-auth)、导出器 |
| SSE 解析 | `lib/hooks/use-sse-stream.ts` | 流事件解析;`VECTOR_TILE_THRESHOLD = 5000` 分流 MVT 与整包 GeoJSON;ref 提货券拉取 |
| 地图指令目录 | `lib/map-commands/` | 20 个 AI 地图指令(layer/view/query/heatmap/annotation/export 族),后端⊆前端目录不变量由测试守护 |
| MapSpec 编译器 | `lib/mapspec-compiler/` | MapSpec → SVG/PNG,worker + CLI |
| API 客户端 | `lib/api/` | transport、sse-stream-parser、各资源客户端;导出/报告/聊天图片走认证 blob 传输 |
| 会话还原 | `lib/session/map-state-restore.ts` | StoryMap 的地图状态回放 |

## 功能特性

- **工作区上下文面板(V3 IA)**:聊天 / 项目 / 数据源 / 图层 / 分析 / 制图工坊 / 结果 / 任务中心 / 工作流九个标签
- **会话管理**:历史抽屉两步确认删除;工作区非空时新建会话需确认;会话切换清理探索任务
- **深度探索进度**:`explorer_progress` 实时面板,任务可关闭、有并发上限
- **项目上下文**:激活项目随聊天请求注入(`project_id`);项目写操作对匿名用户禁用并提示登录
- **底图与瓦片凭据**:底图卡片直绑 `TILE_PROVIDERS` 目录;MapLibre `transformRequest` 对 first-party 瓦片注入会话凭据(>5000 要素 MVT 图层可加载)
- **地图工具栏**:缩放 / 回首页 / 定位 / 2D-3D / HUD 开关 / 导出(DPI 重采样 30s idle 兜底,WebGL context lost 显式报错并恢复 pixelRatio)
- **Agent 状态机**:`idle / thinking / acting / done / error`,思考态扫描线动画受 `prefers-reduced-motion` 门控

## 开发指南

**新增组件**:用语义 token 类(`bg-surface-panel` / `text-ink-muted` / `border-edge-subtle`),不要读 `lib/theme.ts` 的 JS 颜色对象做内联 style;明暗两套都要能看;保持单一职责。

**状态更新**:新字段加进 `lib/store/useHudStore.ts` 对应 slice;需要跨刷新保留的字段必须加进 `partialize`(未列入的字段会静默丢失)。

**主题扩展**:在 `app/globals.css` 的 `:root` **和** `.dark` 两个块里同时定义新 token,再在 `tailwind.config.ts` 绑成 `var(--token)` 工具类——`test/design-system/tokens.contract.test.ts` 强制这条双定义规则。

**地图挂件**:底部挂件统一挂到 `--map-chrome-bottom` 基线,不要写死 `bottom-*`,否则会与图例/比例尺重叠。

## 设计规范(Visual System V4)

定位是**专业 GIS 工作台**:地图永远是视觉主体,工具 UI 克制、紧凑、信息密度高。不用大面积渐变、不堆玻璃拟态(V2 玻璃拟态已移除)、不加无意义动画。

### 语义 token(唯一色彩来源)

全部定义在 `app/globals.css`,经 `tailwind.config.ts` 暴露为工具类。旧的 `--theme-*`、`--agent-*`、shadcn HSL 变量均已 `var()` 指回 V4 token,历史调用点不漂移。

| 组 | token | 工具类 |
|---|---|---|
| 表面 | `--surface-canvas/panel/raised/overlay/sunken/hover/selected/scrim` | `bg-surface-*` |
| 描边 | `--border-subtle/default/strong` | `border-edge-*` |
| 文字 | `--text-primary/secondary/muted/disabled/on-accent` | `text-ink-*` |
| 状态 | `accent` `success` `info` `warning` `critical` `neutral`(各带 `-soft`/`-border`) | `text-status-*` `bg-status-*-soft` |
| 地图挂件 | `--map-chrome-bg/border/text/…` | `bg-map-chrome` / `.map-chrome` |

- **字体**:DM Sans(正文)+ JetBrains Mono(代码/数值/坐标)
- **字号**:6 级密排刻度 —— `text-micro`(10) `caption`(11) `meta`(12) `body`(13) `title`(14) `heading`(15)
- **圆角/控件高**:`rounded-xs…pill` 刻意偏紧;`h-control-sm/md/lg`(24/26/30),24px 是 WCAG 2.2 SC 2.5.8 下限
- **焦点环**:靠全局 `*:focus-visible`,组件里不要写 `outline: none`

### 两个 accent,别搞混

- `--accent` —— 文字安全,也是 `--text-on-accent` 底下的填充色
- `--accent-vivid` —— 只用于图例色块/指示点这类非文字标记

用户自选强调色:`--agent-accent-raw`(store 写入)→ `--agent-accent`(按主题校正后的可用值)。**读的时候一律读 `--agent-accent`。**

## 测试与视觉回归

146 个测试文件;`test/design-system/` 三个套件是硬约束,改 token 前先看:

- `tokens.contract.test.ts` —— `darkMode: 'class'`、token 双主题定义、工具类绑 CSS 变量
- `contrast.test.ts` —— 解析 CSS 算真实对比度:正文 4.5:1 / 非文字 3:1
- `visual-system.contract.test.tsx` —— 无障碍与交互行为(可及名称、焦点、inert、键盘重排)

视觉回归(需先起 dev server,capture 默认打 :3311):

```bash
npm run dev -- -p 3311    # 另开终端;离线环境加 NEXT_FONT_GOOGLE_MOCKED_RESPONSES(见下)
node test/visual/capture.mjs --out .visual/after
```

4 视口 × 明暗 × 全部界面(含 8 个结果工作台场景)落到 `.visual/`(已 gitignore);场景经真实 SSE 通路播种(stub 掉的 `/chat/stream` 重放 `step_result`),后端全走内置 fixture,无需真后端。离线/无外网时给 dev server 注入字体 mock,否则首次编译会卡在 Google Fonts:

```bash
NEXT_FONT_GOOGLE_MOCKED_RESPONSES='{"DM Sans":{"subsets":["latin"],"weight":["300","400","500","600"],"style":"normal","src":"https://fonts.gstatic.com/s/dmsans.woff2"},"JetBrains Mono":{"subsets":["latin"],"weight":["400","500"],"style":"normal","src":"https://fonts.gstatic.com/s/jetbrainsmono.woff2"}}' npm run dev -- -p 3311
```

## 相关文档

- [技术方案说明书](../docs/技术方案说明书.md)
- [架构文档](../docs/architecture.md)
- [API 文档](../docs/api-docs.md)
- [Fetch-on-Demand 与 MVT](../docs/data-fetcher.md)
