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

## M2 — （待填）

---

