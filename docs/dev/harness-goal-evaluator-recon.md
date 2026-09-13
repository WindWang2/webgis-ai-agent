# Harness Goal Satisfaction Evaluator — Recon（Phase 0）

- 基线：`origin/master` = `580b33e923e992cd6706659d455dfd72ef55d033`（PR #1272 Adaptive Data Supply V1 合并后）。
- 分支：`harness/goal-satisfaction-evaluator-v1`，独立 worktree（主工作树零改动）。
- 勘察日期：2026-09-14。

## 1. 现有 evaluator / verdict 版图（G0 输入）

master 上已有 **5 个评价/裁决层**，各自回答不同问题：

| 层 | 位置 | 回答的问题 | 输入 | 输出 |
|---|---|---|---|---|
| L1-L4 完成契约 | `app/services/gis_harness/completion/contracts.py` `evaluate_completion_contract` | 地图产品在 7 维（data/analysis/science/cartography/observed_map/methodology_disclosure/uncertainty_disclosure）上是否成立 | MapCompletionResult + chapter | 7 维布尔 |
| Product Verdict | 同上 `derive_product_verdict` | 单字产品裁决：READY / READY_WITH_WARNINGS / NEEDS_REPAIR / BLOCKED_BY_DATA / BLOCKED_BY_METHOD | 完成态 + 契约 + cartographic review | verdict + reasons |
| Final Map Verification | `completion/map_verification.py` | 最终图面 verified / verified_with_degradation / failed / unknown | 观察证据 | final_map_status |
| L5 Visual Judge | `app/lib/harness/visual_evaluator.py` `derive_goal_satisfaction` | 视觉可读性（record-only，evidence_class=visual 不得单独判 PASS） | 截图 + VLM/injected judge | pass/fail/not_evaluated |
| HarnessEvaluator | `app/lib/harness/evaluator.py` | run 级质量门（ToolChoiceAccuracy/MapSpecValidity/…/CartographicQuality） | harness telemetry | 离线 gate 报告 |

辅助裁决面：
- `intent_acceptance.py`（V7 ADR-0134 D6）：planned result layers + 组件槽位 + verdict → `accepted` / `intent_verified`。**这是最接近任务语义验收的既有面，但只覆盖「计划图层/组件在场」，不覆盖分析/比较/导出/统计等非图请求。**
- `continuation.py` `decide_continuation`：repair/deepen/requalify/replan/abort 回路的单一裁决点（预算驱动，不看任务语义）。
- `runtime_state_machine.py` `derive_runtime_phase`：章节事实 → committed/finalizing/…/idle 阶段；`_task_complete` = product_verdict READY ∧ final_map ∈ {verified, degraded}。
- `SpatialGoalGraph`（goal_graph.py）：目标 → 方法学骨架投影（无满足判定）。

## 2. 关键缺口（本任务的合法空间）

`_task_complete`（`completion/pipeline.py::_is_task_complete` / `runtime_state_machine._task_complete`）是当前**任务级完成布尔**，但它只折叠：
- product_verdict ∈ {READY, READY_WITH_WARNINGS}
- final_map_status ∈ {verified, verified_with_degradation}

它**不看**：
- 用户显式要求的分析/比较/统计是否真的产出（比较缺位时地图 READY 仍 task_complete=True）；
- 导出交付（plan.exports / intent.export_intents）是否兑现；
- 数据是否足以支撑结论（空结果 200、fallback 不可比源）；
- 多子目标的逐项 ledger（部分完成被整体 PASS 吞掉）；
- 证据 revision 是否 stale（旧 artifact / 旧 export）；
- 用户 pinned 约束（must / must-not）是否被尊重。

即：**「地图好看」≠「用户任务完成」的缝隙真实存在**，且 master 没有任何模块拥有「用户任务语义层」的需求面与证据面。

## 3. 生产调用链（before）

```text
用户消息 → Pi (vendor agent loop) → PiBridge (agent_pi_bridge.py)
  ├─ tool dispatch → gis_harness tools → SessionPlan 行进度 (apply_tool_result)
  │                                          └─ gis_chapter{data_requirements/analysis_steps/map_layers/...}
  ├─ turn 收尾 (agent_settled 前)
  │    ├─ maybe_finalize_map_product(session_id, final_gate=True)   [pipeline.py:577]
  │    │    ├─ 去重门(revision+rows_fp+render_seq)
  │    │    ├─ run_map_finalization → validators → repairs → final_map_status
  │    │    ├─ derive_product_verdict → result.product_verdict
  │    │    ├─ repair_planner.plan_repairs_for_chapter
  │    │    ├─ _finalizer_continuation → decide_continuation → (request_replan)
  │    │    └─ 锁内持久化 chapter["map_product"]（supersede/revision/rows/obs 四守卫）
  │    │         └─ map_product_block 内含 task_complete 布尔
  │    └─ read_stored_map_product → turn_stats["map_product"]
  └─ agent_settled → pi_event_mapper._handle_agent_settled
       └─ SSE task_complete{map_product:{status,summary,task_complete}}
            └─ 前端 finalizer / runtime_state_machine.derive_runtime_phase 消费
```

G5 接线点决策：**Goal evaluator 在 `maybe_finalize_map_product` 成功产出（或读取）`map_product` 块后、`task_complete` 折叠处消费**——不新开第二终验管线，不复制 Pi loop，不触碰去重门语义。

## 4. 与最近/在途 PR 的重叠矩阵

| 能力 | #1271 AC-V11 (merged) | #1272 ADS-V1 (merged) | #1273 qc-loop (open) | #1274 typed tool (open) | #1275 situation (open) | #1276 cap-graph (open) | #1277 kernel (open) | #1278 skill lib (open) | #1279 cost gov (open) |
|---|---|---|---|---|---|---|---|---|---|
| 制图质量/verdict | ●（主 owner） | — | ○ 复用 | — | — | — | — | — | — |
| Goal Contract schema | — | — | — | — | — | — | ○ SessionPlan v2 相邻 | — | — |
| Evidence registry（任务语义） | ○（carto 域） | ○（数据域） | — | — | ○（情境域） | ○（能力域） | ○ | — | ○（资源域） |
| Deterministic requirement evaluator | — | — | — | — | — | — | — | — | — |
| Multi-goal partial ledger | — | — | — | — | — | — | — | — | — |
| Stop/replan 信号 | — | — | — | — | — | — | — | — | — |
| Anti-cheat counterfactual corpus | ○（ratchet 基线） | ○（fixture 域） | ○（review 语料） | — | — | — | — | — | — |
| Acceptance corpus（goal 级） | — | — | — | — | — | — | — | — | — |

● = 主覆盖，○ = 相邻/部分，— = 无。**结论：任务语义层的 Goal Contract + Evidence Registry + deterministic 逐项裁决 + multi-goal ledger + 停止/重规划信号在 master 与全部 open PR 中无主 owner。** 详细 per-PR 结论见 decisions 文档与 Subagent A 报告归档（附录 A）。

## 5. 旧审计 finding 仍成立的核验

- 「完成 → 意图满足」循环论证：V7 intent_acceptance 已修复**图层/组件面**；分析/导出/统计面仍无独立判定（本任务补全，复用其 user-wins 语义）。
- `_task_complete` 双形状 verdict bug（评审 F1）已修（`_product_verdict_token`）；本任务消费时必须走同一 token 函数，不复制逻辑。
- visual judge record-only：`derive_goal_satisfaction` 只是 L5 视觉推导——**不是**任务完成判定，不得让它单独判 PASS（本任务红线）。

## 6. 复用 / 扩展 / 不做 清单

**复用（只读消费）**：product verdict + `completion/pipeline._is_task_complete`、`_product_verdict_token`、chapter 行事实、`intent_acceptance`（作为 product 层证据源之一）、render observation、`decide_continuation`、visual judge 摘要（作为 evidence 行）、V11 cartography metrics/ratchet（只消费不重算）、`app/evaluation/` 语料模式（GISBenchmarkCase / corpus builder / runner 模式）。

**扩展**：`map_product` 块 additive 键（goal_satisfaction）；`read_stored_map_product` 载荷 additive 键；SessionPlan chapter additive 键 `goal_contract`。

**不做（防重复）**：不建第二 workflow truth / planner / registry；不改 completion 管线的 status 语义；不吞 #1270 CI hygiene；不从 raw text regex 抽需求（来源 = intent/SessionPlan/MapProduct 结构化事实）；不重复 L5 视觉判定。

## 附录 B：Subagent A 勘察归档（PR 维度，2026-09-14）

- **open PR 8 条**：#1270 CI hygiene（不吞，遗留 4 项 P1/P2 follow-up 归 #1270 线）；#1273 qc-loop 5 轮收敛（改 `completion/pipeline.py`、`runtime_state_machine.py` 等——**语义基线：全量 findings 判终态、verdict token 归一**，本任务接线必须兼容）；#1274 typed tool surface（ADR-0180，无重叠）；#1275 situation/world model（ADR-0180，§10 预留「Goal evaluator 消费 SituationDelta」下游接口——未合并，本任务不 import，仅留适配注释）；#1276 capability graph（ADR-0181，`GoalRequirements` 为能力级子集，未合并不 import，命名对齐）；#1277 harness kernel + SessionPlan v2（ADR-0180，`patch_plan`/StepEvidence 协议先行未接线；`goal_graph.py` 被其标注为「无生产 importer 实验模块」）；#1278 skill library（ADR-0182，`SkillEvidenceRecorder` docstring 预设 Goal Evaluator 为下游——未合并不 import；162 用例是 resolver 语料非 goal 语料）；#1279 resource governor（ADR-0182，goal 只是预算作用域，无重叠）。
- **ADR 占用**：0180 三方争用（#1274/#1275/#1277）、0181（#1276）、0182 双方争用（#1278/#1279）→ **本任务用 0183**。
- **#1271/#1272 review**：行内 comments 为空；issue comments 遗留——#1271：svg2pdf 类型声明（已修）、migration gate dialect 混用、4 项挂起 P1/P2（JWT TTL、dual visual-judge、ADR status、README Next version，归 #1270 线）；#1272：ruff F401/bandit B608（commit 61c995b8 已修）、迁移漂移闸警示（本任务 goal 契约不入库，无迁移面）、6 源 offline verified=false（ADS 线自认限制）。
- **子能力覆盖结论**：用户级 Goal Contract / goal 级 evidence registry / goal 完成度 evaluator / multi-goal partial / stop-replan 决策信号 / counterfactual tests / goal acceptance corpus —— **全部空白**；可复用地基（#1276 GoalRequirements、#1278 replay_procedure、#1277 patch_plan、#1275 SituationDelta）均未合并，本任务只做命名/协议对齐，不做 import 依赖。
- **冲突面**：`app/lib/harness/**`、`app/services/planning/**` 零触碰（安全）；本任务接线文件 `completion/pipeline.py`、`runtime_state_machine.py` 与 #1273 相交（各 <20 行 additive，rebase 时以 #1273 语义基线为准）。

## 附录 A：证据源清单（G2 输入）

| 证据 | 来源 | key/path |
|---|---|---|
| 工具执行回执 | chapter 行状态 + harness telemetry | `data_requirements[].status` / `analysis_steps[].status`（complete/failed/pending/unavailable） |
| 数据资格 | `data_qualification.qualify_workflow_data_roles` 产物 | chapter `workflow_contract.roles[].status`（bound/external/blocked） |
| 分析 artifact | artifact registry / 行 ref | `analysis_steps[].depends_on`、artifact_registry records |
| 方法义务 | workflow contract | chapter `workflow_contract.obligations[]`、`method_blockers`、`data_blockers` |
| 产品完成 | map_product 块 | `status/product_verdict/final_map_status/task_complete/completion_dimensions` |
| 渲染观察 | render_observation | `render_status`、observation layers mounted/visible |
| 视觉裁判 | visual_evidence | map_product/cartography 检查行 `VISUAL_*`（evidence_class=visual） |
| 导出交付 | chapter `exports`（planner/intent 需求面）+ finalize/export 证据 | 需求 vs 实际 |
| 用户覆盖 | display ack / user-wins 隐藏层 | `disclosures: layer_hidden_by_user:*`、display_confirmation |
