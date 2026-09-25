# F08 — Workflow Resource Scheduler：独立 Review Gate 记录

- Reviewer：Subagent C（独立深审，与实现者分离；只读 + 实证复现脚本）
- 范围：`9e1ad229..HEAD`（4 commits）；基线 origin/master = `9e1ad229`
- 初审结论：**NEEDS_FIX — P0×1 / P1×2 / P2×7**
- 处置：全部 P0/P1 修复 + P2 除一项外全部修复（逐项见下）；修复后
  F08 套件 90 用例全绿，邻域回归通过（存量 flaky 与本分支无关，见附录）。

## P0/P1 处置（全部修复）

| # | 发现（file:line 为初审时点） | 处置 |
| --- | --- | --- |
| **P0-1** | NODE_NOT_EXECUTABLE 提前 return 及「begin 之后、invoke 守护之前」的任何异常/`close` 实参求值失败都会**泄漏 governor 预留**（reviewer 以最小复现脚本实证 admits=1/completed=0/LEAKED=1） | `_run_node_claimed` 引入 `_release_budget` helper：close 实参求值失败兜底归还；选型链整体包 try/except BaseException → 归还后 re-raise；NODE_NOT_EXECUTABLE 先归还再落终态。回归测试 `test_node_not_executable_releases_budget` 锁定 complete==1 |
| **P1-1** | 计划准入门不受 `GIS_WORKFLOW_GOVERNOR=0` 门控，与 ADR-0214 D2/D3「整体直通」矛盾 | `_plan_admission_gate` 入口检查 `workflow_surface_enabled()`；回归测试 `test_kill_switch_disables_plan_gate` |
| **P1-2** | provisional 估算先验经 `min()` 变成 durable worker 硬 kill 信号，可覆盖用户显式 `node_timeout_s`（估算错误被升格为执行失败） | 语义回归 ADR 文本：显式 `node_timeout_s` 恒胜；未配置时才由估算 wall 上界派生（再与 run 剩余取紧者）。回归测试 `test_explicit_timeout_not_cut_by_estimate` |

## P2 处置

| # | 发现 | 处置 |
| --- | --- | --- |
| P2-1 | 波首 OPTIONAL 吞掉 boundary → 同波 main 与上一波 run 合并，波间串行被 max 压平 | boundary 顺延至波内首个主路径节点 + 测试（聚合 wall 形状验证） |
| P2-2 | `global_memory_pressure` 等 DEGRADE 被分类为不可重试（瞬态被终态判死） | 压力类原因（memory_pressure / slo_breach_backlog / under_pressure）→ `RESOURCE_EXHAUSTED`（retryable）；硬预算 `hard_*` / `retry_budget` 保持确定性。分类表测试更新 |
| P2-3 | `_await_job` 注释与 deadline 实际优先级相反 | 注释修正（op_node 声明 > plan budget > driver advisory fallback） |
| P2-4 | `_ROWS_THROUGHPUT` 是驻留 workflow_runtime 的第二张先验 | 迁入 `governor.estimation.WORKFLOW_ROWS_THROUGHPUT`（单一先验真相） |
| P2-5 | plan gate 在事件循环上做 manifest 文件 IO | `workflow_plan_limits` 经 `asyncio.to_thread` 卸载 |
| P2-6 | 测试盲区：(a) 无 not-executable×governor 用例（P0-1 漏网原因）(b) kill-switch×plan gate 无用例（P1-1 漏网原因）(c) manifest fail-open 测试 green-by-construction | (a)(b)(c) 全部补齐；manifest 测试改为真实缺失文件注入（monkeypatch `GOVERNOR_MANIFEST_PATH`） |
| P2-7 | f08 driver 测试把真实 governor 单例带进共享测试进程（跨测试状态/计时扰动） | `tests/unit/workflow_runtime/conftest.py` autouse 关闭 governor 面；f08 两个套件显式 opt-in（monkeypatch env=1） |

**未采纳（1 项，记录理由）**：reviewer 建议为「reuse 命中不做 admission」增加显式断言。reuse 裁决在 `begin()` 之前由代码顺序保证（driver.py 复用检查先于估算/准入），补该测试需搭建完整 reuse index fixture，收益/成本比低；已在本文档记录该顺序不变式，供后续 refactor 时守护。

## 修复后验证

- F08 套件：`tests/unit/workflow_runtime/test_f08_*.py` + `tests/governor/test_export_budget.py` = **90 passed, 0 failed**。
- 邻域回归：`tests/unit/workflow_runtime/` + `tests/governor/` 全量（串行 `-n 0`）= 426+ passed；
  失败仅剩存量 flaky（见附录）。
- `ruff check app/ tests/`：全绿。

## 附录：存量失败/flaky 区分（干净基线双向复现）

| 测试 | 现象 | 基线证据 |
| --- | --- | --- |
| `test_v6_clone_nested.py`（3 用例） | lease-lost → 实例 running 不收敛；时序敏感，失败数 1–3 浮动 | `git stash` 干净基线复现同样失败（3/3）；本分支与基线失败面一致 |
| `tests/governor/test_backpressure_fairness.py::test_weighted_fairness_order` | 加权公平排序断言间歇失败 | 干净基线全套 3 跑 1 败；本分支 2 跑 1 败；单文件运行两侧均稳定通过 —— 负载敏感时序 flaky，与本分支改动面无因果 |

## 遗留风险（Out of Scope / follow-up）

1. worker 侧（celery worker 进程）尚不消费 `resource_envelope` —— 通道已打通
   （dispatch_node task_kwargs），消费面属方向 13/14 render/export runtime。
2. `estimate_bridge`/planner 面（#1484）与 workflow 节点估算共享先验表，但
   `node.resources` 申报键的普及需要编译面（方向 5）在产出 DAG 时写入
   —— 当前缺省路径按 kind 保守档兜底，诚实不精确。
3. `test_v6_cluster_dispatch` 的 `importlib.reload(DP)` 存量隔离缺陷（异常类
   身份分裂）影响任意在其后运行且直接 `from dispatch import NoCapableWorker`
   的测试 —— 本分支测试已改用模块属性引用规避，但根修（移除 reload）属
   测试基建存量问题，未在本方向扩张处理。
