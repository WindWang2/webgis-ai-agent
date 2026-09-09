# 02 — Runtime Unification 设计（Waves 1–2）

## 问题（证据见 00-baseline §A/B/C）

- Compiler V4 typed DAG 仅证据面接线（plan_orchestrator :643-710、semantic_tools :300-368），执行侧零消费。
- `build_plan_graph`（plan_graph.py:254）与 `build_typed_dag`（typed_dag.py:298）双轨；node_id 命名三轨（`cap:*` / `data:*` / ExecutionNode id）。
- `PlanNodeStatus`(7) 与 `StageState`(8) 双枚举并存。

## 设计原则

- 不消灭 `plan_graph`/`workflow_instance`（生产已接线、测试锁定），而是让 **typed DAG node_id 成为共享命名空间**，runtime 状态投影挂在同一 id 上。
- LLM 不得直接改 node state；状态迁移只经 `_mark_progress` / `maybe_update_workflow_instance` / bridge 的契约函数。

## W1：Canonical Workflow Runtime Projection

1. `gis_chapter` 新增 additive 键（如 `workflow_runtime_v6`），内容：
   - `node_map`: typed DAG node_id ↔ plan capability / data role 的映射表（bridge 生成）
   - `node_states`: node_id → `{status, stale_reason, failure_class, repair_state, recompute_policy, node_fingerprint, execution_evidence, artifact_refs}`
   - `package_ref`: WorkflowPackage 版本 + 指纹
2. node 映射规则（初定）：
   - `data:<role>` ↔ typed DAG acquisition/data 节点（role 对齐）
   - `cap:<capability>` ↔ typed DAG analysis 节点（algorithm/capability 对齐）
   - 无法对齐的节点显式记 `unmapped` 并披露（诚实降级，不静默丢）
3. 状态词汇：以 `StageState` 为实例面权威（含 stale），`PlanNodeStatus` 为行面权威；V6 需要的 `REPAIRING/DEGRADED/CANCELLED` 先查现有词汇映射，确无对应再经 `workflow_schema` 词表文件增补并加 parity 测试。
4. `node_fingerprint`：复用 `rows_fingerprint`（workflow_instance.py:167）算法（algorithm+params+input refs 哈希），单点计算。

## W2：Compiler → Runtime Bridge

1. bridge 模块落点：`app/services/gis_harness/workflow_v4/bridge.py`（新）或 `plan_orchestrator` 扩展——倾向独立模块，`plan_orchestrator` 调用，保持 compiler 纯函数。
2. 接线点：
   - plan 落地（合成 + LLM `make_plan` 两路径都要）→ 编译 V4 → 写 `workflow_runtime_v6`（修平 :712-759 vs :643-710 的不对称）
   - `SessionPlan._mark_progress` → bridge 更新 node_states（status/artifact_refs/证据）
   - `maybe_update_workflow_instance` → bridge 同步 stale/failure
   - `maybe_finalize_map_product` 前 → bridge 校验 node 完备性输入七维
3. 兼容：`workflow_runtime_v6` 缺席时行为逐字节同现状（kill switch 环境变量，比照 `GIS_CONTEXT_POLICY=0`/`GIS_TOOL_RETRIEVAL_V4=0` 先例）。
4. 测试：`tests/unit/gis_harness/test_runtime_bridge_v6.py`（映射完备性、状态契约、kill switch  parity）+ 更新 `test_workflow_v4_production.py`。

## 验收（W1–W2）

- Typed DAG 真正参与 runtime execution：执行事件可在 node_states 上观察到正确迁移。
- 任一 plan 行可回答：对应 typed node、其 artifact、其状态与指纹。
- LLM 路径与合成路径的 V4 证据对齐。
- 全量 `tests/unit/gis_harness/` 绿 + cartography 门绿。
