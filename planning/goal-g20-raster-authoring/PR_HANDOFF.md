# PR_HANDOFF — G20-RASTER-AUTHORING（已交付）

| 项 | 值 |
|---|---|
| PR URL | https://github.com/WindWang2/webgis-ai-agent/pull/2078 |
| PR 号 / 状态 | `#2078` / OPEN / **非 draft** |
| title | `feat(raster): G20 持久化栅格表达工作台 · 真实像元检视 · 权威 MapSpec 单幅同语义输出` |
| branch | `zcode/goal-g20-raster-authoring-20261110T131721Z` |
| worktree | `C:\Users\wangj.KEVIN\projects\webgis-worktrees\goal-g20-raster-authoring-20261110T131721Z` |
| base | `master` @ `816705218a5aa6202bf15d745cb31cddb3437f44` |
| head | `49f117fd05ea96db91ae2b08501e0b53e376244e`（5 commits，正常 push，无强推） |
| base/head 关系 | ahead 5 / behind 0（PR 前 `git fetch origin --prune` 复核，master 未前进） |
| 线上 CI | 按任务书 A **未等待、未轮询**；PR 正文如实区分本地通过 / 基线失败 / 环境阻塞 / 未运行项目 |
| 是否 merge / 发布 | **未 merge、未发布、未删除分支或 worktree** |

## 主要功能

1. `raster_expression.v1` 严格表达契约（单波段/RGB、1-based、逐通道拉伸、
   色带/重采样/分类语义、具体错误码、`style_id` 内容身份 + 数据修订绑定）。
2. MapSpec additive `NumericRasterMapSpecSource` + `MapSpecLayer.raster_style`，
   生成 TS 类型同步；**单笔** `UpsertLayerIntent` 原子提交（CAS/checkpoint/undo）。
3. 真实瓦片消费 bands/stretch/resampling/colormap/opacity，缓存身份含
   `style_id + data_revision`；同源双层派生独立源；旧 `style=None` 路径零改动。
4. owner-scoped descriptor / 有界直方图 / 真实源像元检视（CRS、行列、原值、
   scale/offset/单位、mask、状态区分）；快速点击过期响应抑制。
5. 局部工作台 UI（预览/待提交/已提交/失败、错误字段聚焦、键盘可达、
   地图点选、首次挂载入口）、i18n 双语言。
6. 4 个真实注册 Pi `@tool`，与 UI 共用同一领域服务和错误码；回执有界。
7. 图例/表达说明与像素同源；冻结一个修订的真实 PNG 与离线 SVG（base64
   嵌入，恒签发能力 URL）；PDF 明确拒绝，其它省略层如实披露。

## 测试与评审结论

- Python：`tests/unit/raster_authoring` **44 passed**；18 个基线 + 新增文件
  **352 passed, 1 skipped**；`test_mapspec_source.py` **24 passed**；
  ruff / `git diff --check` 全绿。
- 前端：`tsc --noEmit` + `tsconfig.test.json` **0/0**；ESLint **0**；
  vitest 9 files **111 passed**。
- 快照：OpenAPI / 字段契约按仓库既定流程刷新，刷新后两闸 **2 passed**。
- 独立评审：第一轮「不建议交付」（6×P1 + 8×P2 + 5×P3）→ 全部 P1 修复 +
  多数 P2/P3 修复/披露；第二轮**独立** reviewer 复审「**可交付**」，
  无未解决的本任务 P0/P1，残留 P2 已闭环或有明确边界披露。详见 `REVIEW.md`。
- 基线既有失败（主工作区实测隔离证据见 `REVIEW.md` / `TEST_MATRIX.md`）：
  ADR 0286/0287 撞号；registry `binding_unbacked 66 > 64` 棘轮；
  master OpenAPI 漂移（issue `#2007`）。本分支未新增同类债务。

## 与其它 PR 的关系 / 必要操作说明

- PR 前二次 fetch 发现新增开放 `#2073`(G13)、`#2074`(G19)、`#2075`(G14)、
  `#2076`(G15)、`#2077`(G16)；逐一核对 changed files 并把冲突矩阵写入
  `DEDUP_MATRIX.md` 与 `PR_BODY.md`。
- **合并者校正清单**（G15 推 2.0 / G16 推 2.1，均改 `mapspec_schema.py`
  与 `types.generated.ts`；G19 改 `main.py`/`tools/__init__.py`/openapi 快照）：
  ① 以最新主干 `KNOWN_VERSIONS` / `LATEST_VERSION` /
  `SUPPORTED_MAPSPEC_VERSIONS` 为准；② 合并后重跑
  `python -m app.lib.cartography.ts_projection` 并跑
  `test_ts_projection_contract.py`；③ 重跑 OpenAPI/字段快照流程；
  ④ 若 `#2077` 先合入，复核 `mapspec-to-svg.ts` / `mapspec_to_svg.py`
  两个 additive 分支（raster / label）并存；⑤ 与 `#2032` 的
  `context-panel.tsx` 保留双方两行局部接线。
- 未 cherry-pick 任何他人未合并提交；未改 G12–G19、G21 的所有权。

## 迁移 / 回退

纯 additive，无 DB migration / 配置开关 / feature flag。回退 = revert 本 PR；
旧 `RasterStyleSpec`、`type:"raster"` imageRef、旧 XYZ tile 路由、外部底图、
`raster-opacity` 行为均不变；`extra="allow"` + canonical 深拷贝保证旧文档可读。

## 剩余外部阻塞 / 未测限制

- **无实际外部权限/额度/服务阻塞**；push 与 PR 创建均成功。
- 未等待线上 CI；未跑完整 Playwright 浏览器点击旅程（以纯函数 + reducer
  单测 + 真实 FastAPI 路由覆盖）；未运行 `PI_JOURNEY_E2E=1` 的
  uvicorn+node stub 进程（以真实 `ToolRegistry.dispatch` 断言替代，已在
  PR 正文标注）；未调用付费 LLM。
- 导出成果只含数值栅格层 + 图例/说明（其它层在 sidecar 披露）；PDF 明确
  拒绝；位图 ≤2048 边；>16 波段统计/probe 按预算截断并披露。
- Pi 回执保持有界（不含完整 MapSpec），live reconcile 走既有 MapSpec
  事件/SSE 通道，回执以 `reconcile_via:"mapspec_event"` 明确披露。
- `descriptor.py` / `probe.py` / `render.py` 的裸 `rasterio.open`
  登记为后续 GDAL env 收敛项（窗口读已限定波段与 ≤2048 边，路径经
  owner-scope 校验）。

## 关键文件

- 领域：`app/services/raster_authoring/`、`app/schemas/raster_spec.py`、
  `app/api/routes/raster_authoring.py`、`app/tools/raster_expression_tools.py`、
  `app/services/raster_tile_service.py`、`app/services/mapspec_source.py`。
- 契约：`app/lib/cartography/mapspec_schema.py`、
  `frontend/lib/mapspec-compiler/types.generated.ts`、
  `docs/adr/G20-RASTER-EXPRESSION.md`（无数字编号，避免抢号）。
- 前端：`frontend/lib/raster-authoring/`、`frontend/components/hud/raster-authoring-panel.tsx`、
  `compiler.ts` / `runtime.ts` / `mapspec-to-svg.ts` / `vector-svg-export.ts`。
- 测试：`tests/unit/raster_authoring/`、
  `frontend/lib/raster-authoring/*.test.ts`、
  `frontend/lib/mapspec-compiler/compiler.numeric-raster.test.ts`。
- 账本：本目录 8 份规划文档 + `PR_BODY.md`（PR 正文源文件，真实换行）。
