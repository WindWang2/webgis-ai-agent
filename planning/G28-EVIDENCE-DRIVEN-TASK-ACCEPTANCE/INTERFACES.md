# G28 INTERFACES — 消费的已发布接口（只读）

## 我消费（不修改）
- `frame_products.verification.verify_frame_product(mapspec, observed)` / `frame_evidence_rows` — `app/services/gis_harness/frame_products/verification.py`
- `frame_products.service.frame_product_status` — 观察回执 `{frame_id:{revision,mode}}` 来源 = render observation / export receipts
- `feature_edit` journal 读路径（head receipts）— service 层只读入口
- `data_import` batch 状态读（batch.json receipts map）
- `data_delivery` store 收据读（dl_store final receipt + manifest.verify）
- `data_preparation.field_compute.receipt` 读（find/list by session）
- `raster_authoring` MapSpec 层+源（numeric_raster data_revision 对账）
- `project_runtime.refresh.store` ledger（AdoptionReceipt/CheckReceipt）
- `execution_compiler.load_workflow_runs / merge_workflow_rows / workflow_disclosures`
- `runtime_repair` 动作词表 + `loop_budget.loop_retry_admissible/loop_charge`
- `render_observation` / `observation_states.build_observation_summary`

## 我新增（owner 范围）
- `goal_satisfaction/receipts.py` — `collect_capability_receipts`、`receipt_evidence_rows`、`ReceiptsBundle`
- `goal_satisfaction/semantics.py` — `semantic_caps`（P2 闸）
- `goal_satisfaction/facets.py` — frame/observed 切面只读投影（P3）
- `goal_satisfaction/repair_actions.py` — `repair_plan_for`（P5）
- `goal_satisfaction/contracts.py` 加性: EvidenceKind.CAPABILITY_RECEIPT/DATA_REFRESH、GoalRequirement.delivery_expected、cap 常量
- `completion/pipeline.py` 小补丁: 锁外预取 + `map_product_block(receipts=, frame_verification=)` 可选参数 + 块持久化 + SSE 摘要透传
- 前端: `frontend/components/chat/goal-satisfaction-card.tsx` + task_complete 消费点加性接线

## 明确不做
- 不新建第二 evaluator/第二完成 Boolean/并行任务库
- 不接管 R1 issues、不重写全局 budget、不用视觉 LLM 当裁判
- 不写占位 fake receipt；无合入能力（G22/G23/G24/G25/G26/G27）→ unsupported/not_observed
- 不升级 Pi vendor（G22 所有）
