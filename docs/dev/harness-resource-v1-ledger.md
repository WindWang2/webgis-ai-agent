# Harness Resource Governor V1 — 执行台账（Ledger）

> 滚动更新；每完成一个可验证单元即追加一行证据。里程碑编号 M0-M8。

## M0 — 勘察与基线（2026-09-14）

- `git fetch --all --prune`；`origin/master = 580b33e9`。
- Open PR 对账：#1270/#1273/#1274/#1275 open；#1271/#1272 已 merge（master）。
- worktree `../webgis-wt-resource-governor-v1` + branch `harness/resource-cost-governor-v1` 自 `origin/master` 创建。
- Subagent A（1/2）完成 Phase 0 全仓勘察 → `docs/dev/harness-resource-v1-recon.md`
  （预算盘点矩阵 30+ 子系统、缺口 G1-G12、集成缝、并行线热区、重试乘数表）。
- `docs/dev/harness-resource-v1-decisions.md`（D0-D14）落盘。
- ADR 领号：**ADR-0182**（0180 归 #1274/#1275 标题、0181 归 capability-graph 分支）。

## M1 — 契约与基座（R1/R16 基座/配置）

- [ ] `app/services/governor/contract.py`：schema_version=rg.v1，Certainty×Range，六类核心类型。
- [ ] `app/services/governor/config.py` + `config/governor_budgets.json` manifest。
- [ ] `app/services/governor/metrics.py`：封闭词表 Prometheus 面。
- 单测：contract 序列化/unknown≠0/metrics 注册。

## M2 — 估算投影（R2/R13）

- [ ] `estimation.py`：工具类 → ResourceEstimate（DF cost model 消费、range 语义）。
- [ ] `render_budget.py`：map render work 纯函数估工。
- 单测：range 单调、confidence 传播、render 估工单调性。

## M3 — 预算与背压（R4/R5/R6）

- [ ] `session_budget.py`：Session/Goal/Turn 三级账本 + provisional 预算（来自 manifest）。
- [ ] `backpressure.py`：session/subsystem/global 三层 gate + heavy/light 通道 + max_wait。
- [ ] `fairness.py`：加权公平 + aging + small-job bypass + heavy cap。
- 单测：饥饿防护、bypass 不被堵、aging 生效、释放无泄漏。

## M4 — 准入与降级（R3/R7/R8/R11/R12）

- [ ] `admission.py`：五值决策 + enforce/observe 模式 + fail-open。
- [ ] `degradation.py`：DegradePlan + semantics 标注。
- [ ] `planning_hints.py`：ResourceAwarePlanHint 窄协议。
- [ ] `health.py`：breaker/provider 只读视图。
- [ ] `context_link.py`：context_budget 消费 + allocation hints。
- 单测：决策矩阵、unknown 保守档、degrade 语义标注完整性。

## M5 — 取消/重试/治理门面 + 接线（R9/R10/R15）

- [ ] `cancellation.py`、`retry_budget.py`、`storage_pressure.py`。
- [ ] `governor.py`：HarnessResourceGovernor facade。
- [ ] `dispatch_adapter.py` + `tool_dispatch_service.py` 最小 diff 接线。
- [ ] `context_assembler.py` 只读记账接线。
- 单测：取消释放/重试停摆/stale 不提交。

## M6 — 测试矩阵（R17/R18/R19）

- [ ] synthetic workload 生成器 + 校准脚本 + provisional 预算产出。
- [ ] chaos 测试套（死锁/雪崩/泄漏/取消）。
- [ ] 多 session 1/4/8/16 压测（fake executor）+ 指标断言。
- [ ] governor integration + 受影响域 regression（tool dispatch/chat/pi bridge 面）。

## M7 — Review（Subagent B 2/2）+ P0/P1 修复

- [ ] 四轴 review：architecture / reliability / performance / security。
- [ ] P0/P1 全修 + 修复证据。

## M8 — 交付

- [ ] ADR-0182。
- [ ] final regression + 提交 + push + PR（不等待 CI、不 merge）。
