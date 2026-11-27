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

## 本线验证汇总（R2 前，2026-11-27）

### 后端
- `tests/unit/task_data_supply/`：**82 passed**（含 5 旅程 + perf 基准 + 工具 dispatch + REST 契约），单进程 OMP=2
- mypy（7 个新源文件）：Success, no issues
- ruff 全量（app/ tests/ scripts/）：29 项错误 **与干净 master 逐字节一致**（#2281 披露的历史诊断；本线零新增零修改）
- 既有失败集回归：catalog/discovery 相关测试 2 failed 与 master 完全一致（test_audit4 keyword reachable ×2）；dataset_semantics+gis_harness 基线 10 failed 与 master 一致 → **失败集未扩大**
- perf 实测（declared-only 路径）：1k→p50 17.3ms/峰值 22MB；50k→p50 18.4ms/393MB（fixture 构造）；混合 footprint 50k→p50 22.2ms；真值集 0 误判
- 工具 conformance：本线 5 工具全 pass；全仓 fail=11 与 master 完全一致（他线遗留）；capabilities 覆盖 80%/缺122 与 master 持平（本线零恶化）

### 前端
- task-supply-panel vitest：5 passed
- data-sources-tab 既有测试回归：16 passed（3 文件）
- i18n 闸：key-completeness 9/10 passed（figureTopValues 占位符失配为 master 既有）；no-raw-cjk / en 无 CJK 通过
- typecheck：错误集与干净基线逐字节一致（5 项既有诊断）
- eslint（改动文件，--max-warnings 0）：通过

### 快照/契约
- OpenAPI 快照显式刷新：**master 提交面本就漂移**（干净 master 重新生成 = +13434/-5339，已用 detached master worktree 复现验证）；本线刷新 = master 漂移 + 本线新增 path（+89 行 api-docs 生成区）
- api-docs drift 闸 + API compatibility 闸：15 passed

### R1 修复后实测（2026-11-27）

- `tests/unit/task_data_supply/`：**99 passed**（新增 test_r1_fixes.py 17 项回归）
- perf 诚实化后（A-P1-3）：分区扫描 50k 候选 → p50 1.2ms（线性）；有界评估
  24 候选/角色：bbox 声明路径 p50 17.0ms、footprint metric 投影路径
  p50 113ms（成本差如实披露）；输出体积不随规模膨胀；真值集 0 误判
- mypy（8 源文件含 execution.py）：Success
- 面板 vitest 5 passed；i18n 闸（除 master 既有 figureTopValues 失败）通过
