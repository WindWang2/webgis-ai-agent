# Harness Resource Governor V1 — 执行台账（Ledger）

> 滚动更新；每完成一个可验证单元即追加证据。里程碑 M0-M8。

## M0 — 勘察与基线（2026-09-14）

- `git fetch --all --prune`；`origin/master = 580b33e9`。
- Open PR 对账：#1270/#1273/#1274/#1275 open；#1271/#1272 已 merge（master）。
- worktree `../webgis-wt-resource-governor-v1` + branch `harness/resource-cost-governor-v1` 自 `origin/master` 创建。
- Subagent A（1/2）完成 Phase 0 全仓勘察 → `docs/dev/harness-resource-v1-recon.md`
  （预算盘点矩阵 30+ 子系统、缺口 G1-G12、集成缝、并行线热区、重试乘数表）。
- `docs/dev/harness-resource-v1-decisions.md`（D0-D14）落盘。
- ADR 领号：**ADR-0182**（0180 归 #1274/#1275 标题、0181 归 capability-graph 分支）。

## M1 — 契约与基座 ✅

- `app/services/governor/contract.py`：SCHEMA_VERSION=rg.v1；Certainty×DimValue range；
  CONSERVATIVE_FLOORS（unknown≠0 的落地）；六类核心类型。
- `config.py`（GOVERNOR_MODE/GovernorConfig env 旋钮 + manifest 加载）+
  `config/governor_budgets.json`（global/session/goal/turn 四级 provisional 预算，
  source 字段记录证据来源）。
- `metrics.py`：封闭词表 Prometheus 面（无 session-id 标签）。
- 证据：tests/governor/test_contract.py + test_config.py（32 项）。

## M2 — 估算投影 ✅

- `render_budget.py`：确定性估工公式（render_formula.v1）+ export DPI 面积律 +
  complexity 封顶；单调性单测锁定。
- `estimation.py`：ToolCost 档位先验 + args 细化 + DF cost 窄投影（DfCostView）+
  LLM token 定价投影 + `sum_estimates`（计划聚合，短板 confidence）。
- 证据：test_estimation.py。

## M3 — 预算/背压/公平 ✅

- `session_budget.py`：SCOPE_CHAIN turn→goal→session→global；live vs cumulative
  双记账语义；BudgetViolation（provisional 标记）；`live_memory_total`（只按 session
  求和——修复了沿链记账 + global 求和的双重计数缺陷）。
- `fairness.py`：FairScheduler（虚拟起始时间加权公平 + aging + small bypass 预留槽 +
  消费衰减钳顶）；不变量自检 API。
- `backpressure.py`：六通道（heavy/raster/browser/export/external/llm）异步闸 +
  `_SessionGate`（semaphore + heavy Condition，**全部等待有 max_wait 上界**）+
  wait_for 超时/判给竞争窗口的槽位归还路径。
- 证据：test_backpressure_fairness.py。

## M4 — 准入/降级/协同 ✅

- `admission.py`：确定性策略链（硬预算 reject → 内存压力 defer/degrade →
  provisional 留痕 → provider open degrade/reject → SLO backlog 限幅 → 通道积压
  预测 defer → accept）。
- `degradation.py`：8 动作阶梯 + semantics 标注 + reason codes + 节省倍率。
- `context_link.py`：R8 ResourceAwarePlanHint 窄协议 + advise_plan（KDE 场景单测）；
  R12 record_context_report/context_priority_hints（duck-typed）。
- `health.py`：熔断只读视图（owner 故障 → unknown，fail-open）。
- 证据：test_admission_degradation.py。

## M5 — 取消/重试/门面/接线 ✅

- `retry_budget.py`：双层令牌 + 互斥（degrade/retry 不双发）+ 取消清零（对未记账
  会话建档即取消——review 修复）。
- `cancellation.py`：幂等取消事实表 + facade 释放钩子 + 延迟观测。
- `storage_pressure.py`：artifact_cache advisory 总量的只读 TTL 投影。
- `governor.py`：HarnessResourceGovernor 门面（admit_and_reserve/complete/
  cancel_session/close_session/snapshot；observe 模式决策留痕放行——修复了
  observe 放行从未生效的缺陷；fail-open）。
- `dispatch_adapter.py` + `tool_dispatch_service.py`（~15 行接线 + 懒构造 helper）+
  `context_assembler.py`（8 行只读 hook）。
- 拒绝 payload 对齐派发链错误族（`{"success": False, "error": ...}`）—— dedup 诚实
  释放、同参可重试（集成测试锁定）。
- 排队候补的会话取消（R9 扩展）：通道级 `cancel_session` 唤醒 + "cancelled" 信号 →
  facade REJECT(session_cancelled_while_queued)。
- 证据：test_facade_adapter.py + test_dispatch_integration.py。

## M6 — 测试矩阵 ✅

- `tests/governor/synthetic.py`：合成 workload（4 规模 × 6 域）+ FakeExecutor +
  run_mixed_corpus（p50/p95/fairness/peak 指标）。
- `test_chaos.py`：hung+cancel、满载不堵轻查、队列饱和 8 路升格、drain、重试风暴
  有界、provider 不可用、内存压力循环恢复、取消风暴互不阻塞、排队取消无幽灵唤醒、
  chaos 后零残留不变量。
- `test_multisession_stress.py`：1/4/8/16 session 矩阵（终态全覆盖/等待有界/零残留/
  吞吐上界）+ 公平基尼 < 0.4 + 饥饿指数有界。
- `scripts/perf/calibrate_governor.py`：校准 CLI；证据
  `docs/dev/harness-resource-v1-calibration.json`（degrade 24 / est-err mem 8.9% /
  wall 12.1% / n=72，2026-09-14 首轮）。
- 回归：受影响域 292 项全绿（error_sanitization、pi_bridge_lock、
  subagent_context_isolation、tool_error_classification 529/589、runtime_observability、
  round2_review、runtime_p2、cartography_turn_injection、runtime_chaos_engine、
  runtime_chaos_pi、remaining_zero_review）。
- 全 governor 套件：132 项绿（serial，--no-cov，单测最耗时 17s）。

## M7 — Review（Subagent B 2/2）✅

- 四轴 review 完成（read-only + async 探针实证）：Architecture PASS /
  Performance PASS / Security PASS / Reliability ISSUES（1×P0 + 3×P1）。
- **P0 修复**：observe 模式端到端——adapter 只看 `decision.allowed`（不含
  模式）导致 observe 下仍然拦截 + reservation/ticket 丢弃 → 槽位永久泄漏。
  adapter 现显式检查 enforce；新增 adapter 级 observe 测试
  （test_review_fixes.py::TestObserveEndToEnd）。
- **P1 修复**：complete() 原子认领 live 表（取消后迟到 complete /
  双 complete 不再二次归还槽位）；_ChannelGate.acquire 补 BaseException
  清理（调用方任务取消不再泄漏排队槽位）；admit_and_reserve acquire 之后的
  异常先归还 ledger/ticket 再 fail-open（CancelledError 穿透不吞）。
- **P2 修复**：pop_next_grantable pop-and-stash（队首 heavy 不再挡已排队
  small 的 bypass 槽）；global 作用域 cumulative 违规强制 provisional
  （拆掉 ratchet 翻转地雷）；projection_violations 纯读不再 setdefault；
  会话状态有界驱逐（ledger 512/gates 512/retry 2048 + last_activity）；
  测试诚实性——fairness/stress 改为真实通道争用场景，校准误差标注
  "synthetic-jitter smoke only"。
- **P3**：metrics 词表文档对齐实际值域；classify_tool 保留名字优先的
  权衡写入 docstring（156 存量工具 cost 全为 light，cost 优先会整体
  关闭通道记账）。
- P0/P1 全部修复，P2 修复 5 项，测试 138 项全绿（含 6 项新回归）。

## M8 — 交付

- [x] ADR-0182 落盘。
- [x] final regression：governor 138 项 + 受影响域回归（error_sanitization/
  pi_bridge_lock/tool_error_classification×2/subagent_context_isolation）183 项。
- [ ] push + PR（不等待 CI、不 merge）。
