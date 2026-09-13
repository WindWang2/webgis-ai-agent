# AC-02 制图配方自动裁决与泛化降级 —— 执行计划（ADR-0151）

> 分支：`adaptive-cartography/02-recipe-adjudication` · worktree：`../webgis-wt-ac-02`
> 基线：origin/master `09d839d3` · 任务书：`goals-ac/02-recipe-adjudication.md`

## 现状锚点（勘察确认）

- `recipes.py`：SEED 17 条 + `recipe_packs/` 24 模块 147 条 = **164 条**；
  `EligibilityRule` 三维（requires_geometry / min_points / requires_fields）；
  `RecipeFallback{when, reason_code, use, disable}`（已有声明但**无链式求解**）；
  `check_eligibility()` 对 disabled×fallbacks 单条 `break` 匹配（recipes.py:241-250）。
- `planner.finalize_with_profile`（planner.py:969-1060）：`visual_heatmap`/
  `density_overview` 与 `aggregate_grid` 两条**硬编码降级分支**；`RECIPE_INELIGIBLE`
  路径 = 全禁 + 追加点图兜底层（无方案 B/C）。
- `tools.py:1030-1059`：`NEEDS_ADMIN_UNITS` 的 fallback 声明消费（bind 期）。
- `fallback_v3.py`：工作流级四层裁决（preferred/degraded/minimal/blocked）——
  与本线的 recipe 级 fallback 链是两层，不冲突。
- `intent.py` `_HINT_OVERRIDABLE` / `_HINT_PROTECTED_TASKS`（intent.py:799-828）：
  P5 只读消费（intent.py 禁改）。
- `registry_validation.py:147-183`：Recipe 校验段 —— P2 悬空引用校验接入点。
- `data_qualification.py`：五态数据资格 + REMEDIATION_OPS —— EligibilityContext
  词表对齐对象。
- pytest.ini `pythonpath = .` —— 测试从 rootdir 解析 `app`，无需 `-e .`
  （pyproject flat-layout 自动发现本身是坏的，`pip install -e .` 失败：
  "Multiple top-level packages discovered"）。本线环境 = 仅装
  requirements-dev.txt（与 ac-03/ac-04 worktree 同法）。

## 阶段

- [ ] P0 勘察：`scripts/recipe_eligibility_audit.py` → `docs/dev/ac-02-recipe-matrix.csv`
      + `docs/dev/ac-02-recipe-recon.md`（含 10 个失效复现）
- [ ] P1 `EligibilityContext` + 6 检查器（样本量分档/字段基数/分布形态/CRS 尺度/
      时间覆盖/缺失率），返回 `{ok, reason_code, evidence}`，旧三维保留为 fast-fail
- [ ] P2 `fallbacks: list[FallbackLink{to, when, evidence_hint}]` + 
      `resolve_fallback_chain()` + registry_validation 悬空校验
- [ ] P3 finalize 重写：删两条硬编码分支 → 通用链式降级；全链 ineligible →
      数据不足说明卡（非空白图）
- [ ] P4 `FallbackDecision` 扩展（attempts/auto_generated）+ `render_fallback_for_llm()`
      + 工具输出字段（只改后端）
- [ ] P5 `fact_signals(ctx)` 通用化（planner 内，intent.py 只读消费）
- [ ] P6 删 `build_default_components` 兼容分支（词汇迁 model_library）+
      planner 字面量（`admin_bar`/`category_bar`）外迁 recipe 声明
- [ ] P7 147 条 fallback 分 8 批补齐（按 pack 模块），每批跑对应测试
- [ ] P8 30 样本回归 + 里程碑全量 + ADR-0151 + CHANGELOG + 台账

## 默认决策（§0.5 自动执行，不停下问人）

缺 fallback 声明 → 通用兜底（点图→分级图→表格）标 `auto_generated:true`；
多链 eligible → registry 排序键取最优并记录落选者；新旧维度冲突 → 新维度
（严格者）为准；降级改变几何类型 → 允许但必须带 `FallbackDecision`+披露；
全链 ineligible → 「数据不足」说明卡，禁止空 MapSpec。

## 边界（§8）

禁改：`intent.py`、`app/lib/cartography/{visualization_plan,classify,palettes}.py`、
`frontend/**`、`migrations/**`、`.github/workflows/**`。不新建 Alembic 迁移。
