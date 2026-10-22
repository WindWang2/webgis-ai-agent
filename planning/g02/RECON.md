# G02 RECON — GIS 科学方法契约与结果可信度（基线 b1b2ec23, 2026-10-21）

## 0. 环境事实

- `git fetch`（HTTPS 直连）网络失败（SSL/connect 错误）；`gh api` 可用，已核实
  远端 master == 本地 origin/master == `b1b2ec23f10f251ddf8cf4d9fc0baabd03117013`。
- gh 已认证（WindWang2）。open PR：#1758（批量 issue 修复，head=zcode/swarm-review-fixes-v1，
  429 文件）、#1698/#1701（dependabot）。
- 本方向 worktree：`../webgis-g02-science-contract`，分支
  `feat/g02-gis-scientific-contract-vnext`（自 origin/master 新建，名称无占用）。

## 1. 真实调用链（勘察代理 + 主代理双重复核，file:line 均在 b1b2ec23 核对）

### Plan 侧（最强）
- 入口：`MapRequestIntent` → `app/services/gis_harness/planner.py` `MapProductPlanner.plan_from_intent`
- `planner._resolve_capabilities` (:710) → `resolver.resolve` (:736)
- Resolver 科学门：`app/lib/gis/algorithm_resolver.py`
  - 门1 CRS 类 :200-217（`crs_class_allows`，crs_safety.py:113；unknown 放行 :118）
  - 门2 前置条件 :219-239（PASS_WITH_WARNINGS→scientific_warnings；其余拒绝+transform_hint）
  - fallback 语义：`FallbackStep.semantics` (:27-36)，词表 `FallbackSemanticsClass`
    (algorithm_registry.py:39-45)；算法级链 :410-441、显式替补 :358-369
- 前置条件库：`app/lib/gis/scientific_preconditions.py`（18 固定 id + 3 参数化前缀；
  五值 PASS/PASS_WITH_WARNINGS/REQUIRES_TRANSFORM/INSUFFICIENT_DATA/INVALID_METHOD :27；
  **缺失事实 → PASS(deferred)，docstring :11-12 明示**；聚合 combine_verdicts :390）
- 数据资格：`app/lib/gis/data_qualification.py` qualify_data_role :402（用 facts_used 空判
  unknown —— 口径分裂点）；v4 归一化 `workflow_v4/methodology.py:829-861` 四态

### 执行侧（typed errors 被扁平化）
- 执行期 re-resolution：`app/services/workflow_engine.py` `resolve_step_tool` :140-215
  （fresh profile 来自 step args 的 ref 游标 `_resolver_profile_for_args` :217 —— TOCTOU
  事实已具备，但 **re-resolution 拒绝时回退执行 recorded tool（:204 used_recorded_tool），
  科学性拒绝不 fail-closed —— TOCTOU 空隙**）
- 实现层防线：geo_analysis 25 模块抛 `ScientificError` 子类
  （scientific_errors.py:43, 13 子类；ScientificPreconditionFailed :179）
- **断链**：`app/tools/registry.py:1682-1688` `except ValueError` 把 ScientificError
  （ValueError 子类）扁平化为 VALIDATION_ERROR；scientific_code/guidance/ref 全仓无消费

### 结果入图侧（无科学门）
- `app/services/gis_harness/product_compiler.py` compile_product_spec :170 —— 只转录
  refs/decisions/fallbacks（chart kind 级），无方法科学 evidence 面
- mapspec 目录（quality_gate_hook 等）纯数据质量面，无 scientific 引用
- `evidence_claim/carto_binding.py:20 bind_layer_to_statistic` 已建未接线（本方向不抢，
  属 #1755 已合并的 H02 体系）

### 断链 2（转录丢失）
- `AlgorithmResolution.scientific_warnings/required_transformations` (:57-58) 在
  `planner.py:739-754` 构造 `AlgorithmSelectionRecord` (:561-577) 时被丢弃；
  `workflow_compiler.py:448-452` 读 `s.get("required_transformations")` 恒为空。

## 2. 可复用权威（单一真相源，不复制）

| 权威 | 位置 | 内容 |
|---|---|---|
| AlgorithmRegistry | algorithm_registry.py（237 算法） | preconditions/crs_class/fallback_semantics/uncertainty_outputs |
| ParameterContract | parameter_contracts.py（122 契约 + PARAM_UNIT_VOCABULARY :28） | type/min/max/enum/unit |
| MethodReference | method_references.py（129 条） | 文献权威 |
| measurement | measurement.py（ADR-0207；11 MeasurementKind、CANONICAL_UNITS、危险码 :53-55） | 字段量纲 |
| PreconditionResult | scientific_preconditions.py:33 | verdict+facts_used |
| oracle 语料 | tests/science_oracles/（1216 case/14 域，sha256 钉扎 + regen 脚本） | 数值正确性 |

## 3. 与 #1758 交叠（详见 PR_OVERLAP_MATRIX.md）

- 主责 6 文件 + tools/registry.py + workflow_engine.py(resolve_step_tool 区域) + 
  product_compiler.py + workflow_compiler.py：**clean**
- `app/services/gis_harness/planner.py`：#1758 改 best_effort try/except（:49/:369/:832/:866/:894/:1082），
  与本方向 AlgorithmSelectionRecord(:561)/_resolve_capabilities(:736) **区域不相交**，可并行
- #1758 独占（本方向不碰）：geo_analysis 5 文件、crs_safety、longitude、
  execution_catalog_staleness、analysis_reuse、gen_science_oracles.py、CAPABILITY_CATALOG.md

## 4. 缺口清单（G02 差距）

1. PASS(deferred) 与 fact-backed PASS 无区分 → 最终制品无法披露 not_evaluated
2. scientific_warnings/required_transformations 转录断链 → transform step 恒空
3. 执行入口科学拒绝不 fail-closed（TOCTOU）
4. ScientificError 被 dispatch 扁平化 → 科学 reason code 丢失
5. 结果入图前无科学 evidence 门（方法/单位/freshness 对账缺失）
6. 无 MethodUseContract 单一投影（method/unit/precondition/reference 分散）
7. oracle 语料全数值向，无守门链 oracle（负例）
8. 测地/平面度量语义披露不统一（aggregation.py:235-300 仅面积特例）

## 5. 测试基线命令（见 TEST_MATRIX.md）

- `python -m pytest tests/unit/gis/test_scientific_contracts_vnext.py tests/unit/gis/test_scientific_errors_v4.py -q`
- `python -m pytest tests/unit/gis_harness/ -q`（ planner/compiler 面）
- `python -m pytest tests/science_oracles/ -q`（冻结语料回归）
- 全量套件有 #1753 存量挂起 —— **禁止无超时全量跑**
