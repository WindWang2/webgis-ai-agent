# TEST EVIDENCE — GOAL-20261126-02

## R0 干净基线（master 59eb3f527，worktree 无代码改动时）

命令（共享主仓 venv，OMP=2 单进程）：
```
cd <worktree> && .venv-python -m pytest tests/unit/dataset_semantics tests/unit/gis_harness -q --no-cov -p no:cacheprovider
```
两次运行结果一致：**3759 passed / 10 failed / 5 skipped**（345s / 469s）

### 既有基线失败清单（10 项，与本线无关，只记录不扩线修复）

1. `tests/unit/gis_harness/representation_binding/test_binding_taxonomy.py::TestTaxonomyTable::test_registry_consistency_gate`
2. `tests/unit/gis_harness/representation_binding/test_binding_taxonomy.py::TestTaxonomyTable::test_all_registered_kinds_covered`
3. `tests/unit/gis_harness/test_component_composition.py::test_algorithm_taxonomy`
4. `tests/unit/gis_harness/test_component_composition.py::test_registry_validation_includes_component_system`
5. `tests/unit/gis_harness/test_review_harness_medium.py::test_governor_failure_uses_bounded_local_reservations`
6. `tests/unit/gis_harness/test_semantic_v2_families.py::TestRegistryIntegration::test_registry_cross_validation_clean`
7. `tests/unit/gis_harness/test_workflow_guards.py::TestManifestStaleEndToEnd::test_recipe_semantics_change_flips_plan_stale`
8. `tests/unit/gis_harness/test_workflow_guards.py::TestManifestStaleEndToEnd::test_manifest_with_full_packs_compiles_fatal_free`
9. `tests/unit/gis_harness/test_workflow_guards.py::test_workflow_profile_validation_is_wired_into_registry_validation`
10. `tests/unit/gis_harness/test_workflow_guards.py::test_catalog_matches_registry`

原始错误样本（#10）：`assert render_catalog() == committed` → 内容指纹漂移
`try 内容指纹：55218fdb…` ≠ 提交面 `4eed306f…`（生成的 workflow catalog 与提交的
golden 文本不一致，master 上生成器与 golden 已漂移）。判定：生成物漂移，与本线
（任务数据供给）无因果关系；本线不改 workflow catalog 生成器。

任务书点名文件基线：`test_preflight_v1.py` + `test_preflight_matrix.py` = **45 passed**（9.4s）

### Ruff 基线

```
.venv-python -m ruff check app/services/data_catalog app/services/gis_harness/workflow_v4/acquisition.py app/tools/data_discovery.py app/tools/catalog_discovery_tools.py
→ All checks passed
```

## 本线新增验证（逐 WP 追加）

（待填）
