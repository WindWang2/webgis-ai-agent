# INTERFACES.md · G13 接口所有权与共享接线清单

## 本线主写区（新增/修改）

| 文件 | 变更性质 |
|---|---|
| `app/lib/gis/algorithms/capability_ecosystem.py` | G06 域包：`ALGORITHMS` 空→2 条真实描述符（G13 契约核心） |
| `app/lib/gis/capability_availability_cache.py` | **新模块**：同代快照缓存（本任务命名空间） |
| `app/lib/gis/availability_rerank.py` | 增量：共享分类器 `classify_candidates_availability`；`rerank_candidates` 改为消费同一判定（行为对拍由 G06 语料保障） |
| `app/services/chat/pi_availability_adapter.py` | 增量：`summarize_tool_qualification` / `preflight_dispatch_availability` / `DispatchAvailabilityDenial` |
| `app/tools/capability_ecosystem_tools.py` | 封套 v2（诚实 stored/ref）、cutoff 必填、MAX_SCHEMES、不可达披露、N-07 清理 |
| `tests/unit/gis/test_g13_capability_execution.py` | 新测试面 |
| `docs/science/ALGORITHM_CATALOG.md`、`docs/catalog/execution-catalog/*` | 既有生成器再生成（未手改） |
| `docs/dev/GIS_CAPABILITY_ECOSYSTEM.md` | 两行产物语义更新 |

## 最小 additive 接线（共享文件，非本线所有）

| 文件 | 接线点 | 说明 |
|---|---|---|
| `app/services/chat/tool_surface_v3.py` | `SurfaceSelection.availability_suppressed` 字段 + `select()` 步骤 6.8 + `availability_rerank_enabled()` | kill switch `GIS_AVAILABILITY_RERANK`（默认 on，关闭=逐位回退 V7） |
| `app/services/chat/pi_native_surface.py` | `compute_turn_active_tools`：`avail_suppressed` skip 集（两处 re-pin）+ disclosure + receipt `SurfaceDrop('availability')` | 前门/分布工具语义不变（仅 skip 不可用者） |
| `app/services/tool_dispatch_service.py` | 1.42b 复核块（bind 之后、Action IR 之前）+ analysis envelope seam（profile_ref 之后）+ H-1 sentinel + 注册循环 4 号位 | kill switch `GIS_AVAILABILITY_DISPATCH_RECHECK`；seam 只认 v2 契约封套 |
| `app/services/llm_result_formatter.py` | `_PRESERVED_META_KEYS` + `data_ref`/`artifact` | summary 分支保真（不改其它行为） |
| `app/tools/meta_tools.py` | `list_available_tools` 输出 + availability/unavailable_reasons/unavailable_count | 投影失败=整段缺席（发现语义不变） |
| `app/lib/gis/capabilities/network.py` | service_area `output_artifact_types` + stats_table | 能力层承认统计表产物（算法层校验仍生效） |
| `tests/unit/gis/test_g02_science_oracles.py` | 计数 253→255（注释 G13 +2） | 任务书允许的精确计数更新 |
| `app/lib/data/capability_lexicon_golden.json` | expect_status 随绑定更新为 native_verified | F-14 对拍真值同步 |

## 与兄弟任务的合同

- **G12**（Pi vendor/bridge/宿主）：本线零修改其所有权文件；消费点仅
  `get_tool_registry()`、`registered_surface_names`、`DynamicToolSurface`。
  G12 若升级 Pi pin，本线断言（marker/名单/seam）不受 schema 变化影响；
  合并后建议跑 `tests/unit/gis/test_g13_capability_execution.py` 作为契约回归。
- **G14–G16**：不改 MapSpec/编辑/画幅/标注；封套 `data_ref` 是普通 session
  ref，可被既有 ref 消费链使用。
- **开放 PR**：#2021/#2022/#2032 无文件交集（UI 重设计不动主题/布局）。

## 环境变量（新增）

| 变量 | 默认 | 语义 |
|---|---|---|
| `GIS_CAPABILITY_AVAILABILITY_CACHE` | 1 | 同代快照缓存（0=每次直编） |
| `GIS_AVAILABILITY_RERANK` | 1 | selector 可用性投影（0=逐位回退 V7） |
| `GIS_AVAILABILITY_DISPATCH_RECHECK` | 1 | dispatch 执行前复核（0=回退既有闸） |
