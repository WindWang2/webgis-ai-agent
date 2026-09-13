# ads-v1 · DS3 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS3 · 取数计划与代价估算 · ADR-0173 · 里程碑 M2（与 DS2 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| D2 计划编译器 | `app/services/data_fabric/planning/compiler.py`（`PlanRequest`/`PlanCompiler`/`_SourceFacts`） | `tests/unit/test_data_fabric_planning.py`（19 测） | 7 类源计划生成测试（≥5 达标）；步骤执行序契约；plan_id 结构化确定 |
| 下推最大化（诚实） | 同上（pushed_down 由声明能力决定） | `test_plan_generation_across_source_types[...]` 7 组 | 不可下推步骤保留并标"本地"；explain 显式呈现 |
| 代价模型（复用 federated 组件） | `planning/cost_model.py`（selectivity 面积比兜底 + 配额惩罚；常数单点 provisional） | `test_bbox_selectivity_grid_math` / `test_cost_estimate_p50_deviation_within_30pct` | **P50 偏差实测 ≤30% 达标**（行数偏差 0%、字节偏差 ≈18.5%，fixture 网格源） |
| 预算约束选优 | `compiler.choose_plan`（超预算生成聚合/抽样/缩范围变体 + 建议） | `test_over_budget_returns_suggestions_not_failure` / `test_budget_fitting_variant_is_chosen` / `test_no_budget_returns_raw_plan_no_suggestions` | 超预算给建议不报错；计划器不谎报符合预算 |
| plan explain | `planning/explain.py`（确定性逐行说明） | `test_explain_is_deterministic_snapshot` / `test_unpushed_steps_declared_as_local_in_explain` | 快照确定性；"下推/本地"可见 |
| 重放一致性 | `planning/replay.py`（canonical-JSON sha256(dataset_key, version, features)） | `test_replay_same_plan_same_hash` | 同 plan + 同 pin → 同哈希；版本参与哈希；同请求 → 同 plan_id |
| fixture 保真升级 | `tests/data/fabric_fixtures.py`（OGC fake 按 bbox 参数服务端过滤 + `grid_features` 网格助手） | 既有 fixture round-trip 测试继续全绿 | fake 行为对齐真源语义（deviation 测试的前提） |

## 波次验收对照（§6 DS3 行）

- [x] ≥5 类源 plan 测试（7 类：ogc_api/postgis/arcgis/stac/geopackage/stats_api/local_file）
- [x] 代价估算 P50 偏差 ≤ 30%（行 0% / 字节 18.5%，provisional）
- [x] 超预算时给出降级建议而非报错（3 组闸测试）
- [x] explain 输出有快照测试
- [x] 重放一致性测试通过（同 plan + 同 pin → 同哈希）

## 备注

- 计划器零 I/O（编译纯函数）；执行缝 `replay(adapter=)` 供测试注入 fixture adapter。
- DS8 校准点：`cost_model` 顶部常量 + `ranker.WEIGHTS`，均已单点化。
