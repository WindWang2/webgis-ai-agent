# GAP_ANALYSIS — 目标能力 T1–T9 × 现状（证据）

图例：EXISTS-FULLY / EXISTS-PARTIAL（缺什么）/ ABSENT。

- **T1 bounded DataQualityProfile** — PARTIAL。五套剖析/诊断层（DatasetProfiler、build_unified_profile、DatasetProfile、QualityReport、SpatialQualityReport）各自有界，但**无统一前置分析质量画像产物**：无聚合对象、无确定性 digest、无 per-issue 置信度、无 gate 裁决。
- **T2 确定性 field-role 推理** — 推理 EXISTS-FULLY（`semantic_profile.py`：证据分级、名称单独永不到 rule_derived、不确定即 unknown、≤200 样本）。**缺口**：(i) 无 FIELD_ROLE_AMBIGUOUS 发射（等证据竞争/样本反证角色时静默）；(ii) 推理结果不被 qualification/planner 消费 —— 低置信绑定在 measure/rate/count 上无闸（Oracle 缺口）。
- **T3 CRS/unit/time 归一化提案** — CRS EXISTS（`crs_safety.recommend_metric_crs` 提案 + reproject 需声明源的提案链）。unit：码存在但**零生产者**、无提案生成。time：**ABSENT**（无 timezone 证据检查；`time_parser` 生产默认 UTC 静默）。
- **T4 geometry repair preview + 指纹** — PARTIAL。`dry_run_autofix` 预览不突变；`execute_repair` 有 content_digest_before/after。缺：per-op 预测 vs 实际对照、修复事务状态机。
- **T5 schema/类别域和谐化映射** — ABSENT。最近的是 versioning_gate.suggest_renames（同数据集跨版本改名建议）。无 source→canonical 字段映射、无类别域映射契约。admin 码表 + Levenshtein 是可复用基底（guardrails，不重写）。
- **T6 RepairPlan 事务** — PARTIAL。plan 确定性 id + 新 ref 执行 + replaces 血缘 + 摘要证据都有。**缺**：状态机（proposed→dry_run→applied→verified/failed/rolled_back）、verify（修复后质量复评）、rollback 语义（替换链回指 + 血缘事件）、失败零残留保证的显式建模。
- **T7 planner/skill 资格接线** — workflow 路径 EXISTS；**V8 capability 路径 PARTIAL（质量盲视）**；无质量驱动的 clarify 通道（clarification.py 存在但非质量驱动）。
- **T8 质量报告 + methodology disclosure** — PARTIAL。scientific_evidence 是披露骨架；QualityReport.summary() 是 LLM 视图。缺：质量画像 + 修复 provenance + 披露的组合输出。
- **T9 issue 词表** — PARTIAL。四套词表并存（QualityIssueCode 21 码 / RULE_TYPES 16 / audit UPPER_SNAKE / guardrails CODE_*）。目标 9 码中 TIMEZONE_MISSING、FIELD_ROLE_AMBIGUOUS、ADMIN_MISMATCH（数据质量侧）缺失；UNIT_AMBIGUOUS 语义与 INCONSISTENT_UNIT 不同（欠定 vs 矛盾）。

## 结论（rescope 决定）

不再造第二套引擎。本方向 = **聚合层 + 受控词表扩展 + 缺失检测器 + 低置信闸 + 事务语义 + V8 接线**，全部 additive，全部复用既有词表/提案/执行/血缘管道。
