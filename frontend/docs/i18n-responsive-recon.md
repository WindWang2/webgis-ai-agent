# i18n × 响应式 P0 勘察报告（foundation/i18n-responsive-v9）

> 基线：origin/master `8b5b8375`。只读勘察产出（S1 Explore 一次 + 主 agent 复核），是 P3 全量抽取的销项表底稿。
> CJK 检测：正文 `[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]`（含中文标点/全角）；测试仅 `[\u4e00-\u9fff]`。
> 排除：`node_modules/ .next/ coverage/ scripts/ test/ tests/ __tests__/ *.test.* *.spec.*`。

## 1. 字符串普查

### 1.1 总量

| 指标 | 值 |
|---|---|
| 扫描 .ts/.tsx 文件总数 | 342 |
| 含 CJK 文件总数 | **272（79.5%）** |
| 其中 .tsx（组件） | 121 |
| 其中 .ts（逻辑层） | 151 |
| 非测试源码总行数 | 70,063 LOC |
| CJK 匹配行总数 | **7,426 行** |

注意：CJK 行含大量**注释**；真正需要键化的用户可见字符串集中在 JSX 文本、UI 属性（placeholder/title/aria-label/label）、toast/error 消息、导出图纸 chrome 文案。

### 1.2 目录聚类（含 CJK 文件数 / CJK 行数，按行数降序，top 24）

| 目录 | 文件数 | CJK 行数 | | 目录 | 文件数 | CJK 行数 |
|---|---|---|---|---|---|---|
| components/sidebar | 28 | 1032 | | lib/layers | 6 | 160 |
| lib/map-kit | 17 | 985 | | lib/api | 13 | 153 |
| components/map | 40 | 930 | | components/settings | 7 | 152 |
| lib/hooks | 16 | 427 | | lib/mapspec-runtime | 5 | 140 |
| lib/map-commands | 13 | 332 | | lib/results | 3 | 133 |
| components/chat | 14 | 294 | | components/agent | 3 | 116 |
| lib/map-components | 10 | 269 | | components/shared | 11 | 115 |
| lib/workbench | 8 | 268 | | lib/selection | 3 | 106 |
| lib/store | 9 | 254 | | components/hud | 2 | 105 |
| lib/mapspec | 5 | 247 | | lib/collab | 4 | 98 |
| components/layout | 5 | 242 | | lib/session | 2 | 97 |
| components/drawers | 2 | 66 | | app（page.tsx 56 + layout.tsx 1） | 2 | 57 |

### 1.3 Top 20 文件（销项优先级队列）

| CJK 行 | 文件 | | CJK 行 | 文件 |
|---|---|---|---|---|
| 303 | lib/map-kit/export-chrome.ts | | 92 | components/map/map-components/floating-chrome.tsx |
| 274 | lib/map-kit/exporter.ts | | 88 | components/layout/context-panel.tsx |
| 199 | components/sidebar/layers-tab.tsx | | 86 | lib/hooks/use-workspace-session.ts |
| 196 | components/map/map-panel.tsx | | 84 | components/sidebar/data-sources/dataset-inspector.tsx |
| 128 | lib/mapspec/user-mutation.ts | | 82 | components/chat/tool-call-card.tsx |
| 127 | lib/hooks/use-sse-stream.ts | | 81 | lib/map-components/resolve-layout.ts |
| 114 | components/map/comparison/comparison-view.tsx | | 76 | lib/workbench/persistence.ts |
| 102 | lib/map-commands/layerCommands.ts | | 76 | lib/map-commands/visibility-transaction.ts |
| 98 | lib/map-kit/renderer.ts | | 72 | components/sidebar/map-studio-tab.tsx |
| 94 | lib/map-kit/runtime-layer-registry.ts | | 71 | lib/session/map-state-restore.ts |

### 1.4 可国际化属性中的 CJK（components/ lib/ app/，估计值）

| 属性 | 计数 | 模式 |
|---|---|---|
| `placeholder=` | 20 | JSX 双引号形式 |
| `title=` | 102 | `title="…CJK…"` |
| `aria-label=` | 116 | `aria-label="…CJK…"` |
| `label=` / `label:` | 201 | `\blabel\s*[=:]\s*"…CJK…"` |
| toast 行 | 45 | 行含 `toast` 且含 CJK（`useToastStore.addToast(message, type)`，非链式 API） |
| 原生 alert/confirm | 0 | — |

## 2. 动态文案面

### 2.1 SSE / 流式用户可见消息（产生点）

- `lib/hooks/use-sse-stream.ts:340` 深度探索任务名；`:393` 新会话欢迎词（**与 `app/page.tsx:218` 双份硬编码**）；`:696-699` 搜索/热力图/分析结果标题。
- `lib/hooks/use-workspace-session.ts:46` 网络错误；`:50` 未知错误；`:52` 加载会话失败；`:249` 历史会话恢复提示。
- `lib/session/map-state-restore.ts:34` 图层加载失败；`:147/269-271` 分析结果/地图产品图层命名。
- `lib/api/upload.ts:119-138` 上传失败/超时/网络错误族。
- 错误信封统一入口：`lib/api/transport.ts:142` `describeApiError(err, fallback)`（优先 `body.detail`，否则 `fallback（HTTP status）`；TypeError → 网络错误文案）。后端信封无 `user_message` 字段——前端直接展示 `detail`（见 P6 双轨契约）。

### 2.2 隐患

- `components/providers/system-message-bridge.tsx` 用中文正则 `/失败|错误|error/i` 做 toast error 分级——切英文后需按结构化字段判定而非关键词。
- `lib/store/slices/uiSlice.ts:74` `baseLayer` 默认值 `'Carto 深色'` 是**持久化 state 中的中文值**，改名需兼容旧 localStorage。
- chart 轴/tooltip 本身无硬编码中文；仅 `chart-core.tsx:410/456` aria-label、`chart-renderer.tsx:19` 不支持类型消息、`chart-panel.tsx:273/341/345` 面板标题与视野联动。

### 2.3 日期/数字格式化点（18 处，几乎全部显式 `zh-CN`）

`components/agent/run-timeline.tsx:50`、`components/map/sketch-editor.tsx:514`、`components/map/legends/legend-card.tsx:66`、`components/sidebar/chat-tab.tsx:166`、`components/sidebar/results/result-list.tsx:34`、`components/sidebar/results/result-detail.tsx:62,201`、`components/sidebar/map-product-versions.tsx:56`、`components/table/attribute-table-panel.tsx:197`、`components/chat/chart-core.tsx:495,526`、`lib/hooks/use-workspace-session.ts:101`、`lib/map-kit/navigation.ts:285`、`lib/map-kit/exporter.ts:1202`、`lib/map-kit/export-chrome.ts:2228,2245`、`lib/workbench/undo.ts:71`。另 `lib/map-kit/legend-model.ts:15` 注释声明 zh-CN 感知。

## 3. 布局面依赖表

### 3.1 桌面壳结构（app/page.tsx，446 行）

`h-screen flex-col`（fontSize 内联）→ TopBar（高 42，wrapper marginTop:42）→ 中段 `relative flex-1 marginBottom:24`：地图容器 `absolute left = mapInsetLeft(...)`（transition .25s）内含 MapPanel/ExportMask/SpatialCrosshair/PanelDockHost；FloatingLegend（right-3, bottom 随 HUD）；MemoNavRail（悬浮）；StreamingChatHost；RagIndependentPanel；MapStatusReadout。浮层：EmbodiedHud、HistoryDrawer、ConfirmDialog、SettingsPanel、TemplateGalleryV2、TweaksPanel。

### 3.2 现有宽度/断点常量（全部像素常量，无宽度媒体查询）

| 常量 | 值 | 位置 | 语义 |
|---|---|---|---|
| `RAIL_W` | 48 | lib/utils/workspace-inset.ts | NavRail 宽 |
| `PANEL_GAP` | 12 | 同上 | 面板与地图间距 |
| `MAP_INSET_MAX_VIEWPORT_RATIO` | 0.5 | 同上 | #999 最小版：inset 封顶视口 50% |
| `LEFT_PANEL_MIN_VIEWPORT_PX` | 768 | lib/store/slices/uiSlice.ts:24-27 | 仅决定面板初始开合 |
| `VIEWPORT_COLLAPSE_CANVAS_HEIGHT` | 520 | lib/map-components/resolve-layout.ts:134 | max-height 建议收起（非宽度） |
| `sidebarWidth` 默认/clamp | 330 / 280–420 | uiSlice.ts:170-174 | persist（geoagent-settings.partialize 含之） |

- `@media` 全库唯一一处：`app/globals.css:676`（prefers-reduced-motion）。
- `matchMedia` 仅 2 hook：`use-prefers-reduced-motion.ts`、`use-small-viewport.ts`（后者按 **max-height** 判定，唯一消费方 `floating-chrome.tsx:156`，语义是「建议收起」而非布局切换）。

### 3.3 拖拽调宽

`components/layout/context-panel.tsx`：拖拽 separator（:127 读 setSidebarWidth，:186 RAF 提交）+ 键盘路径（:283-287）；滑杆入口 `components/tweaks-panel.tsx:227`。复位点 `lib/store/slices/dockSlice.ts:174`（sidebarWidth:330 + leftPanelOpen:true）。

### 3.4 触控/指针现状

| 文件 | 现状 |
|---|---|
| components/map/comparison/comparison-view.tsx:500 | 对比分割条 pointer 拖拽 |
| components/map/map-components/floating-chrome.tsx:304,576 | 浮动卡片标题栏 pointer 拖拽 |
| components/map/poi-info-panel.tsx:193 | pointer 冒泡阻断 |
| components/map/sketch-editor.tsx:389,405,443 | 草图编辑接管 `map.dragPan.disable()/enable()` |
| components/map/map-panel.tsx:963-982,1096 | 框选模式接管 dragPan/boxZoom |
| lib/map-commands/viewCommands.ts:59-62 | 手势活跃检测（dragPan/dragRotate/touchZoomRotate.isActive） |

无 `touchstart` 直听、无 cooperativeGestures；触控完全依赖 MapLibre 默认手势（含双指旋转），无长按上下文菜单，无 44px 命中区审计。

### 3.5 uiSlice 布局 state（persist key=`geoagent-settings`）

`leftPanelOpen`（非持久，初始 = innerWidth≥768）、`rightPanelOpen:true`、`sidebarWidth:330`（持久）、`hudOpen/ragPanelOpen/tweaksOpen/historyOpen/templatesOpen:false`（overlay 互斥）、`settingsTab:'llm'`、`activeLeftTab:'chat'`、`theme:'light'`（pre-paint）、`accentColor`（pre-paint）、`fontSize:15`、`baseLayer:'Carto 深色'`（持久中文值）。

## 4. 测试硬编码中文断言清单

范围 `test/ + tests/`：27 个测试文件含 CJK，21 个文件 / 411 行。Top（域 / 文件 / CJK 行）：

| 域 | 文件 | 行 |
|---|---|---|
| challenge 压测 | test/challenge/m5-challenger-hud-grid-popover.stress.test.tsx | 88 |
| results | test/results/results-ui.test.tsx | 45 |
| visual corpus | test/visual/workbench-layout-corpus.test.tsx | 39 |
| design-system | test/design-system/visual-system.contract.test.tsx | 28 |
| workbench a11y | test/workbench-editing-a11y.test.tsx | 24 |
| UI 轮次回归 | test/ui-round-883-891.test.tsx | 24 |
| challenge | test/challenge/m5-chat-and-design-stress.test.tsx | 24 |
| viewport | test/viewport-thinning.test.ts | 22 |
| workbench 虚拟化 | test/workbench-virtual-10k.test.tsx | 19 |
| explorer | tests/components/explorer/tabular-data-grid.test.tsx | 17 |
| workbench 压测 | test/workbench-stress-500.test.tsx | 16 |
| explorer | tests/components/explorer/preview-modal.test.tsx | 13 |
| results | test/results/normalize.test.ts | 12 |
| mock | test/__mocks__/maplibre-map.ts | 12 |
| behavioral | tests/behavioral/map-behaviors.test.ts | 8 |

典型断言：`lib/session/map-state-restore.test.ts:392-393`（`toContain('学校分布')` / `toMatch(/加载失败/)`）、`use-sse-stream.test.ts:1065`（`/截断|回放/`）。改造策略：渲染断言改键断言（`screen.getByText(t('key'))` 经测试内 Provider）或消息 fixture；mock/fixture 中文案保留为数据不属守卫范围。

## 5. 基建事实

- 依赖：next 16.3.4 / react 19.2.8 / zustand 5 / tailwind 3.4 / vitest 4 / eslint 9（eslint-config-next 16.3.4）/ maplibre-gl 5 / recharts 3 / framer-motion 13 / playwright 1.63。**无任何 i18n 依赖**。
- `app/layout.tsx`：`<html lang="zh-CN" data-theme="light">`；pre-paint bootstrap 读 `localStorage['geoagent-settings']`（zustand persist key）设置 dark class / data-theme / accent——语言状态沿用同一模式（见 P1）。
- 设置面板：`components/settings/settings-panel.tsx`（TABS：llm/skills/rag/map/account，account 前 divider）——语言切换器以新 tab（append-only）插入。
- vitest：单配置无 workspace；coverage thresholds lines 75 / functions 70 / statements 75 / branches 60；setupFiles test/setup.ts。
- `test/visual/capture.mjs`：`--out --base --only <name>`；视口矩阵固定 4 档桌面（1920/1440/1366/1024）× light/dark；SURFACES 经真实用户路径（nav rail tab 点击）；后端/瓦片请求拦截回放 fixture（离线确定性）。
- 字体 DM_Sans / JetBrains_Mono 仅 latin 子集（无中文子集，CJK 回退系统字体——现状即如此，本线不改字体栈）。

## 6. 销项策略（P3 执行序）

1. **批 1（框架验证）**：app/layout.tsx + app/page.tsx + TopBar + NavRail + 状态条 —— 壳层全键化。
2. **批 2（五域走查域）**：components/chat、components/map、components/sidebar、components/settings、components/story、components/drawers —— §5 门禁的 en 无漏串走查对象。
3. **批 3（动态文案）**：use-sse-stream / use-workspace-session / map-state-restore / upload / transport（describeApiError fallback）+ system-message-bridge 结构化分级。
4. **批 4（导出与逻辑层）**：lib/map-kit（export-chrome/exporter/renderer 等，经非 React `t()`）、lib/map-commands、lib/mapspec/user-mutation、lib/workbench/persistence、session。
5. **批 5（测试断言改造）**：§4 清单逐文件键断言/fixture 化（渲染断言走测试 Provider）。
6. 守卫白名单（test/i18n/no-raw-cjk.whitelist.json）起始记录批 1–5 完成后的残余文件，逐版归零计划写入 PR。
