# G02 PLAN — 分 wave 实施计划

使命：GIS 算法适用性、科学方法契约与结果可信度 —— plan/execute/final 三段科学守门
一致性 + MethodUseContract 单一投影 + 守门链 oracle 语料。不新增空间算法，不修 #1758
认领的单点 bug。

## W0 基线/设计（本文件 + RECON + 矩阵）✅

## W1 契约层（纯增量，向后兼容）

1. `scientific_preconditions.py`：
   - `PreconditionResult.deferred: bool = False`（显式，不用消息启发式 —— temporal_field_required
     的 fact-backed PASS 无 facts_used，启发式必错）
   - 全部 deferred 返回点标 `deferred=True`（~14 处）
   - `pass_evidence_state(result) -> "proven_pass"|"unknown_deferred"|""`（非 PASS 返回空）
   - 五值 verdict 语义不变（候选资格非阻断承诺不变）
2. `algorithm_resolver.py`：
   - `_check_candidate` 返回 4 元组 (+not_evaluated)；PASS-deferred → `not_evaluated:<pid>:<原因摘要>`
   - `AlgorithmResolution.not_evaluated: List[str]`（additive，默认空，旧消费者零影响）
3. `method_use_contract.py`（新）：`MethodUseContract` schema v1 —— 纯投影自
   AlgorithmRegistry + parameter_contracts + method_references + crs_safety：
   algorithm_id/capabilities/crs_class/preconditions(id+desc)/parameters(unit/type/bounds)/
   method_references/fallback(fallback_algorithms+semantics)/uncertainty_outputs。
   `build_method_use_contract(algorithm_id)`；`iter_method_use_contracts()`；
   失败诚实缺省（unknown 字段空串/空表），绝不虚构。
4. `scripts/inspect_method_contract.py`（新）：只读 CLI（--algorithm/--all/--json/--check），
   结构化 findings（未知引用/单位词表外/缺 precondition 描述）。
5. 测试：tests/unit/gis/test_g02_contract_semantics.py（红→绿）。

## W2 生产入口三段守门

1. planner 转录修复：`AlgorithmSelectionRecord` + scientific_warnings/
   required_transformations/not_evaluated 字段；`_resolve_capabilities` 拷贝
   （workflow_compiler :448 现成消费者即刻受益）。第二个 resolve 点 :1626 同步核对。
2. 执行入口 fail-closed：`workflow_engine.resolve_step_tool` —— re-resolution 不可用
   且拒绝理由为**科学性拒绝**（scientific_precondition:/crs_class_mismatch:/insufficient_features:
   且 profile 事实在场）时不再静默回退 recorded tool：返回错误性 evidence + 不执行该步
   （保持 registry 视图未知/工具退役时的既有回退兼容）。科学性拒绝码集合 + 严格单元测试。
   evidence 增加 not_evaluated/precondition 摘要 + profile_digest（freshness token）。
3. `science_final_gate.py`（新）：`run_science_final_gate(plan_selections, execution_evidence,
   contract_lookup)` → `ScienceGateFinding(code, severity(block|disclose), subject, detail)`：
   - G-FALLBACK-SEMANTICS：fallback semantics ∈ {proxy,degraded,approximation} 未披露 → block
   - G-NOT-EVALUATED：not_evaluated 前提 → disclose（不 block，诚实降级）
   - G-FRESHNESS：plan profile_digest ≠ execute profile_digest → disclose
   - G-UNIT：契约 unit ∉ PARAM_UNIT_VOCABULARY → disclose（词表外）
   - G-MISSING-CONTRACT：算法无契约 → disclose
   有界（≤16 findings）、确定性、零 IO。
4. product_compiler 接线：compile_product_spec 内调用 final gate（plan.algorithm_selections
   → findings → result.science_findings + 决定 fallbacks 披露完备性）；enforce 开关
   读 settings（默认 advisory——fail-open 只在披露面，block 只对明确不等价未披露）。
5. tools/registry.py：`except ScientificError` 先于 ValueError —— std_error_response 带
   scientific_code/scientific_ref/guidance（error_type 保留类名）。
6. 测试：三段对账测试（同一方法 plan evidence == execute evidence == final gate 输入）。

## W3 失败注入/TOCTOU/并发

- profile 在 plan→execute 间变化（featureCount 降/CRS 变/nodata 出现）→ 执行门拒
- 同 plan 并发 resolve 幂等（resolver 纯函数验证）
- final gate 对畸形输入（空 selections/缺 evidence/循环 fallback）不抛异常、有界输出

## W4 科学 oracle 语料（≥8 组含负例，pytest-native 触发真实门）

1. numeric_field_required：fields 已知无数值 → 拒；未知 → not_evaluated 披露（负例）
2. projected_crs_required：地理 CRS → REQUIRES_TRANSFORM + hint 流到 plan record/transform step
3. min_numeric_samples:30：样本 10 → INSUFFICIENT_DATA；样本 20 → PASS_WITH_WARNINGS
4. nonzero_variance：方差 0 → 拒；deferred → not_evaluated
5. raster_band_required:5：band 3 → 拒（负例）
6. temporal：无时间字段 → 拒；观测数带（4/8 边界）
7. fallback：not_allowed 语义替补 → final gate block；proxy → 必须披露
8. ScientificError 穿透 dispatch：scientific_code 保留（负例：不出现 VALIDATION_ERROR 扁平化）
9. 测地/平面面积披露（aggregation geodesic 路径 area_crs 契约）
10. performance：全 registry 237 算法 resolve+contract 构建 < 2s（O(descriptor) 无 IO 断言）

## W5 独立 review（subagent C，未参与实现）→ 修复 → 同审查员复审

## W6 文档 + PR（gh pr create --base master，不合并）

## 停机条件

DoD 全项满足（见任务书 §5）且独立 review 无未解决 P0/P1。
