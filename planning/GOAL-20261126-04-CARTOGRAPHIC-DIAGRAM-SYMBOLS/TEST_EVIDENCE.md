# TEST_EVIDENCE — R0 干净基线（master 59eb3f527，本 worktree，2026-11-27）

## Ruff
- CI 逐字命令（ruff==0.16.8，production.yml:149,164）：
  `.venv/bin/ruff check app/ tests/ scripts/ main.py manage.py` → **Found 29 errors**
  - 与 #2281 PR 描述披露的 29 项完全吻合 → 归类为**历史既有**，非本线引入。
  - 分布：app/services/lakehouse/cube_service.py(3), rs_cube.py(5), raster_tile_service.py(1)，
    tests/(test_r1_tools_regressions_2146_2156 等 19 文件 20 项)。无 cartography/chart/glyph 域文件。
  - 本线新增代码必须 0 诊断；既有 29 项不扩线修复。
- 参考：ruff 0.17.6（未锁版本）仅 app/ → 9 errors（版本漂移差异，弃用，以 CI 0.16.8 为准）。

## 门禁命令（scripts/ci-local.sh 与 CI 逐字对齐，test_ci_local_gate_contract 钉住）
- lint: ruff(上) + mypy + debt_ratchet + dependency_bounds + eslint + import_boundaries + skills_lock + source_registry
- 前端: pnpm exec eslint . --max-warnings 0 / pnpm run typecheck / pnpm run test:ci / pnpm run build
- 契约层: pytest tests/test_tool_meta_contract.py 等 8 文件
- 后端全量: pytest -m "not perf and not cartography and not real_services" --cov --cov-fail-under=75
- cartography lane: pytest -m cartography --no-cov --timeout=120 -q   ← 本线相关基线，运行中
- perf lane: tests/benchmarks -m perf

## 待补
- [ ] cartography lane 基线结果（运行中）
- [ ] mypy 基线
- [ ] frontend typecheck/vitest/eslint 基线（#2282 披露 5 项历史诊断待复现归类）

## cartography lane 基线（pytest -m cartography --no-cov --timeout=120 -q，flock 错峰）
- 结果：**5 failed / 2804 passed / 32 skipped / 2 xfailed / 1 xpassed**，耗时 224s
- 失败清单（全部为 master 既有，非本线引入；逐一取证中）：
  1. tests/acceptance/gis_agent/test_contracts.py::TestPinConsistency::test_pin_exists_and_matches_corpus
  2. tests/cartography/test_carto_ir_corpus_v1.py::TestCorpusParse::test_corpus_spec_parses_valid_at_latest[scale_portrayal_cases]
  3. tests/cartography/test_component_variants_v3.py::test_every_descriptor_variant_has_template_entry
  4. tests/cartography/test_component_variants_v4.py::test_v4_variant_count_target
  5. tests/cartography/test_component_variants_v4.py::test_v4_bidirectional_variant_template_coverage
- ⚠ 若本线新增 component variants 会触碰 v3/v4 计数/双向覆盖契约，需先理解失败根因再动（取证中）。

## 基线失败取证（复跑 5 个失败用例的错误细节）
1. pin 漂移：`assert pinned == expected` → "corpus 与 pin 漂移——重跑 python scripts/g05_task_acceptance.py pin"（test_contracts.py:107）
2. corpus 解析：`KeyError: 'spec'`（test_carto_ir_corpus_v1.py:67，scale_portrayal_cases）
3. v3：`exploration_chart_panel: descriptor variant 'default' 无对应 native 模板条目`，`'default' in set()` 为空（test_component_variants_v3.py:58）
4. v4 计数：`assert reg.count == 23` 实际 24（test_component_variants_v4.py:37）——master 上已有人加变体未同步契约
5. v4 双向：同 3 的 `exploration_chart_panel/default 无 native 模板条目`（test_component_variants_v4.py:50）
- 归类：master 既有故障，非本线引入；不扩线修复。
- ⚠ 与本线的交互风险：若本线组件走 component variant registry，则 v4 硬计数契约（==23）必须随新增同步，且需先修复既有漂移才可验证。图符若作为 map renderer 而非浮动面板 variant 则不触碰该契约——待前端审查结论定架构。

## mypy 基线（.venv/bin/python -m mypy，与 ci-local.sh 一致）
- 结果：**Found 1 error in 1 file (checked 38 source files)**：app/core/tenancy.py:149 var-annotated
- 归类：master 既有，非本线域；本线新增代码 0 新诊断。

## 前端基线（pnpm run typecheck + eslint，frontend/，干净 master）
- typecheck：**exit 2，2 项既有错误**（#2282 披露的历史诊断本地复现归类）：
  1. components/flow/flow-product-panel.test.tsx(261,35) TS2345 supported_modes string[] vs FlowDirectionMode[]
  2. lib/mapspec/live-spec.test.ts(112,70) TS2353 'tiles' 不在 GeoJSONFeatureCollection | HeatmapRasterSource
- eslint：无输出（0 问题）。均为既有，非本线引入；本线新增代码 0 新诊断。

## 契约层验证（WP4 后端接线后）
- tests/test_tool_meta_contract.py: 18 passed + **2 failed（test_extension_example_names_exist / test_extension_routes_distribution_before_status_pull）**
  - 干净 master（临时 worktree @59eb3f527 复核）：同样 2 failed → **既有基线故障**（extensions_platform 示例文件断言），非本线引入，不扩线修复。
- tests/test_import_boundaries.py 全过；scripts/check_import_boundaries.py OK；import app.main OK。
- 干净 master 复核同时再证 scale_portrayal corpus KeyError 为既有。
