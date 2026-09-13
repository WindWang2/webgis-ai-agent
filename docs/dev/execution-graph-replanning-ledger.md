# Execution Graph + Incremental Replanning — Ledger（方向 5）

逐里程碑交付账本。每项：目标 / 改动文件 / 新改契约 / 测试 / 证据 / 兼容 / 回滚 / 未解决项。

基线：`origin/master @ 580b33e9` | 分支：`harness/execution-graph-incremental-replan-v1`

---

## M0 — Phase 0 勘察与文档（本次提交）

- **目标**：执行时基线复核、防重复施工对账、架构收敛裁决落档。
- **改动文件**：
  - `docs/dev/execution-graph-replanning-recon.md`（新建：before 调用链 + 重叠矩阵 + 缺口映射）
  - `docs/dev/execution-graph-replanning-decisions.md`（新建：D1-D8）
  - `docs/dev/execution-graph-replanning-ledger.md`（本文件）
  - `docs/dev/execution-graph-recon-subagent-a.md`（Subagent A 原始勘察笔记）
- **新/改契约**：无生产代码改动。
- **测试**：无（勘察阶段）。
- **证据**：一手精读 9 个核心文件（session_plan / plan_graph / runtime_bridge / workflow_instance 结构 / workflow_runtime{service,driver,contracts,recompute,reuse,fingerprints,hooks} / followup / tool seam）+ `gh` 对账 8 个 open PR。
- **兼容**：纯文档。
- **回滚**：删文件即回滚。
- **未解决项**：Subagent A 报告的 5 个待确认项（见 recon §7）。

## M1 — 意图差异最小失效（E5 计划侧 + E6 V5 接线）

- **目标**：follow-up 情境变更从「全 void / 全量归档」升级为「携带 + 最小失效」，计划侧与 V5 执行侧同源贯通。
- **改动文件**：
  - `app/services/gis_harness/intent_diff.py`（新增，纯函数：fine_dims 门控 / 行语义签名 / 依赖闭包携带 / lost 披露）
  - `app/services/session_plan.py`（`_intent_facts` + `_seed_progress` + `apply_intent_diff_to_chapter` + `intent_replan_enabled`；intent 分支返回 `(events, facts)`；锁外 `record_intent_changes_safe` 调用）
  - `app/services/workflow_runtime/service.py`（`apply_intent_facts`：lost→节点级 PendingChange→`apply_changes`（复用 ChangeApplier）；carried→`_chat_complete_node` 拓扑序携带；`_chat_complete_node` 起点状态诚实分类：PENDING/BLOCKED/STALE/READY）
  - `app/services/workflow_runtime/hooks.py`（`record_intent_changes_safe`，fail-open）
- **新/改契约**：`_apply_tool_result_unlocked` 返回 tuple（私有）；`GIS_INTENT_DIFF_REPLAN` 开关（默认开，=0 回 master 行为）；chat 通道新增 STALE 重入队（CHAT_RECOMPUTE）与 READY 直推两种完成起点。
- **测试**：`tests/unit/gis_harness/test_intent_diff.py`（13）、`tests/unit/workflow_runtime/test_intent_facts_service.py`（7）、`tests/test_intent_replan_envelope.py`（3，生产 seam）；回归 `tests/unit/workflow_runtime/` 152 全过、`tests/test_pi_session_plan_host.py` 5 全过。
- **证据**：场景 2/3 语义（scope 收缩→数据链失效；subject 换→边界携带）被 envelope 级测试钉住；kill-switch 测试钉住零回归面。
- **兼容**：裁决失败/开关关停 → 现状全量语义；facts 通道 fail-open，V5 关停（GIS_WORKFLOW_RUNTIME=0）时零行为。
- **回滚**：`GIS_INTENT_DIFF_REPLAN=0`；或 revert 单 commit。
- **未解决项**：意图→role 的映射在 V5 侧依赖 recipe wf_profile（capability_hint），无 hint 的 role 节点靠 cap 节点目标兜底。

## M2 — side_effect 执行纪律（E3；commit dd567ec9）

- **目标**：节点副作用分类 + at-least-once / at-most-once 执行语义。
- **改动文件**：`workflow_v4/typed_dag.py`（SIDE_EFFECT_CLASSES 词表、kind 派生、bounded dict 字段、校验违规）；`workflow_runtime/driver.py`（`_node_side_effect` helper；destructive 不自动重试→FAILED+journal 证据；STALE 不自动重入队）。
- **契约**：`side_effect` additive 字段（旧包按 kind 诚实降级）；`retry_failed_nodes` 显式指令是 destructive 唯一重驱通道。
- **测试**：`test_side_effect_discipline.py`（5）；回归 170（workflow_runtime）+ 28（typed_dag/bridge）+ 19（cross-tenant/e2e v6）全过。
- **回滚**：字段缺省 pure → 全部现状行为。

## M3 — workflow_graph SSE 事件族（E8；commit 27829d71）

- **目标**：图进度/replanned-diff 事件，复用既有 SSE 通道。
- **改动文件**：`workflow_runtime/graph_events.py`（新；`replanned`/`node_states` payload，≤8 项/事件）；`hooks.py`（返回 V5 摘要）；`session_plan.py`（facts → `intent_graph_event` 追加进事件列表）。
- **契约**：`WORKFLOW_GRAPH_EVENT="workflow_graph"`（additive，非 CanonicalPlan 禁用名）；事件经 `apply_tool_result` 事件列表 → 既有 `events_to_sse`/per-toolCall SSE 缓存，**零 bridge 改动、零新通道**。
- **测试**：`test_graph_events.py`（3）+ envelope 级 SSE 断言（`event: workflow_graph` 在、`plan_ready` 不在）。
- **回滚**：事件追加 try/except 包裹，失败只少一条事件。

## M4 — 可靠性场景硬化（E4/E7；commit 1cf20663）

- **测试**：`test_reliability_scenarios.py`（5）：场景 7（失败→显式重排→resume，上游零重复副作用）、场景 10（重复 apply 幂等）、进程重启（孤儿租约复位→新 driver 结算）、E7 跨租户复用必 miss、场景 9（失败行→最小重算种子）。回归 workflow_runtime 全目录 165 全过。

## M5 — Graph Replan 语料（E9；commit 14e96708）

- **测试**：`test_graph_replan_corpus.py`：10 情境 × 5 变体 = 50 场景（对应任务书 10 必做场景）；每场景零错误携带/保守失效断言。
- **指标**：全语料平均节点节省 **0.650**；保守类（scope/time/task 重塑）0.125（等价现状全失效——正确性优先的证明）；style/output/resubmit/pin/resume 类 **零科学重算**（savings 1.0）。
- **回滚**：纯测试文件。

## M6 — ADR-0184 + 文档收口（1bddafd6）

- `docs/adr/0184-execution-graph-incremental-replan.md`（D1-D8 决策全录）；
- ledger/decisions 更新；recon 与实现对账一致。

## M7 — 独立终审 + P2 修复（7b5eeeff）

- **终审**：Subagent B 四轴（Spec/Architecture/Reliability/Perf-Security）——
  **PR-go，无 P0/P1**，8 个 P2（全文：`docs/dev/execution-graph-review-subagent-b.md`）。
- **已修 P2**：2（节点变更截断）、3（STALE 洗白守卫）、4（移除死事件面）、
  5（语料情境替换 + 诚实指标）、6（SIDE_EFFECT_NO_AUTO_RETRY 事件种）、
  7（参数递归深度封顶）、1（文档措辞）。
- **不修 P2**：8（coarse 规则三处并存 → runtime_bridge 契约冲突面，记 follow-up）。
- **复跑**：受影响面 215 全过（workflow_runtime + gis_harness + host seam）。
- **交付状态**：`git diff origin/master...HEAD` 21 文件全部任务相关，无夹带。

---

