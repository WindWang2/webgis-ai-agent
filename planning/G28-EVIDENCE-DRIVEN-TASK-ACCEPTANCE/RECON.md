# G28 RECON — 面向真实 GIS 结果的任务验收与有限修复闭环

- BASE_SHA: `b8f2a6ecc777d0ae633202034b609a48a8d140d9` (origin/master, 2026-11-11，含 #2084/#2085/#2086)
- 分支: `zcode/g28-evidence-driven-task-acceptance-20261111T153846Z`
- worktree: `C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g28-evidence-task-acceptance-20261111T153846Z`
- 快照 PR #2084/#2085/#2086 已全部合并；当前无开放 PR；开放 issues 均为 R1 审查项（有 owner，不接管）。

## 既有栈（已读源码核实）

- `app/services/gis_harness/goal_satisfaction/`（ADR-0183）: contracts/evidence/evaluator/projection/requirements。
  - RequirementKind = map/analysis/comparison/statistics/chart/export；RequirementState = fulfilled/partial/blocked/not_evaluated/failed。
  - PASS_CAPABLE_CLASSES = deterministic/structural（visual/assisted 永不单独背书）。
  - 唯一评估入口 `evaluate_goal_satisfaction(chapter, map_product, cartographic_review)`（纯函数）。
- Finalizer 唯一生产触发点: `app/services/gis_harness/completion/pipeline.py`
  - `map_product_block(...)` 内 ~L930: `evaluate_goal_satisfaction` → `block["goal_satisfaction"]`（持久化）。
  - L1339: `fresh.gis_chapter["map_product"] = map_product_block(..., chapter=awf_eval_chapter, ...)`（锁内，含 rows/product/observation 三重漂移守卫）。
  - L1614/L1771: `bounded_payload` → task_complete SSE 透传；`pi_event_mapper.py:328-338` 同样透传。
- 中间层对账: `app/lib/harness/goal_verification.py` `reconcile_goal_verification`（GOAL 05，消费 goal_satisfaction/observation_health/chapter）。
- 修复: `runtime_repair.py`（MAX_RUNTIME_REPAIR_PASSES=2，动作=重断言 spec 意图，user-owned/locked 不执行）；`loop_budget.py`（loop_retry_admissible/loop_charge，governor RetryBudget + 本地有界储备）。
- 前端: `goal_satisfaction` 载荷**无任何消费方**（grep 零命中）——UI 完成面只有 thinking-flag 翻转 + tool 行 + `map_finalization` 披露卡。P4 缺口确凿。

## G13–G20 回执面（调查员核实，file:line 见 INTERFACES.md）

| 能力 | 权威回执（生产写入者） | 关键字段 | 缺口（按 unknown 处理） |
|---|---|---|---|
| G13 能力执行 | 章节 analysis_steps 行（`session_plan._mark_progress`:782）+ `WorkflowRunState`（`load_workflow_runs`） | status/bound_ref/resolved_algorithm; envelope.ref/feature_count/empty/result_semantics | 行上无 mapspec_revision；dispatch 级证据瞬态 |
| G14 矢量编辑 | `EditReceipt`（`feature_edit/executor.commit_plan`:138-163，journal 持久孪生） | entry_seq/kind/content_revision_after/fingerprint_after/stamp_status/mapspec_revision/created_wfids/retired_wfids | 无显式 status token（entry_seq 在场=committed）；无行数增量 |
| G15 多画幅 | `verify_frame_product`（`frame_products/verification.py:26-125`）+ `frame_evidence_rows`:128 | frames[]{binding,observation,fingerprint,observed_at_revision}, problems, summary | **生产缺口：pipeline 从不写 `map_product["frame_product"]`**，evidence.py:200 消费的键只在合成语料中出现 |
| G16 标注 | MapSpec `layers[].label.authoring.overrides`（mutation_handlers.py:265） | featureId/idKey/sourceRevision/origin/frameId | 消费态（active/stale/orphan）渲染时重算，非持久 |
| G17 导入 | `CommitReceipt`（batch.json，`data_import/commit.py:95-115`） | status/output_ref/input_rows/output_rows/rejected_rows/crs_evidence/duplicate/plan_hash | 无输出 checksum；无数值 dataset revision；未挂接 chapter |
| G18 交付 | `DeliveryReceipt`（dl_store final，`data_delivery/service.py:558-598`） | status/row_count/content_sha256/scope{requested,delivered,missing_ids}/source{content_fingerprint,content_revision}/durable | 回读核验在 manifest["verify"]；与 chapter 无链接 |
| G19 字段计算 | `ComputeReceipt`（`field_compute/receipt.py` by_key JSONL） | row_count/computed_rows/selected_rows/skipped_rows/null_count/error_count; columns[]{unit,unit_status,measurement_kind}; input/output_content_hash | 未挂接 chapter；unit_status 默认 undeclared |
| G20 栅格 | raster commit `_receipt`（`raster_authoring/service.py:102-135`） | committed/data_revision/mapspec_revision/statistics_provenance{sample_count,approximate} | 回执不落持久 store（durable truth=MapSpec 层+源） |
| G10 刷新 | `CheckReceipt`+`AdoptionReceipt`（refresh/store ledger） | map_observation∈observed|unverified; revision_before/after | 项目级，无 session 桥 |
| MapSpec | map_state `_cartographic_mutation_revision` + `MapSpecResult.mutation_revision`（CAS expected_revision） | checked_revision 块锚 | 文档本身无 revision 字段 |

## 科学语义词表已在库中（P2 消费面）

- `app/lib/gis/measurement.py`: MeasurementKind count|rate|density|percent…; derive_field_semantics。
- `data_qualification._unit_dimension_guard`:322-372（分母绑 count 维/率无时间/密度无分母 → fail-closed）。
- `execution_compiler/units.py` build_unit_obligations/_adjudicate_denominator。
- 完整性: `SourceIdentity.completeness{truncated,total_count,total_matching,sample,is_complete}`; `ComputeReceipt` 行数诚实分母; `feature_aggregate` AGG_SCAN_LIMIT/`__other__`; delivery scope.missing_ids; refresh `denominator_changed`。
- CRS: `CrsEvidence.crs_source`; `SpatialInspectionReceipt.crs_source/axis_order_guessed`。

## 选区（S3）

- 服务端已有: interaction_ir/projection.py `project_interaction_snapshot`（selection view: layer_id/selected_ids/matched_count/ids_truncated）+ `ClaimedScope`（gis_situation/analysis_scope.py）+ selection_history。
- 不存在: 冻结 selection-snapshot artifact（G24 未合入）→ S3 只能诚实用 claimed scope + 截断披露，不伪造数据集回执。

## 环境与测试

- Python ≥3.12（本机 3.13.9）；pytest.ini timeout=180、asyncio auto、--cov 默认（跑小集用 --no-cov）。
- 目标测试: `pytest tests/unit/gis_harness/test_goal_satisfaction_*.py --no-cov -q`
- 既有锁定面: test_goal_satisfaction_v1.py 27 测试 + wiring 8 + corpus 4（≥100 案例、反事实反 pass）。

## 已核实缺口清单（本方向增量）

1. frame_product 终验块生产接线缺失（G15 证据死代码）。
2. G13–G20 新能力回执未进 goal_satisfaction 证据面（evaluator 只见章节行+产品块）。
3. 前端不消费 goal_satisfaction（A24 UI 侧缺口）。
4. 科学语义（单位/分母/总体完整性/CRS/陈旧修订/工件身份）无评估闸。
5. 修复动作与 goal_satisfaction 失败规则之间无可解释映射（P5）。
6. 语料无 receipt 族假成功负例（P6 reviewer 重点）。
