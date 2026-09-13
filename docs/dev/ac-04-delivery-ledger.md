# ac-04 交付台账（任务 → 文件 → 测试 → 证据）

基线：origin/master `09d839d3`；分支 `adaptive-cartography/04-data-preprocess`；
worktree `webgis-wt-ac-04`。

| 任务（任务书 §2） | 交付文件 | 测试 | 证据/说明 |
|---|---|---|---|
| P0 勘察：14×7 映射矩阵 | `docs/dev/ac-04-code-op-matrix.csv`（25 码全表 + `in_taskbook_14` 标记 + 4 缺口格）、`docs/dev/ac-04-quality-recon.md` | — | S1（Explore）只读产出：别名 12/25、提案可达 12/25、pipeline 可执行 9/25、完全无映射 13/25；默认组合覆盖率 4/25 |
| P0 勘察：14 个最小复现集 | `tests/cartography/fixtures/quality_cases/<15 CODE>/input.geojson+expected.json`（生成器 `_generate.py`） | `tests/unit/test_quality_cases_matrix.py`（16 用例） | 每码断言：audit 触发 + 级别一致 + gate verdict + 计划 op + 修复后置条件 + 非破坏（输入 deepcopy 相等） |
| P0 勘察：5 类毁图现象 | `tests/unit/test_dirty_data_cartographic_effects.py` | 5 用例 | 自交→面积塌缩(0.0)→修复后 2.0；重复→2 符号→1；混合类型→mix_ratio=1/3；错标 CRS→block→重投影回 (126,45)；离群→断点被拉到 >20000→建议 clip 23.86 |
| P1 制图前置门禁 | `app/services/mapspec/lifecycle_engine.py::_run_quality_gate_hook`（+2 个调用点，diff 纯增量 178 行 0 删除）、`app/core/config.py`（`MAP_QUALITY_GATE_MODE`/`MAP_QUALITY_GATE_MAX_FEATURES`） | `tests/unit/test_quality_gate_lifecycle.py`（8 用例） | blocking 拒绝含 op 序列 hint 且 session 零残留；advisory/off/bypass 三态；bypass 必留审计事件（log+`mapspec_quality_gate_events_total` 计数） |
| P2 op 编排器 | `app/services/spatial_repair_pipeline.py::plan_repair_ops` + `RepairOpPlan` + `CANONICAL_OP_ORDER` | `tests/unit/test_repair_orchestrator.py`（14 用例） | 固定顺序 = CANONICAL_OP_ORDER 子序列；破坏性裁决 4 阈值（5%/20%/30%/1%）；低于阈值诚实 skipped_ops；确定性（同输入同计划）；RING_CHECK_FAILED 映射用合成 issue 钉死 |
| P3 CRS 自动识别 | `app/services/spatial_quality_gate.py::infer_crs`（4 类投影 + 歧义消解 + low_confidence） | `tests/unit/test_crs_inference.py`（15 用例） | pyproj round-trip 验证识别准确（非记忆常数）；4326/3857/CGCS2000(4490+4530)/UTM(32632)；无锚点 GK→low 不猜带号；人工声明优先测试 |
| P4 离群剖析 | `spatial_quality_gate.py::profile_outlier_policy/profile_numeric_fields` | `tests/unit/test_outlier_profiling.py`（13 用例） | 契约键封闭 + 词表封闭；clip_p99 建议值 = inlier p99（23.86 vs 被吞的 85003）；「只剖析不裁剪」字段级断言 |
| P5 修复血缘 | `spatial_repair_pipeline.py::repair_dataset_with_lineage`（既有 `repair_dataset`/`repair_dataset_detailed` 签名与返回元数不变） | `tests/unit/test_repair_lineage.py`（6 用例） | lineage 键封闭 {op,before,after,area_delta,evidence,ts}；evidence[] 复用 Wave-4 op 级键（与 build_repair_evidence 同形）；为何修解释链 plan.reasons×lineage |
| P6 三个新 op | `fix_topology_overlap` / `fix_gaps` / `drop_outliers_or_flag` / `attribute_drop_or_flag` + `remove_empty.drop_zero_coordinates` | `tests/unit/test_repair_ops_new.py`（15 用例） | 边界：空集/单要素/全重叠(预算内)/远距离 no-op；difference 后入让先入（面积账 4-2=2）；flag 默认可整体剥离（`ac04_quality_flags`）；非破坏组合断言 |
| P7 profile 契约 | `spatial_quality_gate.py::default_quality_profile/evaluate_quality_gate.profile_extension` + lifecycle 钩子写入 source.profile | `test_quality_gate_lifecycle.py::test_clean_data_passes_and_writes_profile_extension` | 6 契约键 {geometry_mix,n_valid,extent,crs_confidence,outlier_policy,quality_advisories}；默认值兜底函数供 02/03 先行消费 |
| P8 回归收口 | `docs/adr/0153-pre-cartography-quality-gate.md`、`docs/dev/ac-04-decisions.md`、`CHANGELOG.md`、`requirements-dev.txt`(+pytest-xdist) | 全量 `pytest tests/unit -q -n 2 -m "not heavy and not real_services and not perf"` | 10775 passed / 111 skipped / 46 failed（S2 甄别：map_product_lifecycle_v2×3、workflow_api×2、reproducible_gis_runtime×2 串行重跑全过=并行 flaky；其余族与干净基线同 fam，含 1 个 ERROR 为 sqlite 共享缓存并行时序）；blocking 类 14 码样本 100% 拦截或自动修复（矩阵回归钉死） |

## 复核纪要（§0.2，进 PR 描述）

- PR 检索（quality/repair/CRS/outlier，state=all ≤200）：历史线 #315/#322/#358/#1139/#1160/#1230
  均未覆盖「门禁+编排调度器+CRS 推断+离群剖析」组合，无重叠交付。
- Issue 检索（脏数据/修复/CRS/离群/拓扑/投影 ≤300）：#597/#682/#680/#1110/#866 等为
  已修工具边界 bug，无开放重叠项。
- 分支检索：`fix/lint-quality-gate`、`foundation/quality-e2e-v9` 已合并，无进行中重叠分支。
- grep `repair_linkage_for_code|SpatialRepairPipeline|MISSING_CRS|NULL_ISLAND`：命中集中于
  本线触达文件 + data_quality/repair_execution + ingest_tools —— 与任务书 §0.2.6 一致。
- ADR-0140 已读：复用其 `REMEDIATION_OPS` 词表与 new-ref 红线；`data_quality/autofix.py`
  为另一执行器（规则键控），本线不合并、词表保持同源（fail-fast 守卫既有）。
- 编号：ADR watermark 0147，ADR-0153 未占用 ✓；零 Alembic 迁移 ✓。

## 既有失败（与本线无关，干净基线 09d839d3 复现）

- `tests/data/test_repair_plan_v4.py::TestMigration0028`（2）
- `tests/data/test_durable_blob_store.py::TestPutGetRoundtrip`（2，Windows 文件锁）
- `tests/data/test_quota_retention_v5.py::test_protection_parity_matrix_across_all_gc_paths`
- `tests/data/test_wave1_promotion_gc.py`（3）
- 全部在 `git stash` 后的干净基线上以相同方式失败（Windows 本地环境/既有问题）。
