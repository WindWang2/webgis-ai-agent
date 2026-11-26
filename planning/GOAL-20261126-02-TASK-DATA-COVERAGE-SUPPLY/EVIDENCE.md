# EVIDENCE MATRIX — GOAL-20261126-02（基线勘察 A/B 报告摘要，2026-11-27）

## 已实现（直接复用，不改）

| 能力 | 权威位置 | 要点 |
|---|---|---|
| 五态资格 | `gis_harness/data_qualification.py:31-33` | eligible/transform_required/degraded/blocked/unknown + REMEDIATION_OPS(8) |
| 语义身份证 | `dataset_semantics/`（ADR-0265） | dimension_provenance 三态；空 ≠ 伪造 |
| 任务范围 | `dataset_semantics/task_scope.py:49,86,112` | scope_fingerprint(place,bbox)、evaluate_scope_staleness；**只有 place/bbox，无时间** |
| 多数据预检 | `dataset_semantics/preflight.py:224,482` | preflight_multi_dataset + SCOPE_MISMATCH/UNVERIFIABLE + typed gap provide_role_data |
| 角色绑定 | `dataset_semantics/role_binding.py:361` | bind_session_roles：BOUND/AMBIGUOUS/NOT_FOUND/STALE...；role_qualification_evidence:561 |
| ADS 取数计划(ADR-0173) | `data_fabric/planning/`（compiler/cost_model/explain/replay） | PlanRequest(dataset_key,bbox,time_range,version,budget)→D2 AcquisitionPlan；choose_plan 预算内最小代价+超预算降级建议；**无生产入口** |
| 契约 D1-D4 | `data_fabric/contracts.py:66-247` | D1DatasetDescriptor(temporal/granularity/license/freshness)、AcquisitionStep(pushed_down)、FallbackDecision(comparable=False 默认)、AcquisitionFact |
| 源注册 | `data_fabric/source_registry.py:144` + config/sources/*.yaml(12 源) | pushdown 六旗标、license、temporal_coverage、geographic_coverage（**声明未参与评估**）、auth env 引用 |
| 语义检索(DS2) | `data_fabric/retrieval/service.py:111` | BM25+embedding(降级)；Filters(bbox/temporal/granularity/licenses)；rank_card coverage_score：bbox 相交+temporal 包含 → **排序命中，非需求侧覆盖矩阵** |
| 受控数值读取 | `data_fabric/coverage_read.py:108` | OGC Coverages subset→COG→ref:fabric-cog/；预算三重门 |
| 联邦目录 | `data_catalog/catalog.py:363` | 4 通道(session/upload/fabric/project-禁)、limit≤100、owner=sid 强制、bbox 相交过滤、sources_queried 诚实 |
| 血缘 | `data_catalog/lineage_query.py:110` | 有界(256 节点/16 深) |
| 取数事实/预算 | `data_fabric/facts.py:66` + `models/ads_fabric.py`(0070/0071 迁移) | FactsStore.record/check_budget/ratchet |
| 物化铸 ref | `data_fabric/materialization_service.py:437` | →ref:fabric-parquet/ + register_artifact（幂等/supersede/profile_ref） |
| durable jobs | `geocompute/durable.py:91,190,274` | queue_for_node/dispatch_node/await_node_job(s) |
| 版本钉扎 | `data_fabric/versioning_gate.py:282` | pin_or_fetch + DriftReport |
| ref 生命周期 | `artifact_registry.py:535` + `artifact_revisions.py` + `ref_lifecycle.py` | valid/stale/expired/superseded/failed |
| 融合(#2273) | `data_fusion_authoring/` + routes + tools | 本线只读边界，不复制 |

## 工具面现状

- `data_discovery.py`：6 工具（list/search/profile/describe/find_by_role/lineage），**全 session 作用域，无任务级 place/bbox/时间声明**
- `data_fabric_tools.py`：11 工具（connect/inspect/search_catalog/describe/query/materialize/refresh/**plan_data_query**(explain dry-run)/aggregate/federated×2）
- `catalog_discovery_tools.py`：4 工具（capability-first **工具链**发现，非数据目录）
- `project_data_refresh_tools.py`：8 工具（G10 刷新/staleness，owner 服务端反查）
- **retrieval.RetrievalService 与 planning.compile/choose_plan 均无工具面**（app/tools/ 零引用）

## 真实缺口（本线增量，两端断链）

1. **同名两物**：workflow_v4 `AcquisitionPlan`(角色×通道布尔声明) ↔ data_fabric D2 `AcquisitionPlan`(单 dataset_key 取数计划) **零转换**。缺 role+task_scope→PlanRequest 投影器。
2. **ADS 无生产入口**：compile/choose_plan 唯一消费方是离线 matrix harness；无 route 无 tool。
3. **族级需求哑化**：MethodologyFamily.data_role_demands 只进 typed DAG extra_roles，不生成 DataRoleResolution/acquisition 声明。
4. **acquisition_plan 不进 chat 规划证据**（Plan.workflow_v4 摘要缺）。
5. **task_scope 无时间维度**，且取数/覆盖评估不消费 scope。
6. **geographic_coverage/temporal_coverage/license 声明未参与任何覆盖评估**。
7. **任务级覆盖矩阵不存在**（每需求×每候选：full/partial/none+缺口维度+原因码）——命名 coverage_matrix/task_data_supply/supply_plan 全部 0 命中，未占用。

## R0 基线验证

- pytest tests/unit/dataset_semantics + tests/unit/gis_harness：3759 passed / 10 failed / 5 skipped（345s）——失败清单重跑确认中（初步：test_workflow_guards.py 2 项 registry 对账失败，疑为基线既有）
- ruff（data_catalog/workflow_v4/acquisition/data_discovery/catalog_discovery）：All checks passed
- preflight 两指定文件：45 passed
