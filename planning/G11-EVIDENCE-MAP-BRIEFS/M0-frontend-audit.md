# G11 证据化地图简报 —— 前端现状审计（worktree: webgis-wt-g11-evidence-map-briefs-20261108T160816Z @ master 8eb8aa1）

以下所有路径均为绝对路径，根目录 `/home/kevin/projects/webgis/webgis-wt-g11-evidence-map-briefs-20261108T160816Z`（下文以 `$W` 简写）。

---

## 1. 技术栈与目录结构

- **框架**：Next.js 16.4.4（App Router）+ React 19.3.1 + TypeScript 5 + Tailwind CSS 3.4 + lucide-react + framer-motion + recharts。见 `$W/frontend/package.json:18-35`。地图栈：maplibre-gl 6.10 + react-map-gl 8.1 + three（`package.json:23,28,33`）。
- **路由**：App Router 文件路由，无集中路由表。页面仅 3 个：
  - `$W/frontend/app/page.tsx` —— 主工作台（TopBar + NavRail + ContextPanel + MapPanel + EmbodiedHud）
  - `$W/frontend/app/story/page.tsx:8` —— StoryMap 回放（`StoryView`）
  - `$W/frontend/app/geoai/page.tsx:5` —— GeoAI 面板壳
  - `$W/frontend/app/api/maplibre-worker/` —— maplibre worker 路由
- **状态管理**：zustand 5。核心 store：`$W/frontend/lib/store/useHudStore.ts:103`（`create<HudState>`，图层/HUD/导出设置等）、`$W/frontend/lib/store/useChatStore.ts:29`（消息单一 owner）、slices 在 `$W/frontend/lib/store/slices/`（workbench/results/layers/ui/…）。MapSpec 会话游标是独立外部 store：`$W/frontend/lib/mapspec/session-cursor.ts:90-243`（`getCommittedMapSpec:145`、`commitMapSpecDocument:149`、pending presentation 合并）。
- **UI 组件库**：无第三方库；自建设计系统（Visual System V4，Tailwind token 类 `surface-*/ink-*/status-*`），`$W/frontend/components/ui/` 只有 `toast.tsx`（`useToastStore`，全局 toast）。共享件在 `components/shared/`。
- **i18n**：自建 next-intl 风格体系：`$W/frontend/lib/i18n/config.ts:16-20`（`SUPPORTED_LOCALES=['zh-CN','en-US']`，默认 zh-CN）、`lib/i18n/messages.ts:22,54,89,127,162`（catalog 聚合登记处，新增 namespace 在此登记）、`lib/i18n/i18n-provider.tsx`、`lib/i18n/useT.ts`（hook）、`lib/i18n/t.ts`（命令式）。资源：`$W/frontend/messages/{zh-CN,en-US}/*.json`（34 个 namespace）。
- **G11 判断**：技术栈与设计系统可直接复用；新增 G11 文案应在 `messages/*/story.json` 或新建 namespace 并在 `lib/i18n/messages.ts` 登记。

## 2. StoryMap 前端入口

- **入口链路**：主工作台命令面板 `session.story` → `window.open('/story?session_id=…')`：`$W/frontend/app/page.tsx:308-318`；文案 key `commands.storyView`（`messages/zh-CN/commands.json:14`）。Story 页 opener 与新 tab 间用 postMessage 交接匿名会话 owner token / 登录态：`$W/frontend/lib/session/story-token-handoff.ts:33`（`installStoryOwnerTokenResponder`，page.tsx 挂载）、`:62`（`requestStoryOwnerToken`）。
- **核心视图**：`$W/frontend/app/story/story-view.tsx:84`（`StoryView`，959 行）。装载流程（:310-430）：`GET /chat/sessions/{id}` → `GET …/map-state`（`applyStoryMapState` 还原地图）→ `loadSessionStorySpec`（已存资产优先）→ 失败则 `compileStorySpec` 现场编译；spec 不合法（`isValidStorySpecDto`）一律降级本地派生章节。
- **章节模型**：`$W/frontend/app/story/chapters.ts:16-210` —— `deriveChapters`（1 消息=1 章节）、`ChapterOrchestration` localStorage 覆盖层（key `geoagent-story-orchestration-v1:{sessionId}`，:90）、`extractFlyTo`（从消息 json 围栏提取相机，:174）、`assignChapterArtifacts`（`ref:chart|table|stats|grid|admin-*` 归章，:144）。
- **编排模式组件**：`$W/frontend/components/story/story-narrator.tsx:63`（`StoryNarrator`，滚动驱动活跃章，`pickActiveChapter:55`）；`$W/frontend/components/story/story-dashboard.tsx:27`（`StoryDashboard`，章节联动 widget 看板，`DashboardWidget` 模型 :14-19，kind=chart/table/kpi/stats）；`$W/frontend/app/story/chapter-scrubber.tsx:13`（播放/步进/seek）；`$W/frontend/app/story/chapter-artifact.tsx:22`（章节产物回放：chart→`ChartCore`，table→`TabularDataGrid`）。
- **API 客户端**：`$W/frontend/lib/api/storymap.ts` —— `compileStorySpec:69`（POST `/api/v1/storymap/compile`）、`exportStoryBundle:82`（POST `/api/v1/storymap/export` → HTML Blob）、`saveSessionStorySpec:99`、`loadSessionStorySpec:114`、`isValidStorySpecDto:135`、`specToNarratorView:196`（DTO→视图模型，camera/widget/scene 映射）。后端对应 `$W/app/api/routes/storymap.py:33-128`。
- **消息词典**：`$W/frontend/messages/en-US/story.json`（75 行，与 zh-CN 同构）。关键 key：`orchestrated`、`exportBundle`/`exportBundleTitle`、`saveAsset`/`assetSaved`、`bundleExported`/`bundleFailed`、`shareCopied`、`pdfRenderProgress`、`takeoverPaused/Resume`、`sceneApplyFailed` 等。
- **G11 复用/缺口**：查看/播放/导出链路完备。**关键缺口：章节编辑面板只在本地派生模式可用**（`story-view.tsx:772` 的 `!specView &&` 条件），且仅有重命名/隐藏/重排（:786-865），**无新增/删除章节、无正文编辑、无地图选择、无指标/图表绑定、无证据侧栏**；后端 spec 模式下章节结构是只读权威。

## 3. 地图容器组件

- **主容器**：`$W/frontend/components/map/map-panel.tsx:273`（`MapPanel`，2011 行；react-map-gl `Map` + MapSpecRuntime + SceneController + SketchEditor + LegendStack + MapDecorations + MapToolbarHUD）。Story 页直接复用（`story-view.tsx:23-26` dynamic import，:942-944 挂载）。
- **MapSpec 驱动**：committed spec（真相源）`lib/mapspec/session-cursor.ts:145`；live 合成 `lib/mapspec/live-spec.ts`（`composeLiveMapSpec`）；运行时渲染 `lib/mapspec-runtime/runtime.ts:86`（`MapSpecRuntime`）；MapSpec→MapLibre style 适配 `lib/mapspec-runtime/adapter.ts`；用户场景/图层突变 `lib/mapspec/user-mutation.ts`、`scene-mutation.ts`（`commitUserScene`、`mergeSceneRotary`，story-view.tsx:38 引用）。
- **动作通道**：`$W/frontend/lib/contexts/map-action-context.tsx`（`dispatchAction`），命令 union 在 `lib/types.ts:119`（含 `fly_to`、`export_map`、`export_vector_pdf`、`chart_*`、`MAP_FINALIZATION`）。命令臂：镜头 `lib/map-commands/viewCommands.ts:245`（fly_to）；导出 `lib/map-commands/exportCommands.ts:44`（`export_vector_pdf`）、`:76`（`export_map`）；相机仲裁 `lib/map-commands/camera-arbitration.ts`；图表命令 `lib/map-commands/chartCommands.ts`。
- **图例/地图组件**：`components/map/legend-stack.tsx`、`floating-legend.tsx`、`thematic-legend.tsx`、`baselayer-switcher.tsx`；制图组件库 `components/map/map-components/`（`title.tsx`、`north-arrow.tsx`、`scale-bar.tsx`、`colorbar.tsx`、`statistics-panel.tsx`、`chart-panel.tsx`、`table-panel.tsx`、`uncertainty-panel.tsx`、`methodology-note.tsx`、`inset-map.tsx` 等，注册表 `registry.ts`）。
- **快照/导出引擎**：`$W/frontend/lib/map-kit/exporter.ts` —— `captureMapCanvas:85`（canvas→Blob 快照）、`exportToPDF:958`（多页栅格 PDF）、`downloadBlob:1185`、`uploadExport:1341`（上传到 `/api/v1/export/…`）、`composeExportSpec:1503`、`runExport:1833`（`MapExporterEngine.export`）。G04 publication 前端投影：`lib/api/publication.ts:54`（`exportVectorPdf`，POST `/api/v1/export/vector-pdf`，`pageProfile` 参数 :50-51 即 G04 #1999 打印档）；编排封装 `lib/map-kit/publication-export.ts:130`（`runVectorPdfExport`，503 回退栅格）。
- **导出 UI 入口**：`$W/frontend/components/sidebar/map-studio-tab.tsx:665-676`（「发布与导出」按钮，按 `exportSettings.format` 分派 `export_vector_pdf`/`export_map`，**authUser 登录门控**）。矢量 SVG 导出 `lib/map-kit/vector-svg-export.ts`。
- **G11 判断**：章节地图选择/相机 keyframe 编辑可直接复用 `MapPanel` + `getCommittedMapSpec` + `captureMapCanvas`；发布预检可借用 `exportVectorPdf` 的 typed error code 模式（`vectorPdfErrorCode:73`）。

## 4. 前端 API 消费层

- **统一 transport（手写 fetch wrapper，非 openapi 生成）**：`$W/frontend/lib/api/transport.ts` —— `apiFetch:319`（自动 `Authorization: Bearer`（:201-202）、`X-Request-ID`、30s 超时、幂等方法重试、**401 单飞 refresh-and-retry**）、`apiFetchBlob:385`（二进制下载 + Content-Disposition filename）、`openStream:434`（SSE，同样 401 刷新）、`ApiError:101`、`describeApiError:155`。API base：`lib/api/config.ts:16`（`NEXT_PUBLIC_API_URL ?? 'http://localhost:8001'`）。匿名会话 owner token 走 `X-Session-Token`（transport.ts:73,198）。
- **端点模块**：`$W/frontend/lib/api/*.ts` 约 40 个（chat、publication、storymap、project-assets、jobs、explorer…），全部走同一 transport。
- **SSE 投影**：解析器 `lib/api/sse-stream-parser.ts:60`（`parseSSEStream` async generator）；主 hook `lib/hooks/use-sse-stream.ts:330`（`useSSEStream`：token 批处理、tool call 状态机、图层 fetch、explorer 独立流）；消息变换纯 reducer `$W/frontend/lib/chat/message-reducer.ts`（可确定性回放测试，消息 cap 200）。
- **工具卡渲染**：`ToolCallEntry` 接口 `$W/frontend/components/chat/tool-call-card.tsx:26`；`ToolCallChain` 在 `$W/frontend/components/sidebar/chat-tab.tsx:8` 渲染；专用结果卡 `components/chat/{cartography,h3-lisa,isochrone}-result-card.tsx`；图表 `components/chat/chart-core.tsx:586`（`ChartCore`，recharts）与 `chart-renderer.tsx:15`（chat-tab.tsx:36 dynamic）。
- **认证流程**：登录表单 `$W/frontend/components/settings/account-section.tsx:107`（设置面板 → Account tab，`#auth-identifier`/`#auth-password`）；`lib/api/auth.ts:30`（`login`）、`:43`（`logout`）；token 存储 `$W/frontend/lib/auth/tokenStore.ts` —— **JWT 内存态**（localStorage 只存 userId 供跨 tab 同步 :87-112，刷新令牌单飞 `refreshAuthToken:215`），`NEXT_PUBLIC_AUTH_DISABLED=true` 开发期 bypass（:26-28，生产构建死代码消除）。跨 tab Story 交接见 `lib/session/story-token-handoff.ts`。受保护下载 helper：`lib/api/authenticated-download.ts:33`。
- **G11 判断**：API client 完全可复用；新增 G11 端点（预检/冻结/release 下载）照 `lib/api/storymap.ts` 模式加函数即可；release 私有 HTML 下载用 `apiFetchBlob`（自动带 Bearer/ownerToken + filename）。

## 5. 证据/产物展示

- **图表**：`components/chat/chart-core.tsx:586`（`ChartCore`，主题化 recharts）；数据适配 `lib/chart-adapter.ts:23`（`adaptChartData`）；ref 产物管线 `lib/map-components/chart-artifact.ts`（`loadChartArtifact`）/ `table-data.ts`（`loadTableArtifact`，缓存+会话 cursor 鉴权）——Story 章节产物（`chapter-artifact.tsx`）与地图 chart-panel 同源。
- **指标卡/结果**：`lib/results/types.ts:68`（`ResultMetric`）、`:122`（`SuggestedActionKind` 含 `'export'`）、`lib/results/families.ts:163`（`extractMetrics`）；UI `$W/frontend/components/sidebar/results/result-detail.tsx:94`（`MetricItem` 指标卡）、`result-list.tsx`。地图内嵌统计：`components/map/map-components/statistics-panel.tsx`、`uncertainty-panel.tsx`、`methodology-note.tsx`。
- **trace/回放**：工具链卡 `components/chat/tool-call-card.tsx`（running/completed/failed + 耗时）；执行时间线 `components/agent/run-timeline.tsx`；深度评审抽屉 `components/workbench/review-drawer.tsx:78`；Agent 运维座舱 `components/cockpit/*`（`agent-ops-cockpit.tsx`、`execution-views.tsx`）。证据专用逻辑：`lib/layers/render-evidence.ts`、`lib/layers/filter-evidence.ts`、`lib/mapspec-runtime/runtime-evidence.ts`；后端叙事证据 `$W/app/services/gis_harness/evidence_claim/narrative.py`。
- **G11 判断**：证据侧栏可由 `result-detail.tsx`（MetricItem）+ `tool-call-card` + chart/table ref 管线组合而成，缺的只是"按章节聚合证据"的容器组件（章节归属算法 `chapters.ts:144 assignChapterArtifacts` 已有雏形）。

## 6. 导出/下载 UI（现状）

- **Story 页工具栏**（`story-view.tsx:652-699`）：
  - `Share2`：复制当前 URL（`handleShare:447`，toast `story.shareCopied`）
  - `ImageDown`：OG 分享卡 PNG（`handleShareCard:471` → `composeShareCard`，`app/story/narrative-export.ts:40`，canvas 合成）
  - `FileDown`：叙事 PDF（`handleNarrativePdf:524` → `exportNarrativePdf` 逐章 fly_to + `captureMapCanvas` 多页合成，进度条 `pdfRenderProgress`）
  - `Save`：保存 StoryMap 资产（`handleSaveAsset:591` → `saveSessionStorySpec`，revision 单调）
  - `HardDriveDownload`：离线 bundle HTML（`handleExportBundle:571` → `exportStoryBundle`）；**Save/Bundle 按钮仅在编排模式（specView）显示**（:678-699）
- **主工作台**：map-studio-tab「发布与导出」（见第 3 点）；`downloadBlob`=`lib/map-kit/exporter.ts:1185`；上传产物经 `uploadExport:1341` 走 `/api/v1/export/download/{filename}` 下载（`lib/api/authenticated-download.ts:26`）。
- **G11 判断**：分享链接、PNG、PDF、HTML bundle、资产保存全部已有。**缺口：无「发布预检」UI、无「冻结 release」概念、无 release 私有 HTML 下载入口**（grep `preflight|precheck|frozen|release` 在 frontend 无业务命中）。

## 7. 浏览器 e2e 测试

- **框架/位置**：Playwright 1.64（devDependency），配置 `$W/frontend/playwright.config.ts`：testDir `./e2e`、`testMatch **/*.journey.ts`、mock 模式自动起 `pnpm exec next dev -p 3310`（:60-71）、retries=0、clipboard 权限（StoryMap 分享断言依赖）。journeys 在 `$W/frontend/e2e/journeys/`（j1 上传分析导出 … j12），单测用 vitest（`vitest.config.ts`，jsdom；命名互斥避免收集 journey）。
- **双模式**：`E2E_MODE=mock|real`（`e2e/helpers/mode.ts`）；real 模式需真实后端监听 `E2E_API_URL`（默认 :8001）+ `E2E_USER/E2E_PASS`（夜班车道 `manage.py create_admin` 供给）。
- **真实后端认证写法**：`e2e/helpers/bootstrap.ts:47`（`loginViaApi`：`page.request.post(API_ORIGIN/api/v1/auth/login)` 取 token，凭据只存 runner）+ `:95`（`authenticateShell`：真实 UI 设置→Account 表单登录，断言 `#auth-identifier`）；`e2e/helpers/real-chat.ts`（`seedConversationViaApi` 真实 chat API 造会话）；mock 桩 `e2e/fixtures/api-stubs.ts`（`installJourneyStubs` context 级路由，覆盖弹窗页）。
- **StoryMap e2e**：`$W/frontend/e2e/journeys/j4-storymap-share.journey.ts` —— mock 变体（:104）与 real 变体（:133）：Ctrl+K 命令面板开 Story 弹窗（断言 popup 请求带 Bearer，:72）、播放推进（scrubber value）、分享链接复制回放（:90 `replayLink`）、以及 real 模式外部者打开分享链接必须 401/403/404（:163-170，所有权契约）。fixtures：`e2e/fixtures/story-seed.json`。
- **G11 判断**：新 G11 journey 应照 j4 双变体模式写；mock 需在 `api-stubs.ts` 补 storymap 预检/冻结/release 端点桩。

## 8. 构建与本地 dev

- **scripts**（`$W/frontend/package.json:5-17`）：`dev` = `next dev -H 0.0.0.0`（默认 3000）、`build` = `next build --webpack`、`build:turbopack`、`test`=vitest run、`typecheck`（双 tsconfig）、`i18n:extract`。包管理 pnpm（workspace + lock）。
- **端口约定**：前端 3000（`frontend/docker-compose.yml:9-18`）；后端 API 默认 `http://localhost:8001`（`lib/api/config.ts:16`），`manage.py dev`/dev compose 为 18000（`frontend/README.md` 快速开始节需 `.env.local` 写 `NEXT_PUBLIC_API_URL`）；e2e dev 端口 3310（playwright.config.ts:26）。生产 build 同源（`NEXT_PUBLIC_API_URL=""`），CSP/nonce 在 `$W/frontend/proxy.ts`（middleware），安全头在 `next.config.mjs:16-31`。

---

## G11 前端最小增量清单（结论）

**复用（零改动）**：`MapPanel`、`MapSpecRuntime`/`getCommittedMapSpec`、`captureMapCanvas`/`downloadBlob`、`ChartCore`/chart-artifact/table-data ref 管线、`StoryNarrator`/`StoryDashboard`/`ChapterScrubber`/`ChapterArtifact`、transport（`apiFetch`/`apiFetchBlob`/ownerToken）、`useToastStore`、i18n story namespace、j4 e2e 骨架。

**BRF-03（编辑器联动）需要新增/修改**：
1. 新增 `$W/frontend/app/story/story-editor.tsx`（或 `components/story/`）：直接编辑 `StoryMapSpecDto` 的章节 CRUD（新增/删除/重排/正文编辑），复用本地编排面板的交互样式（`story-view.tsx:772-867` 的列表+上下移+重命名模式）与 `StoryMarkdown` 预览；保存走既有 `saveSessionStorySpec`（需 ownerToken）。
2. 修改 `story-view.tsx`：解除 `:772` 的 `!specView` 门控，让编排模式也能进入编辑器；工具栏加「编辑」入口。
3. 新增章节地图/相机选择器：从 `getCommittedMapSpec` + 当前地图实例取 center/zoom/pitch/bearing 写 `camera_keyframes`（复用 `specToNarratorView` 的取帧语义，`storymap.ts:196-226`）；预览复用 `dispatchAction fly_to`。
4. 新增指标/图表绑定面板：编辑 `linked_widget_ids`/`linked_widgets`，数据源复用 `assignChapterArtifacts` 的 ref 扫描 + `loadChartArtifact/loadTableArtifact`；渲染复用 `StoryDashboard`/`ChapterArtifact`。
5. 新增证据侧栏组件：聚合该章节的 tool-call 证据（复用 `ToolCallEntry`/`result-detail.tsx` 的 `MetricItem`）。

**BRF-06（分享/下载入口）需要新增/修改**：
6. 新增发布预检面板（预检结果列表 + 阻断项展示），照 `publication.ts` 的 typed error code 模式在 `lib/api/` 加预检 client；UI 挂 `story-view.tsx:652-699` 按钮组。
7. 新增「冻结 release」动作 + release 状态徽标（冻结后编辑器转只读）；新 API client 函数照 `saveSessionStorySpec` 形状。
8. 修改 `handleExportBundle`（`story-view.tsx:571`）：支持按 release 下载私有 HTML（`apiFetchBlob` 自动带认证；匿名会话需 `requestStoryOwnerToken` 通道）。
9. `messages/{zh-CN,en-US}/story.json` 增补预检/冻结/release 文案 key；`lib/i18n/messages.ts` 无需改（namespace 已登记）。
10. 新增 `e2e/journeys/j13-*.journey.ts`（编辑→预检→冻结→私有下载双模式变体），`e2e/fixtures/api-stubs.ts` 补新端点桩；real 模式复用 `loginViaApi`/`seedConversationViaApi`。