# AC-02 交付台账（任务 → 文件 → 测试 → 证据）

> ADR-0151 · 分支 `adaptive-cartography/02-recipe-adjudication` · 2026-09-13

## 任务 → 文件

| 任务 | 文件 | 变更性质 |
|---|---|---|
| P1 多维资格 | `app/services/gis_harness/recipes.py` | `EligibilityContext`+`from_profile`、`FieldFacts`/`DistributionFacts`/`SpatialFacts`/`TemporalFacts`、`CheckResult`、6 检查器、`EligibilityRule` 新增 optional 维度字段、`FieldExpectation`；`check_eligibility` 叠加 V4 裁决 |
| P2 降级链 | `recipes.py`、`registry_validation.py` | `FallbackLink`/`DEFAULT_FALLBACK_CHAIN`/`FallbackAttempt`/`ChainResolution`/`resolve_fallback_chain`；悬空引用启动期校验 |
| P3 finalize 重写 | `planner.py` | 删 2 条硬编码分支 → `_ELEMENT_ALIASES` + 通用元素降级；链式换案 `_finalize_with_fallback_recipe`；`INSUFFICIENT_DATA` 说明卡 `_append_insufficient_data_card`；矛盾计划修复 |
| P4 可解释 | `recipes.py`、`planner.py`、`tools.py` | `FallbackDecision.attempts/auto_generated`；`render_fallback_for_llm`；输出 `fallback_llm`/`fallback_summary`；guidance 披露面 |
| P5 fact_signals | `planner.py` | `fact_signals(ctx, intent)` 通用投影 + `data_fact_signals` plan 字段 + 冲突→methodology_warnings；插值局部变量改名解遮蔽 |
| P6 第二事实源 | `components.py`、`model_library.py`、`planner.py`、`recipes.py` | 删兼容分支；`graduated` 收编别名（仅登记）；statistics/charts 字面量外迁 `default_statistics`/`default_charts` + task 派生规则 |
| P7 覆盖义务 | `recipe_packs/_kit.py` + 16 模块、`recipes.py`（seed） | `auto_fallback()` 助手；58 条 pack + 6 条 seed 补 `fallback_links` → 164/164 |
| P8 回归 | `tests/unit/gis_harness/test_recipe_downgrade_regression.py` | 30 样本 + 零静默路径 + 说明卡纵深 |
| 审计 | `scripts/recipe_eligibility_audit.py`、`.gitignore`、`docs/dev/ac-02-recipe-matrix.csv` | 矩阵 dump + 覆盖率审计（allowlist 白名单） |
| 文档 | `docs/adr/0151-*.md`、`docs/dev/ac-02-{plan,recon,decisions,ledger}.md`、`CHANGELOG.md`、`docs/workflows/workflow-catalog.md`（再生） | — |

## 任务 → 测试 → 证据

| 门禁项 | 结果 | 证据 |
|---|---|---|
| 164/164 fallback 声明 | ✅ | `python scripts/recipe_eligibility_audit.py`：total=164 missing=0；auto_generated 60/64 链级（台账列明） |
| 悬空引用校验 + 测试 | ✅ | `test_eligibility_v4.py::TestDanglingFallbackLinkValidation`（注入坏引用 → validate_gis_library 报错） |
| 30 不达标样本 100% eligible + reason_code | ✅ | `test_recipe_downgrade_regression.py` 32 passed（30 样本参数化 + 2 路径） |
| 零「全禁+点图兜底」静默路径 | ✅ | `test_no_silent_point_map_path`；planner RECIPE_INELIGIBLE 分支强制 disclosure+attempts |
| 6 检查器单测 + 边界（空集/单值/全 null/超大 n） | ✅ | `test_eligibility_v4.py` 39 例（含 empty profile / n=10^9 / 全 null 字段 / 边界值 8/30/500） |
| `build_default_components` 兼容分支删除 + 既有测试绿 | ✅ | `test_components.py` 20 passed；`test_fact_signals_v4.py::TestComponentAuthorityConsolidated` |
| 里程碑全量 `pytest tests/unit -m "not heavy and not real_services and not perf"` | ✅ | 见 PR 门禁证据（串行；xdist 不在仓依赖） |
| `ruff check <变更文件>` 0 告警 | ✅ | 逐文件执行记录 |
| 未改 `.github/workflows/**` | ✅ | `git diff origin/master --stat | grep workflows` 为空 |
| gis_harness 既有测试不劣化 | ✅ | `tests/unit/gis_harness/` 全绿（1247+ passed；3 skipped 为既有跳过） |

## 覆盖率台账（§5：auto_generated 占比列明）

- 元素级 `RecipeFallback`（存量）：100 条
- 链级 `fallback_links`（本线新增声明）：64 条
  - `auto_generated=true` 通用兜底链：60 条（58 pack + seed od_flow/raster）
  - 领域链（有意义目标）：4 条（categorical→poi、hotspot→point_density、
    proximity→poi、accessibility→proximity_analysis）
- 合计：**164/164（100%）**，auto_generated 占链级声明的 93.75%（60/64）、
  全量的 36.6%（60/164）。

## 协调点（PR 同步注明）

1. `FallbackDecision.attempts/auto_generated`：01 线统一契约时对齐。
2. `EligibilityContext` 新维度事实（distribution/密度/时间）：04 线供给。
3. `fallback_llm`/`fallback_summary` 事件字段：07 线消费呈现。
