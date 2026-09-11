# UX 深度线（feat/ux-depth-v9）P0 勘察报告

> 基线：origin/master @ 8b5b8375。只读勘察（S1 Explore + 主 agent 复核），所有结论带 file:line。
> 用途：P1–P6 实现的契约依据；与任务书 §0.2.4 预期不符之处已在各节末尾以「⚠ 勘察修正」标出。

## 1. 快捷键与 undo 现状表

### 1.1 use-keyboard-shortcut.ts

- `frontend/lib/hooks/use-keyboard-shortcut.ts`（54 行）：`useKeyboardShortcut(options)`，Options = `{onSend?(Ctrl/Cmd+Enter), onClear?(Escape), onSave?(Ctrl/Cmd+S), disabled?}`（:7-16）。单一 `handleKeyDown`（:23-46）if/else 判三键，window keydown（:49）+ cleanup（:50）。
- 消费点：**生产零消费**，唯一引用是自身测试。Ctrl+K 全仓无绑定。

⚠ 勘察修正：任务书称「注册表模式」，实际是固定三键 hook，无注册表。本线不改该文件（避免扰动他线），另建 `lib/commands/` 注册表（新目录，§8 边界内）。

### 1.2 全部 keydown 绑定清单（冲突源）

| 文件:行 | 键 | 范围 | 输入框防护 |
|---|---|---|---|
| lib/workbench/use-undo.ts:53-69 | Ctrl/Cmd+Z、Ctrl/Cmd+Shift+Z、Ctrl+Y | window 全局（app/page.tsx:144 挂载） | 有（isEditableTarget :39-49） |
| components/map/map-toolbar-hud.tsx:238-276 | `+ = - _` 缩放、`0/n/N` 复位、`3` 3D、`d` 测距、`a` 测面、`b` 框选、Escape | window 全局 | 有（:239-248）；**单字母无前缀=面板冲突源** |
| components/map/baselayer-switcher.tsx:76-86 | Escape（仅 open） | document | 无 |
| components/map/comparison/comparison-view.tsx:313-321 | Escape（仅 active） | window | 无 |
| components/map/sketch-editor.tsx:452-465 | Enter/Escape（sketch 激活时） | window | 有 |
| lib/hooks/use-dialog-focus.ts:48-64 | Tab 围栏 + Escape | document（dialog open） | —（7 个 dialog 共用） |
| components/layout/nav-rail.tsx:157-175 | Arrow 键 | tablist 局部 onKeyDown | — |
| components/settings/settings-panel.tsx:107-124 | Arrow 键 | tablist 局部 | — |

冲突标注：Escape 并列多 listener（无 stopImmediatePropagation）；命令面板打开时需「面板内 Escape 只关面板」。无冲突前缀组合仅 Ctrl/Cmd+{Enter,S,Z,Y}。**Ctrl+K、`?` 均空闲**。

### 1.3 undo API（lib/workbench/undo.ts，349 行）——只增不改的基线

| 导出 | 行 | 形状 |
|---|---|---|
| `UNDO_MAX_HISTORY` | :27 | 50（有界栈） |
| `WorkbenchCommand` | :30-42 | `{id; label; kind: OpLogEntry['type']; ts; actor:'user'\|'agent'; sessionId; layerIds?; undo(); redo()}` |
| 栈 | :44-51 | 模块级 `undoStack/redoStack` + version + listener Set（useSyncExternalStore 订阅；刻意不进 zustand 防循环依赖） |
| `subscribeUndo`/`getUndoSnapshot` | :57/:62 | 外部订阅（P5 时间线消费口） |
| `recordCommand`/`journalOnly` | :88/:110 | 入栈+清 redo+写 opsLog / 不可逆只落日志 |
| `undo()`/`redo()` | :128/:146 | 弹栈执行，journal 记 undo/redo |
| `clearUndoHistory()` | :165 | 会话切换清栈（use-workspace-session.ts:195,369） |
| `docCommand`/`withDocUndo` | :181/:258 | 组织态 delta 反演 |
| `presentationCommand`/`reorderCommand` | :270/:314 | 显隐/不透明度、重排 |

消费方：page.tsx:144（全局键）、layers-tab.tsx:59,939（按钮）、user-mutation.ts:284,569,606、visibility-transaction.ts:334、sketch-editor.tsx:134,412。**P5 只读消费 subscribeUndo/getUndoSnapshot + 调 undo()/redo()，不新增栈语义。**

### 1.4 opsLog

- Store：`lib/store/slices/uiSlice.ts:177-184`，`pushOpLog` **前插**，上限 `MAX_OPS_LOG=200`（:13）；不持久化（useHudStore.ts:120-155 partialize 白名单外）。
- 条目：`lib/store/hud-types.ts:25-49` `{id; type; label; time: string; detail?; actor?; reversible?}`；type 封闭词表 `add|remove|toggle|flyto|style|sketch|undo|redo|lock|group|reorder|lock_conflict`。
- `time` 是 `toLocaleTimeString('zh-CN')` 显示串（undo.ts:71），**非 epoch**。⚠ 排序只能按数组序（前插=最新先）；P5 时间线按数组序渲染。
- 消费：agent-run-panel.tsx:73,105（近 8 条）；workspace-content.ts:17。

## 2. Story 页现状表

### 2.1 app/story/page.tsx（276 行）

- 数据：`GET /api/v1/chat/sessions/{id}` → `{messages}`；`GET /sessions/{id}/map-state` → `{map_state}`（:42-43,:140-151）；sessionId 来自 `useSearchParams`。
- 映射：`messages.map()` 线性渲染，`activeIndex` 高亮 + `PLAY_INTERVAL_MS=2500` 定时推进（:26,:70-78）；ScrollSpy 是空壳 TODO（:170-173）。
- 地图快照：**无每帧快照**，仅会话级最终态 `applyStoryMapState`（lib/session/map-state-restore.ts:420-436）。`SessionMapState` = base_layer + viewport + layers[] + mapspec{layers,view}（:40-62）；相机仅 `mapspec.view.framed===true` 时 fly_to（:86-105）。
- 分享：复制 location.href（:85-99）。地图：MapPanel dynamic ssr:false（:6-9）。
- map-state 写入端：`POST /sessions/{id}/map-state`（app/api/routes/chat.py:1423-1498），`MapStatePushRequest{viewport?, layers?(≤128), base_layer?, seq?}`，256KB 上限。

### 2.2 产物（artifact）类型与 ref 通道

- 后端：`GET /sessions/{sid}/chart-artifacts/{ref_id}`（chat.py:1321-1345，前缀 `ref:chart-`）→ `{chart: ChartData}`；`GET /sessions/{sid}/table-artifacts/{ref_id}`（chat.py:1347-1380，前缀 `ref:table-|stats-|grid-|admin-`）→ `{table:{columns,rows}}`。**P3 chart/表回放即消费这两个端点。**
- 前端类型：`lib/results/types.ts`（OutputKind :48 'vector|raster|statistic|table|image|none'）；ChartMessage charts?: unknown[]（useChatStore.ts:3-14）。
- ref 提货券机制：大结果不进 LLM 上下文，`ref:` 游标流转（README.md:62,286,311；tile-url.ts MVT>5000 要素；data-fabric.ts:439 fetchRefGeoJSON；map-state-restore.ts:390-392）。

### 2.3 渲染组件

- `ChartCore`（components/chat/chart-core.tsx:572-583）：`{chart: ChartData; height?; highlightedCategories?; onSelectCategory?}`，18 种 kind，主题读 `data-theme`（:50-56）。
- `TabularDataGrid`（components/explorer/tabular-data-grid.tsx:35-64）：`{data?（rows|GeoJSON|QueryResult）, columns?, totalCount?, loading?, defaultPageSize=10, enableSearch/Sort/RowCopy, onRowClick?}`；**分页式非虚拟化**（slice :431-434）。
- exporter（lib/map-kit/exporter.ts，2002 行）：`exportToPDF(canvas, title, subtitle?, options{paperSize, orientation, author, dataSource, textLayer, pages?:{canvas,title?}[], onDegradation?})` :897 —— **pages 多页参数可直供叙事 PDF**；`captureMapCanvas` :49；`runExport` :1505；`uploadExport` :1163 → POST /api/v1/export（栅格链）。

## 3. data-fabric query 契约与 history 检索能力表

### 3.1 查询链路（app/api/routes/data_fabric.py，934 行）

| 端点 | 行 | 说明 |
|---|---|---|
| GET /data-fabric/sources | :381 | `DataSource[]` 含 `capabilities: string[]` |
| GET /data-fabric/catalog | :553 | `q`(ILIKE name/title/description :600-606)、source_id、geometry_type、availability、limit(1-200 默认50)、offset、summary → `{total, items:[{id,source_id,name,title,description,geometry_type,crs,bbox,availability,updated_at}]}` |
| GET /data-fabric/catalog/{id} | :654 | 全量含 meta_profile/descriptor |
| GET .../preview | :700 | limit(1-100)，需认证 → `{features, total_count, schema_info}` |
| POST .../explain | :745 | `{query_spec?}` → `{status, explain: string[], plan, capabilities, dataset{geometry_type,srs,feature_count}}`；422 非法 |
| **POST .../query** | :856 | body=`QuerySpec`，需认证；成功 `q_res.model_dump()`；413 ResultTooLarge / 502 DataFabricError（`{success:false,error_type,error,details}`）；400 兜底；404 不存在/跨租户 |

- `QuerySpec`（app/schemas/data_fabric_schema.py:106-124）：`bbox?; columns?/fields?; limit=100; offset; filter_expr?; where?: str|dict; datetime_range?; zoom?; tile_coords?`，`extra="allow"`（aggregate/group_by/order_by/result_mode/cursor/sample_size 经 normalize 归一）。
- `QueryResult`（:127-148）：`dataset_id, query_spec, features, data, ref_id, total_count, total_matching, returned_count, truncated, is_pushed_down, payload_type, execution_time_seconds, schema_info, metadata, next_cursor, has_more, result_mode('descriptor|statistics|sample|features|materialize|vector_tile'), is_demo`。
- pushdown 披露：`metadata["query_plan"]` → 前端 `QueryPlanInfo`（lib/api/data-fabric.ts:109-137：pushed_filters/local_filters、pushed_projection/spatial/aggregation/sort、pagination_strategy、estimated_rows/bytes、execution_mode 'pushdown|local_fallback|hybrid'、fallback_reason、warnings、steps[]）；`metadata["query_evidence"]` → QueryEvidenceInfo（:141-156）。
- 客户端：`dataFabricApi`（lib/api/data-fabric.ts:234）——listSpatialCatalog :316、previewCatalogItem :373、queryCatalogItem :383、explainCatalogItem :400、materializeCatalogItem :412、fetchRefGeoJSON :439。
- 能力：sync 时 `adapter.capabilities()` 持久化（manager.py:205-228）；explain 输出 `capabilities` 键（:739）；descriptor.query_capabilities（schema :97）。**危险语句守卫依据：query 端点为只读检索（QuerySpec 无写语义），写类语句一律前端拦截并诚实披露。**
- 上图管线参照：data-sources-tab.tsx:292-355（实例化至图层：materialize → 加层）；materialize 端点 :893 接受 `query_spec`——**查询控制台「结果上图」= 以当前 QuerySpec materialize 后走既有加层管线**。

### 3.2 history / 会话检索能力边界

- `GET /api/v1/chat/sessions`（chat.py:1198-1225）：仅 limit(≤200)/offset，**无 q=/search= 参数**；返回 `{sessions:[{id,title,createdAt,updatedAt}]}` 无消息内容。
- `GET /sessions/{id}`（:1227-1291）：limit(默认200,≤200)/offset，`{messages:[{id,role,content,timestamp}]}`——全文只能逐会话拉取。
- 服务层 history_service_async.py:491-508 纯 order_by updated_at。
- 前端 history-drawer.tsx：数据=useHudStore.sessions，搜索为纯客户端 title 过滤（:19-27），tags 恒空。

**ADR-0147 决策输入：后端无全文检索端点 → P4 做 client 侧本地索引（会话列表 + 按需拉取 transcripts，LRU 截断），后端全文端点列为协调点。**

### 3.3 前端本地持久化

localStorage（无 IndexedDB）：`geoagent-settings`（useHudStore.ts:60，partialize :120-155）、auth tokens（tokenStore.ts:83-131）、会话锚 session-anchor.ts:23-53。transcripts 仅内存（useChatStore.ts:29-38 非持久化）。**P2 查询历史 / P4 索引 / P3 章节编排用独立 localStorage key，不动 geoagent-settings。**

## 4. a11y 与动效约定表

- Focus trap 现成：`lib/utils/focus.ts`（getTabbableIn/trapTabKey）+ `lib/hooks/use-dialog-focus.ts:22`（`UseDialogFocusOptions{open, containerRef, onEscape?, initialFocusSelector?}`，初始聚焦/归还焦点/Tab 围栏/Escape）——**命令面板与 tour 复用**。
- 播报约定：集中式 announcer（components/chat/chat-announcer.tsx:28,84）/ toast（ui/toast.tsx:102）；listbox+aria-activedescendant 先例 baselayer-switcher.tsx:117；**无 combobox 先例，面板按 APG combobox 自建**。
- inert：`lib/hooks/use-inert.ts:14`。
- reduced-motion 双轨：JS=`usePrefersReducedMotion()`（lib/hooks/use-prefers-reduced-motion.ts:11）；CSS=globals.css:673-676 media query。**story 缓动/tour 动画必须走 hook 门控。**
- 主题：`<html>` class `dark` + `data-theme` 双写（page.tsx:266-271；layout.tsx:34-39 no-flash）；chart-core 读 data-theme。
- visual snapshot：无像素 diff 服务；基线采集器 test/visual/capture.mjs（:3311 + route 拦截 fixtures，1920/1440/1366/1024 × light/dark）；契约测试 test/design-system/{contrast,tokens.contract,visual-system.contract}.test.tsx。**P7 明/暗断言走组件级双主题渲染契约（与本仓惯例一致），不引入像素 diff。**

## 5. 测试与构建约定表

- vitest.config.ts：jsdom、globals、timeout 15s、setup `test/setup.ts`、include `**/*.{test,spec}.{ts,tsx}`、alias `@`。coverage v8，**thresholds lines 75 / functions 70 / statements 75 / branches 60**（:26-31）。
- setup.ts：localStorage mock（vi.fn）、Blob.arrayBuffer polyfill、Canvas 2D stub + toDataURL 固定 PNG、offsetParent polyfill。
- scripts：dev/build/start/lint(--max-warnings 0)/test/test:watch/test:coverage/test:ci/typecheck（双 tsconfig）。无 test:e2e script。
- **⚠ 勘察修正：msw 未安装、全仓零使用。**网络 mock 惯例=模块级 vi.mock(transport/apiFetch/dataFabricApi) + capture.mjs playwright route 拦截。本线遵循仓库惯例（fixtures 模块 + vi.mock），不新增 msw 依赖；PR 复核纪要注明。
- Playwright：仅 devDep ^1.63 + capture.mjs；无 playwright.config、无 spec。
- next.config.mjs：reactStrictMode、transpilePackages(react-map-gl,maplibre-gl)、output standalone、optimizePackageImports。**App Router page 文件只允许导出 page 组件与 route-segment 配置**（story/page.tsx:17-19 注释明示）——P3 新增文件放 page.tsx 外。

## 6. Rail tab 与主入口清单（命令面板种子）

- `LeftTab` 10 值（hud-types.ts:96）：chat|project|layers|components|analysis|exports|export_layout|data_sources|tasks|results（exports=export_layout 别名）。RAIL_GROUPS（nav-rail.tsx:54-68）；activateTab :118；switchMode :132；三模式 `explore|analyze|compose`（workbenchSlice.ts:31-40）+ MODE_TABS。
- Tab 渲染：context-panel.tsx:366-419。
- 可注册动作（file:line）：顶栏 新会话/历史/3D/tweaks/设置（top-bar.tsx:145/154/168/193/202）；chat 建议 prompt（suggested-prompts.tsx:10-27）；layers undo/redo 按钮（layers-tab.tsx:59,939）；data_sources 注册/探测/同步/预览/实例化（data-fabric.ts:251/290/302、data-sources-tab.tsx:292-355）；components dock 操作（components-tab.tsx:215-270）；analysis 工具提交（analysis-tab.tsx:97,141,241）；tasks 跳转/取消/重试（tasks-tab.tsx:150-237）；project 建/重跑/回放/恢复（project-tab.tsx:47-302）；制图 export_map（map-studio-tab.tsx:505-518 → exportCommands.ts:26）；模板库（nav-rail.tsx:301-309、template-gallery-v2.tsx:62-66）；对比模式（comparison-view.tsx、ComparisonKind workbenchSlice.ts:57-58）；底图（baselayer-switcher.tsx）；主题（embodied-hud.tsx:76-79、tweaks-panel.tsx:216）。
- **地图命令词表**：`COMMAND_CATALOGUE`（lib/map-commands/catalogue.ts:22，九域合并）+ `MapCommandContext`/`CommandEntry`（types.ts:22-60）——UI 命令注册表（P1）与其正交：P1 注册「面板动作命令」，地图命令可由面板经 `useMapAction()`（lib/contexts/map-action-context.tsx:448）dispatch。
- 设置面板：settings-panel.tsx:74（APG tablist + useDialogFocus），开关=settingsOpen（uiSlice）——**tour 重看入口挂这里**。

## 7. #1213 评估（ADR-0147 决策输入）

- 端点：`POST /api/v1/export/vector-pdf`（app/api/routes/map.py:296-373）——`VectorPdfRequest{mapspec: dict, title?, target_dpi?}`，WeasyPrint publication 链，429 busy / 503 unavailable / 400 typed / 413 载荷。
- 契约本质：**单 MapSpec → 出版级地图 PDF（spec 级帧）**。StoryMap 章节是异构文档（地图 + chart + 表格 + 文本），端点无 chart/table 帧位；章节图表是会话 ref 产物，需逐章内联且 429/503 面扩大。
- **结论（写入 ADR-0147）：叙事 PDF 不消费 vector-pdf，走既有栅格多页链（exportToPDF pages 参数）；#1213 的「单图矢量出版接线」缺口继续开放，留给制图面后续线（PR 协调点重申）。**

## 8. 实现映射总表

| 阶段 | 关键依赖 | 落点 |
|---|---|---|
| P1 | Ctrl+K/`?` 空闲；useDialogFocus；announcer 惯例；COMMAND_CATALOGUE 派生 | lib/commands/**（新）、components/command/**（新）、lib/hooks/use-command-palette.ts（新） |
| P2 | dataFabricApi.query/explain/materialize；QueryPlanInfo；TabularDataGrid 吃 QueryResult；code-highlight tokenizer 支持 sql（tokenizer.ts:84,230） | components/console/**（新）+ drawers 挂载 |
| P3 | chart/table artifact 端点；exportToPDF pages；captureMapCanvas；applyStoryMapState；usePrefersReducedMotion | app/story/**（归本线） |
| P4 | sessions API 无搜索→本地索引；transcripts 按需拉取；LRU | components/search/**（新） |
| P5 | subscribeUndo/getUndoSnapshot 只读消费；opsLog 前插序 | components/workbench/**（新）或 drawers |
| P6 | useDialogFocus；usePrefersReducedMotion；settings-panel 挂重看入口 | components/onboarding/**（新） |
