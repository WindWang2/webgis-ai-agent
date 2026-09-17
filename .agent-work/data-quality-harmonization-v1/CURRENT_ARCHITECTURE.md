# CURRENT_ARCHITECTURE — 质量与和谐化现状架构（base faa453a8）

## 质量相关事实源（全部复用，不新建平行系统）

| 职责 | 契约 | 位置 |
|---|---|---|
| 受控 issue 词表 | `QualityIssueCode`（21 码 str-Enum，受控扩展策略） | `app/lib/data/quality.py:34` |
| 剖析证据质量检查 | `run_quality_checks(DatasetProfileV3) → QualityReport` | `app/lib/data/quality.py:331` |
| 规则引擎 | `RULE_TYPES`（16）+ `RULE_FUNCTIONS`（import 期对账） | `app/services/data_quality/rules.py:27`、`rule_functions.py` |
| 修复提案缝 | `propose_repairs`（码→REMEDIATION_OPS 单点映射+词表守卫） | `app/services/data_ingest/repair_planning.py:211` |
| 修复计划实体 | `RepairPlan`/`RepairStep`（确定性 plan_id） | `app/services/data_quality/repair_plan.py:83` |
| 修复执行 | `execute_repair`（新 ref + replaces 血缘 + repair_evidence 摘要） | `app/services/data_quality/repair_execution.py:101` |
| dry-run | `plan_autofix`/`dry_run_autofix` | `app/services/data_quality/autofix.py:47` |
| 空间审计 | `SpatialQualityEngine.audit_dataset`（5 维、bounded topology） | `app/services/spatial_quality_service.py:141` |
| 修复编排器 | `plan_repair_ops`（CANONICAL_OP_ORDER + 破坏性裁决） | `app/services/spatial_repair_pipeline.py:217` |
| pre-cartography 门 | `evaluate_quality_gate`（enforce/advisory/off） | `app/services/spatial_quality_gate.py:637` |
| 语义角色推理 | `derive_semantic_profile`（rule_derived>metadata>user；不确定即 unknown；≤200 样本） | `app/lib/gis/semantic_profile.py:215` |
| 语义角色薄适配 | `infer_field_roles`（声明级，cartography D1 线） | `app/services/data_fabric/semantic/field_roles.py:24` |
| 五态数据资格 | `qualify_data_role`（eligible/transform_required/degraded/blocked/unknown） | `app/services/gis_harness/data_qualification.py:262` |
| V8 资格引擎 | `QualificationContext`/`qualify_node`（additive 友好，ADR-0181 先例） | `app/services/gis_harness/qualification_v8.py:75` |
| 方法资格 | `QUAL_<DIM>_<VERDICT>` 稳定码 | `app/lib/gis/methodology/qualification.py:34` |
| 行政区校验基底 | `admin_division_verifier`（码表 + Levenshtein 最近码） | `app/services/spatial_guardrails/admin_division_verifier.py:60` |
| artifact/血缘 | `register_artifact(profile_digest=)`、`ArtifactGraph.replacement_chain`、`record_revision` | `app/services/artifact_registry.py:454`、`artifact_revisions.py:56` |
| 科学证据披露 | `scientific_evidence`（capabilities/inputs{crs,units}/transformations_applied/…） | `app/lib/gis/scientific_evidence.py` |
| resolver 事实出口 | `DatasetProfile.to_resolver_profile()`（camelCase 单出口） | `app/lib/gis/dataset_profile.py:159` |

## 消费链（T7 现状）

- workflow 路径已接线：`workflow_compiler._run_qualify`（:258，stage blocked 语义）、`plan_candidates`（data_fit 评分）、`workflow_v4/compiler_v4`、`workflow_v4/methodology`。
- V8 capability 路径盲视质量：`capability_resolution.build_situation`（:186/:239）只吃 crs/geometry/feature_count/bands。
- `derive_semantic_profile` 唯一生产消费者是 `app/tools/semantic_tools.py:172`（工具展示），**planner/qualification 不消费**。

## 关键红线（来自模块 docstring 与回归锁）

1. **unknown ≠ unsatisfied**：画像缺事实不得判不满足（test_data_qualification 锁）。
2. **plan-only / 新 ref**：提案层绝不执行；执行产物只进新 ref（源载荷永不被覆写）。
3. **单一词表**：REMEDIATION_OPS 是唯一修复词表；新码必须进 QualityIssueCode + _REPAIR_MAP 受控映射；RULE_TYPES/RULE_FUNCTIONS import 期对账。
4. **bounded**：计划 ≤16 步、issues ≤32、样本 ≤200、扫描 20k(sync)/200k(job)、advisories ≤16；阈值单点 `app/lib/cartography/data_tiers.py`。
5. **确定性 id**：plan_id/profile_digest 排除墙钟；同输入同 id（幂等复用依赖此）。
6. **fields_status=="explicit" 闸**：截断 schema 的空字段列表 ≠ 字段不存在的证据。
7. **asyncio.to_thread**：重 CPU 几何/剖析必须在 thread（repo 红线）。
8. **命名碰撞**：`RepairPlan`（data vs gis_harness.repair_planner）、`QualityReport`（pydantic vs SQLAlchemy model）——import 必须 module-qualified。
