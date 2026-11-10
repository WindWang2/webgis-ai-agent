## 问题

基线有两条互不相通的栅格路径：① 会话 ref 指向 GeoTIFF → XYZ PNG 瓦片（只有 `cmap`/`bands` 两个 query 参数，stretch 由内部近似统计决定、重投影固定 bilinear、opacity 完全不进数据面）；② 数值阵列 → 烘焙 PNG → MapSpec `type:"raster"` 的 `image` 源（换色 = 重跑转换 = 新 PNG）。`RasterStyleSpec` 只有测试构造点，没有生产消费链；没有 metadata/直方图/像元检视路由；刷新后无法恢复用户表达；导出不携带同一套样式与图例。

典型请求「把这幅四波段影像按第 4、3、2 波段显示，用明确的拉伸范围，告诉我点击位置各波段的原始值和单位；再把当前单幅地图连同表达说明导出」在当前 master 上无法完成。

## 用户获得的行为

- 在「图层」页（无图层时也可用新增的栅格工作台入口）选择一份**已登记**的数值栅格 ref，看到真实波段清单、dtype、可证明的单位/scale/offset、有界直方图与近似统计口径；波段角色无可靠元数据时只称「第 N 波段」。
- 选择单波段着色或 RGB 三波段组合（4/3/2 等），设置全局或逐通道拉伸、色带、重采样、透明度、连续/分类语义；拖动预览只是临时草稿，提交才写权威 MapSpec。
- 提交后真实 XYZ 瓦片像素随之改变；刷新、重开会话、撤销后恢复同一表达；同源两个图层可用不同组合而互不串色。
- 点击地图（或输入坐标）读取**源像元**原始值、行列、CRS 转换结果、mask/nodata 状态与单位；快速点击 A/B 只有最后一次结果会显示。
- 图例与表达说明和图面像素同源；导出一个**冻结修订**的真实 PNG 或离线可读 SVG（base64 位图嵌入，不依赖 session URL），含同套图例与表达说明。
- Pi 可用 4 个真实注册的 `@tool`（describe / apply / probe / export）完成同一旅程；UI 与工具共用同一领域服务、同一数据引用、同一提交语义和同一错误码表。

## 实现边界（只做栅格表达/像元域）

- MapSpec 仍是唯一权威：新增 `NumericRasterMapSpecSource` 与 `MapSpecLayer.raster_style`（`raster_expression.v1` 子契约），文档版本保持 1.9（纯 additive optional，旧文档 `parse_mapspec` 逐字不变；避免与 G15/G16/G21 抢 1.10）。TS 类型由 `ts_projection.py` 生成。
- 提交恒为**单笔** `UpsertLayerIntent`（`mapspec_source.store_data` 的 `numeric_raster` typed passthrough），沿用既有 session 锁 / CAS `expected_revision` / checkpoint / undo / provenance；不新增 intent、Body codec、第二 store / ID 系统 / 撤销栈 / evidence store。
- 新增 `app/services/raster_authoring/` 领域包、`app/api/routes/raster_authoring.py`（7 个端点）、`app/tools/raster_expression_tools.py`；`raster_tile_service.render_raster_tile` 增加可选 `style`（`style=None` 时旧路径逐字兼容）。
- 前端新增 `frontend/lib/raster-authoring/*`、局部 `RasterAuthoringPanel/Launcher`，在 compiler/runtime 增加 `numeric_raster` 派生源分支；`mapspec-to-svg.ts` / `mapspec_to_svg.py` 对会话绑定瓦片 URL 明确披露而不是输出坏 `<image>`。
- **不改** G12 的 Pi pin/RPC/extension/dispatcher、G13 资格政策、G14 要素编辑、G15 画幅、G16 标注、G17 上传、G18 数据成果交付、G19 字段计算、G21 OD 流图；不改全局主题/布局骨架；不重建遥感/地形/分类算法，不做 #2022 自动刷新或 G09 批分析。

## 本地验证（真实执行，无需等待线上 CI）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/raster_authoring -q --no-cov -o timeout=900` | **44 passed** |
| `python -m pytest <18 个基线 + 新增文件> -q --no-cov -o timeout=900` | **352 passed, 1 skipped**（不含下附基线 ADR 撞号闸） |
| `python -m pytest tests/test_adr_numbering_contract.py -q` | **3 passed / 1 failed**：仅 `test_adr_numbers_are_unique` 为基线撞号（见下） |
| `python -m pytest tests/unit/test_mapspec_source.py -q` | **24 passed** |
| `python -m ruff check <本任务 Python 改动>` / `git diff --check` | **All checks passed** / 干净 |
| `pnpm exec tsc --noEmit` + `tsc -p tsconfig.test.json --noEmit` | **EXIT=0 / EXIT=0** |
| `pnpm exec eslint <本任务前端改动> --max-warnings 0` | **EXIT=0** |
| `vitest run <9 files> --maxWorkers=1` | **111 passed** |
| OpenAPI / 字段契约快照（按仓库既定 `*_UPDATE=1` 流程刷新后复跑） | **2 passed** |

旅程证据：`tests/unit/raster_authoring/test_raster_authoring_routes.py` 用 `httpx ASGITransport` 打真实 FastAPI 路由，断言「请求参数 → 返回 PNG 字节 → 恢复后的 spec → 图例 → 导出文件」；测试夹具现场生成 1/2/4/20 波段、nodata=0、零值有效、常值、全 nodata、EPSG:3857 与非 WGS84/全球网格 GeoTIFF（不签入二进制）。数值库线程 `OMP/OPENBLAS/MKL_NUM_THREADS=1`，vitest 单 worker。

## 独立评审 / 修复 / 复审

- 第一轮独立 reviewer 冷读：**不建议交付**（6×P1 + 8×P2 + 5×P3）。6 个 P1 全部修复并补回归：默认草稿把近似统计伪装成精确 manual、RGB 逐通道拉伸无法提交、一次提交拆成 2–3 笔 mutation、登录用户导出下载 401、每瓦片重跑全波段全幅统计读、UI probe 只查显示波段。详见 `planning/goal-g20-raster-authoring/REVIEW.md`。
- 第二轮**独立** reviewer 复审：**可交付**；六项 P1 逐条给出 `file:line` 闭环证据，未发现新的 P0/P1。复审残留 P2（>16 波段 probe 截断、launcher 恢复接线、ref 失效清缓存、宽波段指纹 OOM）与 P3（PDF 提前拒绝、错误文案）也全部补修；仅「descriptor/probe/render 的裸 `rasterio.open`」登记为后续收敛项（这些窗口读已限定 ≤16 波段 / ≤2048 边且路径经 owner-scope 校验）。

## 与其它 PR / 分支的关系及冲突段

- 基线 `master = 816705218a5aa6202bf15d745cb31cddb3437f44`（`#2036` 为最新合并）。开放 `#2032`（UI 重设计）、`#2022`（数据刷新）、`#2021`（简报）均已复核；本分支不重复其工作。
- **共享文件冲突段**（合并时按最新主干校正，均只做 additive 追加）：
  - `app/main.py`（+1 import / +1 `include_router`）、`app/tools/__init__.py`（+1 行 `_TOOL_MODULES`）——与 `#2022` 同区追加。
  - `frontend/components/layout/context-panel.tsx`（+1 import / +1 行条件挂载 `<RasterAuthoringLauncher />`）——与 `#2032` 同文件；不改主题/布局骨架/面板宽度。
  - `app/lib/cartography/mapspec_schema.py` 只新增 raster 子模型、`MapSpecLayer.raster_style` 与 `SCHEMA_EXPORT_MODELS` 三项，不动 `MapSpecFrame` / `MapSpecLayerLabel` / flow 子模型与 `KNOWN_VERSIONS`；ADR 使用无数字编号 `docs/adr/G20-RASTER-EXPRESSION.md`，不抢号。
  - `tests/quality/snapshots/openapi.json` / `api_contract_fields.json` 按仓库流程刷新；**同时吸收 master 上 `#2035` 遗留的 `UndoAgentCommitBody` 漂移**（开放 issue `#2007` 已记录该基线漂移），属 additive，不改变既有端点。

## 基线既有失败（如实隔离，非本任务引入）

1. `tests/test_adr_numbering_contract.py::test_adr_numbers_are_unique`：`{'0286': 2, '0287': 2}`。主工作区未改动的 `docs/adr/` 同样各有两个 0286/0287 文件（前批并行 PR 占号）。本任务 ADR 无数字编号，不新增撞号。
2. `tests/unit/gis_tool_conformance/test_live_registry_audit.py::test_live_registry_completeness_ratchet`：`binding_unbacked 66 > ceiling 64`。主工作区基线 `collect_live_facts(bootstrap=True)` 实测 `400 tools / 66`，本分支 `404 / 66`（四个新工具均为 `no_capability_binding`，不增加债务）；其余 5 项审计（含 `STATUS_FAIL == 0`、ref_offload 集合）全绿。

## 迁移 / 回退

- 纯 additive：`RasterExpressionSpec` / `NumericRasterMapSpecSource` 为新类型，旧 `RasterStyleSpec`、`type:"raster"` imageRef、旧 XYZ 瓦片路由、外部底图与 `raster-opacity` 行为均不变；无数据迁移、无 DB migration、无配置开关。
- 回退方式：revert 本 PR 即可；运行时只是不再产生 `type:"numeric_raster"` 源与 `layer.raster_style`。已写入的 MapSpec 因 `extra="allow"` + canonical 深拷贝仍可读取（字段作为 unknown 披露，不会破坏旧文档解析）。
- 不需要 feature flag；Pi 工具通过既有 `ToolRegistry` 注册，不修改 native surface / vendor pin。

## 未测限制 / 已知边界（不伪装成通过）

- **没有等待线上 CI**（按任务书 A 要求）；本地验证全部真实执行。
- 未起完整前端浏览器服务跑 Playwright 点击旅程；地图点击链以 `publishMapClick`/`subscribeMapClick` 纯函数 + state reducer 单测覆盖，HTTP/瓦片/导出经真实 FastAPI 路由验证。
- Pi 旅程使用 `ToolRegistry.dispatch` 的真实注册工具入口（含 stub 式参数与有界回执断言），**未**运行仓库 `PI_JOURNEY_E2E=1` 的 uvicorn + node stub 进程；未调用任何付费 LLM。
- 导出成果只渲染数值栅格层 + 图例/表达说明；其它矢量/底图层在 sidecar `omitted_layers` 与 `limits` 明确披露，不伪称整图。PDF 明确拒绝（位图嵌入能力未接入），不静默丢层；位图 ≤2048 边，明确「提高 DPI 不增加真实遥感细节」。
- >16 波段的 descriptor 统计与 probe 按领域预算截断并披露；`raster_content_fingerprint` 的冻结格式未改，极宽波段源改用 `bnd:` 有界文件摘要。
- Pi 提交回执保持有界（不含完整 MapSpec），live reconcile 由既有 `apply_gis_mutation` 的 MapSpec 事件/SSE 通道承担；工具回执以 `reconcile_via:"mapspec_event"` + `mapspec_revision` 明确披露。
- 完整 publication PDF 渲染链、3D/terrain 路径、COG 远程源的浏览器端完整链路不在本任务范围。

## 交付文件

`planning/goal-g20-raster-authoring/` 下 `BASELINE.md`、`DEDUP_MATRIX.md`、
`PLAN.md`、`INTERFACES.md`、`LOOP_LEDGER.md`、`TEST_MATRIX.md`、
`REVIEW.md`、`PR_HANDOFF.md` 记录基线、排重、接口、迭代、测试、评审与交付信息。
