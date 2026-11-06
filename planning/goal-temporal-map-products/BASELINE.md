# BASELINE — GOAL 03 时空 GIS Harness + 动态专题图生产能力

基线：origin/master `6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（2026-10-31 18:54 +0800，merge PR #1875）。
worktree：`/home/kevin/projects/webgis/webgis-wt-goal-temporal-map-products`，分支 `zcode/goal-temporal-map-products-v1`。
审计方式：2 个只读 subagent 全量扫描（后端 106 tool uses / 前端 112 tool uses）+ 主 Agent 交叉验证关键集成点（intents/registry/fingerprint/mutation batch/aggregate 端点/catalogue）。

## 1. temporal tools（矢量/统计时序分析）——成熟，无地图绑定

- 已有实现：`app/tools/temporal_tools.py`（`:194 register_temporal_tools`）注册 7+3 工具：`temporal_profile/temporal_filter/temporal_aggregate/temporal_change/temporal_trend/spatiotemporal_hotspot/temporal_raster` + 科学面 `temporal_changepoint/temporal_seasonal_decompose/emerging_hotspot_analysis`。
- 入口与调用链：`app/tools/__init__.py:69` → `@tool`（`app/tools/registry.py:2092`，ToolRegistry `:475`）→ `TemporalEngine`（`app/services/temporal/engine.py:34`）→ `filter.py:92 / aggregation.py:79 / trend.py:59`。调度统一 `ToolDispatchService.dispatch`（`app/services/tool_dispatch_service.py:266`）。
- 时间窗口语义：`TimeInterval` 双闭区间（`app/services/temporal/models.py:103`）；`parse_value_to_instant`（`app/services/temporal/profiler.py:38`）接受 ISO-8601/epoch 秒·毫秒/纯年份；naive→UTC；relative_window 近似（月=30天）。
- 现有测试：`tests/unit/test_temporal_gis_runtime.py`、`test_temporal_science_vnext.py`、`test_temporal_relative_window_507.py`、`tests/test_temporal_profiler.py`。
- **缺口**：结果只是一次性 tool_result，无任何通往 MapSpec 图层/tile/聚合端点的绑定；跨工具无时间轴概念。

## 2. science_temporal_tools（栅格立方体分析）——成熟，输入须内联

- `app/tools/science_temporal_tools.py:48`：`temporal_cube_stats/phenology_features/temporal_anomaly/ts_smooth_gapfill` → `app/lib/geo_analysis/temporal_cube.py`（守卫 T≤512、T·H·W≤8M）。
- **缺口**：与 lakehouse 持久 cube 无 ref 通道；docstring 自认应走 artifact/ref（未实现）。

## 3. 算法描述符 `app/lib/gis/algorithms/temporal.py`

- 13 个 AlgorithmDescriptor + 5 个 PARAMETER_CONTRACTS，纯声明层（单一事实源），实现分居 `app/services/temporal/trend.py` 与 `app/lib/geo_analysis/spatiotemporal_eha.py`。**禁止平行重写**；新增算法禁止回填中央 registry。

## 4. recipe_packs/temporal.py —— 静态时序制图 recipes

- `app/services/gis_harness/recipe_packs/temporal.py:24`：6 个 recipe（站点画像/趋势/变点/季节性/聚合统计/年际对比），消费链 `recipes.py:1529` → `chat/plan_orchestrator.py:581` → `map_plan_compiler/projector.py:108`。golden corpus 已有 `base__temporal_comparison_map__*` 等静态渲染 golden。
- **缺口**：全部输出静态图；无动态多帧/时间轴产品 recipe。

## 5. lakehouse cube 存储 + issue #1837

- `app/services/lakehouse/cube_schema.py`（V7/V8 labeled cube）、`cube_store.py`（`read_cube_window :239`）、`cube_service.py`（`read_session_cube_window :250`，8M cells 上限）。查询端点 `POST /lakehouse/cubes/window`（`app/api/routes/lakehouse.py:177`，整数下标 slice）。
- **#1837（OPEN，audit/bug，未修复，已定位）**：`cube_store.read_cube_window` 返回的 `transform` 是整 cube affine 原点，不随 y/x 切片起点平移；前端 `timeline-player.tsx:161-183`、`query-results.tsx:188-207` 盲信 → x0/y0>0 的窗口帧地理位置全错。本任务不认领该修复（audit 专人），**规避**：本任务 raster 路径只做 x0=y0=0 全范围窗口读（transform 正确），并在新代码中禁止复刻同类缺陷；登记冲突矩阵。

## 6. MapSpec schema + time 幽灵分支 + CAS/user-wins

- 权威 schema：`app/lib/cartography/mapspec_schema.py`，`KNOWN_VERSIONS("1.0".."1.7")`、`LATEST_VERSION="1.7"`；`MapSpecDocument`（`:843`）`extra="allow"`，**无任何 time/temporal 声明字段**；`MapSpecFrame`（`:766`）无时间语义。
- **time 幽灵分支**：`SetTimeIntent`（`app/services/mapspec/intents.py:334`）→ `_handle_settime`（`app/services/mapspec/mutation_handlers.py:1071`）写 `mapspec["time"]`；REST `SetTimeBody`（`app/schemas/mapspec_mutation_schema.py:145`）经 `POST /chat/sessions/{sid}/mapspec/mutations`；registry codec `mutation_registry.py:591`。**无 agent 工具调用、无前端消费——全链路断头路**；字段松散（type: 自由串、current: Any、无时区/语义/缺失策略）。
- 指纹：`app/lib/cartography/quality_loop.py:263 cartographic_fingerprint` → `cartographic_projection` 已显式投影 `"time"` 分支（temporal 需同样加一行）。
- CAS/user-wins：`MapSpecLifecycleEngine.apply_mutation`（`app/services/mapspec/lifecycle_engine.py:266`）：分布式锁→mutation_id 幂等→origin=user 必带 expected_revision（ADR-0058）→反幻觉网关→COW→校验/checkpoint/revision 单调/回滚。用户面锁内复检 `:461`。
- `apply_gis_mutation_batch`（`app/services/gis_world_state/mutation.py:551`）**只接受 PatchLayerPresentationIntent**——时序写入走单 intent `apply_gis_mutation` / lifecycle engine 通道。

## 7. MVT / aggregate —— 无时间过滤

- MVT：`GET /layers/data/{ref}/tiles/{z}/{x}/{y}.mvt`（`app/api/routes/layer.py:700`）零时间参数；同一 ref 所有时间切片要素全部渲染。
- aggregate（#1875）：`GET /layers/data/{ref}/aggregate`（`layer.py:286`）：`group_by/agg/agg_field/bbox/where(typed AST ≤2048B)/max_groups/v(revision guard→409)`；实现 `app/services/feature_aggregate.py:76`（500k 扫描上限、诚实披露）。where AST 算子（`app/services/feature_pages.py:291`）：`eq/ne/in/between/contains/startswith/gte/gt/lte/lt` + and/or/not——**字面值比较**，字符串日期走字典序。
- **机会（本任务采用）**：时间窗口 → 编译为 where AST（值按数据实际表示归一：iso_string 字典序对同格式恒正确 / epoch 数值比较），复用既有端点，map/chart/table 共享同一 where AST 即共享同一 time filter fingerprint。

## 8. Requirement IR / map_operation.v1 / 工具 schema

- IR：`app/services/gis_harness/requirement_ir/contracts.py`；`TimeSpec`（`:231`）仅 range_start/end(≤16 裸串)/granularity/series；`MeasureSpec.temporal_required`。扩展样板 = SceneSpec（`:304-338`：additive 字段 + Literal 词表 + is_default() + degrade_reason + GISIntentSpec 一行 default_factory `:358`）。
- map_operation.v1（#1874）：`webgis_map_operation`（`app/tools/map_operations_tools.py:57`）→ `EFFECT_KINDS` 8 种（`app/services/gis_harness/map_operations/contracts.py:45`，**无时间效果**）→ `commit_map_operation`（`service.py:634`：幂等重放→编译→敏感效果 confirmation_required→apply）。**本任务不向 EFFECT_KINDS 加未审查效果**，走独立 temporal_view_* 薄工具。
- 工具 schema：`args_model.model_json_schema()`（registry.py:646）；capability manifest 有 CI 契约门（新工具须同步归类）。

## 9. 导出 / atlas jobs（#1871）

- PNG `POST /export`（前端 canvas）、矢量 PDF `POST /export/vector-pdf` → `app/services/publication_export.py:131 compile_publication_page` → `mapspec_to_svg.py`（支持 layout.frames 的 `_apply_frame_overrides :415`）。
- atlas：`POST /api/v1/atlas/jobs` → `app/lib/cartography/atlas_job_spec.py:44`（`AtlasPolicy.driver=frames|category|feature`，`classification_scope=shared|local`）→ planner（`app/services/atlas_batch/planner.py:230`，`AtlasPageBinding{filter_layer_id/filter_property/filter_value/...}`）→ manifest（`manifest.py:43`，`atlas_publication_manifest.v1`）。**无时间驱动分页**——time driver 为 additive 扩展点（enum + planner 分支 + binding/manifest additive 字段）。

## 10. 前端

- lakehouse timeline-player（`frontend/components/sidebar/lakehouse/timeline-player.tsx`）：模态播放器（rAF、倍速、键盘、LRU 帧缓存、reduced-motion），数据 `POST /lakehouse/cubes/window`；与地图仅单向单帧快照（`useHudStore.addLayer` heatmap）。**不作为地基，仅作交互/缓存/无障碍参考**。
- map-panel（`frontend/components/map/map-panel.tsx` 1908 行）：MapSpecRuntime 差分 reconcile（`:800-915` composeLiveMapSpec → reconcileAsync）；selectionFilters（`:297-330`）在 reconcile 后以 per-layer MapLibre filter 应用——**时间过滤走同一模式**；comparison 双视图（`components/map/comparison/comparison-view.tsx`）支持 side-by-side/swipe（空间对比）。**无时间控制插槽**。
- MapSpec TS 类型：`frontend/lib/mapspec-compiler/types.generated.ts`（由 `app/lib/cartography/ts_projection.py` 生成，权威在 Python schema）。
- #1875 联动：selection-store（transient，绝不进 MapSpec）+ `useViewportAggregate`（`frontend/lib/map-components/viewport-aggregate.ts` → `frontend/lib/data-plane/paged-features.ts:211 fetchLayerAggregate`）+ `usePagedLayerTable`（PAGE_LIMIT=200 游标、where 下推）。**仅空间/类别维度，无时间维度**。
- 命令：`frontend/lib/map-commands/catalogue.ts:24` 合并 12 域片——新增 `temporalCommands` 域片即可让 Agent SSE 命令与 UI 共用；SSE 词表无时间事件（命令走 step_result→dispatchAction 通道即可）。
- 状态：`HudState/workbenchSlice/session-cursor` 均无时间字段；会话恢复 `frontend/lib/session/map-state-restore.ts` 优先 `_cartographic_observation` 快照；workbench 水合走服务端 `mapspec.workbench` 分支（CAS `_rev`）。**时间游标进 MapSpec.temporal 本身（单一真相），恢复即读 committed spec**。
- user-wins 前端样板：`render-apply-ack.ts:8,214`（用户 pending 显隐弃权）、`user-mutation.ts`（用户相机变更 commitExplicitView + CAS）——时间游标用户操作走同款 origin=user 提交。
- i18n：`frontend/messages/{zh-CN,en-US}/*.json` 32 namespace，`test/i18n/key-completeness.test.ts` CI 门。
- 测试脚本：`pnpm test`（vitest run）/ `pnpm typecheck`；覆盖率闸 lines 75/functions 70/branches 60。

## 端到端场景追踪（3 例）

**S1「显示成都小学近 5 年变化并逐年播放」**：自然语言 → plan_orchestrator → Requirement IR（TimeSpec.series=true）→ recipes（temporal_aggregate_stats）→ 工具 `webgis_layer_*` 挂图层 + （本任务新增）`temporal_view_bind` 探测时间字段→生成 frames（数据实际年份，无虚构期）→ MapSpec.temporal（lifecycle CAS 落盘）→ 前端 map-panel reconcile +（本任务新增）TemporalTimeController 逐帧 where 过滤（MVT 客户端过滤 + aggregate 服务端 where AST）→ displayed_at 门控 → 导出单帧（vector-pdf）。
**S2「固定颜色分级对比 2023 与 2026 年各区数量」**：`temporal_view_compare`（本任务）→ contract.compare + shared classification breaks（两窗聚合求公共分级）→ 前端 twin 层 + comparison-view 或固定 breaks 的同层两帧 → 图例/统计随帧更新（aggregate 同 where fingerprint）。
**S3「单期数据拒绝假动态」**：`temporal_view_bind` 探测仅 1 期 → typed fallback（`single_period_unsupported` + 诚实说明），不写 temporal.enabled；前端无时间轴。

## 现有功能 × 缺失调用链矩阵（结论）

| 能力面 | 现状 | 本任务动作 |
|---|---|---|
| 时序分析工具/引擎 | 成熟 | 只复用（profiler 探测、change 差值、aggregate 统计），不重写 |
| MapSpec time 分支 | 幽灵（无 schema 无消费者） | 新建 v1.8 `temporal` 一等字段（严格契约），time 分支保留不动 |
| MVT 时间过滤 | 无 | 检测+降级：客户端 per-frame filter（tiles 已按预算流式），统计走服务端 where AST |
| aggregate 时间过滤 | where 字面比较 | 时间窗口→where AST 编译器（表示归一），零端点改动 |
| map_operation 效果 | 8 种无时间 | 不动 EFFECT_KINDS；新 temporal_view_* 薄工具走 lifecycle CAS |
| 前端时间轴 | 仅 lakehouse 模态 | 新 components/map/temporal-* + lib/temporal-map（复用 selectionFilters 模式/播放器交互范式） |
| 图表表格联动 | 空间/类别 | 时间维度投影：同 where fingerprint 进 aggregate/paged hooks |
| 导出/atlas | 静态帧+3 driver | 单帧复用 vector-pdf；atlas 增 time driver（additive） |
| 会话恢复 | 静态快照 | 时间游标在 MapSpec.temporal（committed spec 即恢复源） |
