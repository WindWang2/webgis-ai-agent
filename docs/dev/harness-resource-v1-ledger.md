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

## M7 — Review（Subagent B 2/2）

- [ ] 四轴 review 报告（architecture / reliability / performance / security）。
- [ ] P0/P1 修复 + 证据。

## M8 — 交付

- [x] ADR-0182 落盘。
- [ ] final regression + push + PR（不等待 CI、不 merge）。
