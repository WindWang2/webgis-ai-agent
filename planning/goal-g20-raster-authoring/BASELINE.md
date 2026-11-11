# G20-RASTER-AUTHORING — BASELINE

## 仓库身份 / 基线（2026-11-10 执行时重新核对）

- `git remote -v`: `origin https://github.com/WindWang2/webgis-ai-agent.git`；
  `gh repo view WindWang2/webgis-ai-agent --json nameWithOwner,defaultBranchRef`
  → `WindWang2/webgis-ai-agent`, `master`。
- 主工作区 `git status --short` 有用户未提交文件（`.claude/`, `.tmp_*`, `dev-batch-g/`,
  `research/`, `docs/reviews/SWARM_DEEP_REVIEW_2026-10-19.md`）—— **只读，不覆盖**。
- `git worktree list --porcelain` 在执行前：主工作区（master @ 816705218a5a…）、
  `fix/deep-review-2026-10-28`、两个兄弟工作树 `goal-g17-import-workbench-20261110T131224Z`、
  `goal-g18-dataset-delivery-20261110T131127Z`。G17/G18 已在本机并行开发，本任务不触碰其 worktree。
- `git fetch origin --prune` 后：`origin/master = 816705218a5aa6202bf15d745cb31cddb3437f44`
  （与研究快照相同；`#2036` 为最新合并 PR）。`BASE_BRANCH = master`。
- 本任务 worktree / branch：
  - `C:\Users\wangj.KEVIN\projects\webgis-worktrees\goal-g20-raster-authoring-20261110T131721Z`
  - `zcode/goal-g20-raster-authoring-20261110T131721Z`
  - 创建自 `origin/master` 的上述 SHA；`git status --short` 为空。
- `BASE_SHA = 816705218a5aa6202bf15d745cb31cddb3437f44`。

## 已读治理文档

- 根 `CLAUDE.md`（skill routing + 「Never wait for CI results」）、根 `CONTEXT.md`
  （MapSpec / Session / ref / Tool / dispatch / Pi Native Surface / 三平面纪律）。
- `frontend/AGENTS.md`（Next.js 16 breaking-changes 指示 → 读 `node_modules/next/dist/docs/`）、
  `frontend/CLAUDE.md`（`@AGENTS.md`）。
- 已安装 Next.js 本地文档：主工作区 `frontend/node_modules/.pnpm/next@16.4.0_…/node_modules/next/dist/docs/`；
  `frontend/package.json` 声明 `next: 16.4.4`，pnpm store 实际安装 16.4.0。
  已读 `01-app/01-getting-started/05-server-and-client-components.md` 与
  `01-app/01-getting-started/11-css.md`（本次只新增 `'use client'` 局部组件与既有 Tailwind 类）。
- 已安装技能：`.agents/skills/` 下 gstack/planning-with-files/context-save 等（未额外安装，按原生 /goal 推进）。
- ADR 命名：本任务使用 **非数字编号** `ADR-G20-RASTER-EXPRESSION`，避免与 G12–G21 抢同一数字。

## PR / issue 首轮复核（2026-11-10）

- 开放 PR：`#2032`（UI 重设计，纯前端展示层，改 `context-panel.tsx`/`tailwind.config.ts` 等）、
  `#2022`（G10 数据刷新）、`#2021`（G11 地图简报）、dependabot 若干。
- 最近合并：`#2036` Pi-only、`#2035` Agent 提交撤销栈、`#2034` Map Studio 单源、
  `#2033` 命令清单、`#2011–#2017` 前批执行/运行时/资格/情境/组合器/生态/可靠性、
  `#1975` 出版制图、`#1973` Pi v1.5.0、`#1873` 要素编辑、`#1875` 联动视图/标注基础。
  **全部重新查询为 MERGED**；`#2021/#2022/#2032` 为 OPEN。
- Review 复核：`gh api --paginate .../pulls/<n>/reviews`、`.../comments`、`.../issues/<n>/comments`
  对 `#2032/#2034/#2035/#2036/#2021/#2022` 均无正式 review / inline 评论；只有 `#2021`
  的会话评论含作者自述独立 review。按任务书：「没有正式 review 不代表已通过评审」。
- 开放 issues：`#2037–#2072` 为 R1 审计批次（数据/平台/前端库），无栅格表达工作台相关项；
  `#1999–#2007` 为旧出版/导出审计（其中 `#2003` 指北针、`#1999` A4 求解），与本任务不同域。
  `issue open` 不等于未修复，本任务不接管。
- 远端分支：`origin/zcode/*` 无 G12–G21 新分支（G17/G18 仅本机 worktree）。
  历史 `goal-ogc-cog-data-services-v1` 等已合并的旧分支不代表在途实现。
- G09 参数化批量分析不纳入本任务；本任务不做计算编排。

## Pi 边界

- `.gitmodules` Pi 上游 `https://github.com/earendil-works/pi.git`；基线 pin v1.5.0 /
  `f9a26365568127461c47a07a37dcb42c140a3ac5`（`#1973` 已合并）。
- 任务书快照称最新稳定 v1.5.2 / `b4afdddf44e059c67ccf5097e5317d913521b470`；
  **本任务不修改 pin、构建配置、RPC/bridge 或 extension**（G12 独占）。
  本任务只通过既有 `ToolRegistry` / `@tool` 与 `ToolDispatchService` 入口暴露领域工具。
- `#2036` 后对话为 Pi-only；不恢复 ChatEngine 回退。

## 生产路径证据（首轮追踪，行号以 BASE_SHA 为准）

两条互不相通的栅格路径：

1. **数值栅格 ref → XYZ PNG 瓦片**：`app/api/routes/layer.py:964-1017 get_raster_tile`
   （`session_id`/`cmap`/`bands` query，`require_owned_session`，`_layer_data_budget`，
   缓存键 `(session,ref,z,x,y,cmap,bands)`，`single_flight`，`tile_lru_cache.get_epoch`/
   `put_if_current`，`_png_tile_response` ETag/304，`_resolve_raster_tile_path` owner-scoped
   路径解析 + `register_raster_ref` 失效登记）；`app/services/raster_tile_service.py:309-544
   render_raster_tile`（窗口读、overview 降采样、全局 `_get_band_stats`、固定 `Resampling.bilinear`、
   `apply_colormap_u8`、2048 条/256MB LRU）。
   **缺口**：无 stretch / resampling / opacity 参数；样式只有 `cmap`+`bands` 两个 query；
   没有 metadata/histogram/pixel probe 路由；`RasterStyleSpec` 生产无构造点。
2. **阵列 → 烘焙 PNG → `ref:raster/<id>` → MapSpec `type:"raster"` image 源**：
   `app/services/raster_cartography_converter.py:411-550`；
   `app/lib/cartography/mapspec_schema.py:225-232 RasterMapSpecSource`；
   `frontend/lib/mapspec-compiler/compiler.ts:513-524,881-886`；
   `frontend/lib/mapspec-runtime/runtime.ts:1069-1076`。
   **缺口**：MapSpec 无数值栅格 ref + 样式字段；换色=重跑转换=新 PNG。

其他已核实：

- `app/schemas/raster_spec.py:239-279 RasterStyleSpec` 仅被 `tests/unit/test_raster_spec.py`
  构造；`normalized_bands` 会静默剔除越界波段、`min(3,n)` 对 2 波段产生二元 RGB（渲染器拒绝）。
- `app/services/raster_stretch.py:46-96` 量化 payload（clip 在量化前）与
  `frontend/lib/map-kit/raster-stretch.ts:62-103` 仅测试/golden 消费，无生产调用。
- `frontend/components/hud/layer-style-panel.tsx:379-402` specBacked 栅格亮度/对比/饱和禁用；
  挂载点 `frontend/components/layout/context-panel.tsx:532`。
- 导出：`frontend/lib/mapspec-compiler/mapspec-to-svg.ts:806-831` 与
  `app/services/mapspec_to_svg.py:1362-1404` 只读 `tiles/url`，不读 `imageRef`；
  `frontend/lib/map-kit/exporter.ts:2384-2428` 有 `vector_svg_fallback_raster` 披露；
  PNG 单幅后端口 `app/api/routes/map.py:230-264 POST /export`（接收前端 Canvas 合成结果）。
- 权限：`app/services/auth_history_bridge.py:57 require_owned_session`；
  `frontend/lib/map-kit/tile-auth.ts:36-62 buildTileTransformRequest` 给 MapLibre 原生瓦片请求
  注入 `Authorization: Bearer` / `X-Session-Token`（仅第一方 URL）—— 新 tile 路由可复用，
  **不需要把 owner token 拼进 URL 或写进 MapSpec**。

## 首个可重复失败旅程（WP01）

在 BASE_SHA 上：注册一份四波段 GeoTIFF ref → 用现有
`GET /api/v1/layers/data/{ref}/raster-tiles/{z}/{x}/{y}.png` 分别请求
`bands=3,2,1` 与 `bands=4,3,2`：像素确有差异（bands 生效），但
1. `stretch` 无法表达（只能依赖内部 `_get_band_stats` 的近似全局 min/max）；
2. `resampling` 恒为 bilinear（分类数据缩放会造出中间类别）；
3. `cmap` 未知名静默回退灰度，`opacity` 完全不进数据面；
4. 没有任何权威 MapSpec 字段记录这次选择 → 刷新后回到默认，图例与导出无法引用同一表达；
5. 点击地图没有任何源像元值读回通道。

本任务以「请求参数 → 返回 PNG 像素 → 恢复后的 spec → 图例/导出字节」为证据闭环，
不以控件文案或 mock 调用次数判定。
