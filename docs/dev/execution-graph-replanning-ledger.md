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

## M1 — （待填）

## M2 — （待填）

---
