# F08 — Workflow Resource Scheduler Convergence — Design

- Date: 2026-09-26（实现） / 2026-09-27（本文为合并后补记的 design，G06 文档债补齐）
- ADR: `docs/adr/0214-workflow-resource-scheduler-convergence.md`
- Recon: `docs/dev/f08-workflow-resource-scheduler-recon.md`；Review: `docs/dev/f08-workflow-resource-scheduler-review.md`
- 实现基线: `origin/master @ 9e1ad229`；Merge: PR #1499（`15193a2c`）；前置: #1484（unified cost/resource/planning，ADR-0213）
- 状态: 已合并（merged）。本文全部内容以合并后代码为事实源，可 rg 核对。

## 目标与红线

#1484 建成了 governor 的工具面管线（rg.v1 契约/先验/plan_aggregation/backpressure/
calibration），但 **workflow_runtime 与 governor 零接线**：节点执行不过任何
admission/backpressure/预算记账，节点无 typed ResourceEstimate，worker 槽位只声明
不记账，「worker 在但槽满」与 `NO_CAPABLE_WORKER` 不可区分。红线：**rg.v1 是唯一
数值真相** —— workflow 侧只做投影桥，禁止第二份先验表；governor 六通道容量语义只
消费不重定义。

## Module map

| 模块 | 状态 | 内容 |
|---|---|---|
| `app/services/workflow_runtime/estimate.py` | **new** | 节点估算投影桥：`resource_estimate_for_workflow_node`（:204，唯一入口；声明 `resources.profile`/`node_declared_profile`/`node_declared_cost` 优先，`_tool_backed_estimate` 走 `governor.estimation.estimate_for_tool`，`_kind_fallback_estimate` 按 kind 保守档兜底并诚实披露）；`execution_priority_for_node`（调度优先级投影） |
| `app/services/workflow_runtime/governor_link.py` | **new** | 节点执行 governor 面：`workflow_surface_enabled()`（kill-switch `GIS_WORKFLOW_GOVERNOR=0` 整体直通，:56）；admit→execute→complete 环绕 + 一切 governor 异常 fail-open；压力类原因（memory_pressure/slo_breach_backlog/under_pressure）归类 `RESOURCE_EXHAUSTED`（可退避重试），硬预算 `hard_*`/`retry_budget` 保持确定性（:247）；complete → `get_calibration_store().record_usage("workflow:{kind}:{capability}")`（:136，校准只观测） |
| `app/services/workflow_runtime/plan_feasibility.py` | **new** | plan 级 feasibility：`dag_waves`（:86）→ `dag_plan_nodes`（:130）把 DAG 波次结构投影成 PlanNode 序列 → `evaluate_plan`（:180，消费 `governor.plan_aggregation.aggregate_plan`/`budget_violations`，manifest scope=`workflow`）；`workflow_plan_limits`（:224，经 `asyncio.to_thread` 卸载文件 IO）；`plan_admission_mode`（:46，provisional observe 默认，enforce 才拒） |
| `app/services/governor/export_budget.py` | **new** | `ExportWork`（:54）导出节点资源契约 + export 预算投影（#1484 Out-of-Scope 第 2 项的收敛面） |
| `app/services/workflow_runtime/driver.py` | extended | `_run_node_claimed` 统一收口：复用检查先于估算/准入（reuse 命中不做 admission 的顺序不变式）；`_plan_admission_gate`（:160，入口检查 `workflow_surface_enabled()`）；估算随 durable job 走既有 `resource_envelope`，补喂 `run_id/node_attempt/node_deadline_s`（由 estimate wall 上界派生，显式 `node_timeout_s` 恒胜）；`_release_budget`（:747）兜底归还预留；`NODE_NOT_EXECUTABLE` 先归还再落终态（:845） |
| `app/services/workflow_runtime/dispatch.py` | extended | worker 容量二值化：`WorkerCapacityExhausted`（:98，「能跑但此刻满载」，瞬时，走既有重试退避门）对照 `NoCapableWorker`（确定性不可重试）；`profile_capacity`（:118，worker 行 → (declared_slots, reported_in_flight) 有界求和，行形状异常 fail-open (0,0)）；`choose_dispatch`（:148）分派 `NO_CAPABLE_WORKER`（:408）vs `RESOURCE_EXHAUSTED`（:414） |
| `app/services/workflow_runtime/contracts.py` / `retry.py` | extended | 词表加法：资源面状态/reason；`NON_RETRYABLE` 维持 `NO_CAPABLE_WORKER`/`RESOURCE_BUDGET_EXCEEDED`/`CANCELLED` 不可重试，`RESOURCE_EXHAUSTED` 可重试 |
| `app/services/governor/estimation.py` | extended | `WORKFLOW_ROWS_THROUGHPUT`（:54）迁入 —— review P2-4 消灭驻留 workflow_runtime 的第二张先验表 |
| `tests/unit/workflow_runtime/conftest.py` | **new** | autouse 关闭 governor 面（f08 两个套件显式 opt-in monkeypatch env=1），防真实 governor 单例泄漏进共享测试进程（review P2-7） |

## 规范语义（normative）

1. **单一数值真相**：节点估算一律经 `resource_estimate_for_workflow_node` 投影
   governor 既有先验/`render_budget`/`export_budget`；parity 不变式 = 同工具同参数
   → dims+resource_class 全等（`test_f08_node_estimate.py` 锁定）。
2. **节点执行统一过 governor**：admit→execute→complete→calibration 在
   `_run_node_claimed` 内收口；kill-switch `GIS_WORKFLOW_GOVERNOR=0` 时估算/准入/
   记账全直通（含 `_plan_admission_gate`，review P1-1）；governor 任何异常 fail-open
   —— 观测与准入失败绝不阻断业务执行。
3. **预留不泄漏**：begin 之后、invoke 守护之前的任何异常/close 实参求值失败/
   `NODE_NOT_EXECUTABLE` 一律先 `_release_budget` 再落终态（review P0-1，
   `test_f08_driver_budget_lifecycle.py::test_node_not_executable_releases_budget`
   锁定 complete==1）。
4. **超时优先级**：显式 `node_timeout_s` 恒胜；未配置时才由估算 wall 上界派生、再与
   run 剩余取紧者（review P1-2：估算错误不得升格为执行失败）。
5. **容量二值化**：无合格 worker → `NO_CAPABLE_WORKER`（不可重试）；有 worker 但
   `in_flight >= slots` → `RESOURCE_EXHAUSTED`（可退避重试）；registry 未上报 load
   时 fail-open 放行（队列自身能吸收瞬时排队）。
6. **校准只观测**：节点 actual 回填 `CalibrationStore`（键 `workflow:{kind}:{capability}`），
   先验更新仍只能走离线 `suggest_priors` + 人工 PR，生产零自修改。

## 测试计划（F08 套件 90 passed）

| 文件 | 覆盖 |
|---|---|
| `tests/unit/workflow_runtime/test_f08_node_estimate.py` | 估算桥 parity、声明覆盖、kind 兜底、优先级投影 |
| `tests/unit/workflow_runtime/test_f08_governor_link.py` | admit/complete/fail-open、压力类→RESOURCE_EXHAUSTED 分类表、kill-switch |
| `tests/unit/workflow_runtime/test_f08_driver_budget_lifecycle.py` | 预留生命周期（含 P0-1 回归 `test_node_not_executable_releases_budget`、P1-1 回归 `test_kill_switch_disables_plan_gate`、P1-2 回归 `test_explicit_timeout_not_cut_by_estimate`） |
| `tests/unit/workflow_runtime/test_f08_plan_feasibility.py` | DAG 波次投影、budget_violations 裁决、observe/enforce、真实缺失 manifest 注入 |
| `tests/unit/workflow_runtime/test_f08_worker_capacity.py` | 二值化分界、fail-open、有界求和 |
| `tests/unit/workflow_runtime/test_f08_resource_sim.py` | 资源模拟场景 |
| `tests/governor/test_export_budget.py` | ExportWork/export 预算投影 |

## Review 结论与遗留

初审 NEEDS_FIX（P0×1/P1×2/P2×7）→ 除一项外全部修复（P2 未采纳项与理由记录在
review 文档）。遗留（Out of Scope）：worker 进程侧消费 `resource_envelope`（归
render/export runtime 方向）；`node.resources` 申报键普及需编译面在产出 DAG 时
写入（当前缺省按 kind 保守档兜底）；`test_v6_cluster_dispatch` 的
`importlib.reload` 存量隔离缺陷属测试基建，不在本方向扩张。
