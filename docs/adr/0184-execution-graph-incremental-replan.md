# ADR-0184: GIS Execution Graph + Incremental Replanning（execution-graph v1）

- 状态：Proposed（随 `harness/execution-graph-incremental-replan-v1` 分支评审）
- 日期：2026-09-14
- 关联：ADR-0076（SessionPlan 事实源）、ADR-0104（Workflow V4 / Runtime Instance）、
  ADR-0134（HarnessRuntime V7：D1 状态机 / D2 PlanRuntime）、Epic workflow-v5
  （Semantic Workflow Runtime V5）、方向 5 任务书（Execution Graph + Incremental
  Replanning）

## 背景

方向 5 要求把「每轮从头规划 + 串行调用工具」升级为可持久、可失效、可局部重算的
GIS Execution Graph。Phase 0 勘察（`docs/dev/execution-graph-replanning-recon.md`）
确认 master @ 580b33e9 已有两层图骨架：

1. **计划事实层**：`SessionPlan.gis_chapter`（行状态唯一写手 `_mark_progress`）
   → `plan_graph` / `workflow_instance` / `runtime_bridge` 三个纯投影 →
   `[GIS Plan]`/`[GIS Recompute]` 文本行喂给 Pi；
2. **可执行账本层**：`app/services/workflow_runtime/`（V5）——两级 CAS、
   Driver（拓扑 ready、有界并发、租约、取消、deadline、孤儿复位、STALE 拓扑
   安全重入队、复用指纹裁决）、ChangeApplier（quiescence defer + `compute_affected_subgraph`）、
   ReuseIndex、启动恢复、REST API。

真正缺口（G1-G3）：

- **G1**：follow-up 情境变更在 chat 路径从不进入增量重算引擎——同 goal replace
  void 全部行、异 goal supersede 全量归档，均为全量重算语义；
- **G2**：`compute_affected_subgraph` + ChangeApplier 仅 REST 可达；
- **G3**：节点副作用无分类词表，重试/重算无 at-least-once / at-most-once 语义。

## 决策

### D1 — 不新造第五套 DAG；Execution Graph = 既有三元组的生产贯通

图结构事实源仍是 `workflow_v4.typed_dag`（编译期）+ `plan_graph`（行投影）；
执行态事实源仍是 V5 instance/node 行（DB CAS）。本 ADR 不引入任何新图 IR、
新 planner、新 tool loop。Pi 仍是 Agent Host：[GIS Plan] 建议性文本 + 行状态
单写者纪律（runtime_bridge 自述的架构红线）原样保留。

### D2 — 意图差异裁决：chapter 结构化 diff（`intent_diff.py`）

新增纯函数 `gis_harness/intent_diff.py::diff_chapters(old, new)`：

- 精细变更维（scope/subject/time/measure/group_by/comparison/task/recipe）
  只做**携带门控**；coarse 维（⊆ `RECOMPUTE_DIMENSIONS`）进 V5 引擎；
- 携带（carried）= 行语义签名不变（排除 status/bound_ref 完成态字段）
  + 数据行过 scope/time 门 + 依赖闭包全部携带 + global 重塑维未变；
- 保守红线：宁可漏携带（重算），绝不错误携带（stale 复用）。

**全局重塑维**（task/recipe/measure/group_by/comparison）任一变化 → 零携带
（等价现状全量失效）。`dataset` 维是保留词：chapter 当前无数据集版本事实，
漂移由既有 artifact health 复用校验（runtime_bridge W5）在执行面兜底；
#1275（gis_situation.diff）合并后可经同一 adapter 口升级变更源。

### D3 — 计划侧落地：replace 保留携带行，supersede 预置携带

`session_plan._apply_tool_result_unlocked` 的 `webgis_map_intent` 分支：
replace 只 void 非携带行（携带行 complete + bound_ref 存续并写回新 chapter
行）；supersede 用 `_seed_progress` 预置携带行为 complete。行状态**词表**
仍是 `ProgressStatus`（单一事实源不变）；写入点全部收敛在 session_plan
会话锁内（`_mark_progress` / `_seed_progress` / `apply_intent_diff_to_chapter`
—— review P2-1 更正：master 本就存在 `_mark_progress` 之外的行状态写手
（如 tools.py 的重置路径），本 ADR 的纪律是「新写入点必须锁内、同词表、
同语义」，而非字面意义的第一写手）。`GIS_INTENT_DIFF_REPLAN=0` 一键回到
master 全量语义（kill switch）。

### D4 — 执行侧落地：`apply_intent_facts` 复用 V5 唯一引擎

`WorkflowRuntimeService.apply_intent_facts`：lost → 节点级 PendingChange
（analysis → `cap:<cap>`；数据 capability → `data:<role>`）→ 既有
`apply_changes`（ChangeApplier：quiescence defer + STALE CAS）；carried →
`_chat_complete_node` 拓扑序直推 SUCCEEDED（复用，零执行）。V5 闭包与
chapter 闭包的结构差由完成 CAS 后的 STALE 窗口复查保守吸收（不洗白）。

### D5 — chat 完成通道的起点状态诚实分类

`_chat_complete_node` 此前只接受 PENDING/BLOCKED 起点——REST `apply_changes`
标 STALE 的节点在 chat 通道永远无法完成（既有缺口，方向 5 的失效路径会放大）。
现补齐：PENDING 绑定 / BLOCKED 解除 / **STALE 重入队（CHAT_RECOMPUTE，与
driver 的 STALE_RECOMPUTE 同语义——chat 重执行是 Pi 驱动的等价重算，
receipt = 工具 ref）** / READY 直推（attach 预绑完成路径）。

### D6 — 副作用纪律（E3）

`TypedWorkflowNode.side_effect ⊆ {pure, derived_external, destructive}`
（kind 派生缺省：data_input → derived_external；bounded dict additive 字段，
旧包按 kind 诚实降级）：

- pure / derived_external：at-least-once，可自动重试（失败半提交产物已有
  compensation 清理；geocompute 通道 `idempotent=True`）；
- destructive：**at-most-once**——driver 自动重试关闭（即使错误码可重试，
  终态 FAILED + `DESTRUCTIVE_NO_AUTO_RETRY` journal 证据）、STALE 只作披露
  不自动重算；`retry_failed_nodes` 显式指令是唯一重驱通道。当前无 destructive
  生产产生源，词表保留给外部写出类节点。

### D7 — 图事件（E8）：`workflow_graph` SSE 事件族，复用既有通道

新增 `workflow_runtime/graph_events.py`：`replanned`（意图维 + carried/lost
+ V5 决策摘要）与 `node_states`（V5 节点批量投影）两种 payload，≤8 项/事件、
无时间戳。事件经 `SessionPlanEvent` 随 `apply_tool_result` 事件列表流出
（pi bridge `events_to_sse` → 既有 per-toolCall SSE 缓存）——**零新通道、
零 websocket**；CanonicalPlan 三事件名禁用门不受影响。V5 journal 是完整
事实，SSE 只是有界投影（不逐转移刷流）。

### D8 — 语料（E9）

`tests/unit/gis_harness/test_graph_replan_corpus.py`：10 类人审情境（对应
任务书 10 必做场景的计划侧语义）× 5 变体 = 50 确定性场景。每场景断言零错误
携带、保守失效、global 重塑零携带；聚合指标：全语料平均节点节省 0.650，
保守类（scope/time/task）0.125（正确的全失效语义），style/output/resubmit/
pin/resume 类零科学重算。

## 后果

- 正面：follow-up 局部重算成为生产事实（场景 2/3/5/6/8/10 不再全量重跑）；
  V5 执行语义（超时/取消/重试/复用/恢复）经 chat seam 首次可达；副作用纪律
  为外部写出节点铺路；事件面为前端进度/replanned-diff 提供契约。
- 风险与缓解：意图误判携带 → 保守门（global/data 门 + 闭包）+ W5 artifact
  health 兜底 + kill switch；chat STALE 重入队扩大 Pi 重执行面 → 仅影响被
  变更闭包污染的节点，且 receipt 语义不变。
- 后续接口点：#1275 situation diff 作为变更源升级；#1277 SessionPlan v2
  合并后迁移 intent 分支 hook 调用点；前端 `workflow_graph` 事件订阅；
  driver autorun 面向 auto-executable 子图的策略（本 ADR 不开启）。
