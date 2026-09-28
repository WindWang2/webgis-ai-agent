# ADR-0216: Subagent Delegation Lease Protocol（可取消、可追因、fail-closed 的委派基座）

- 状态：Proposed（随 `zcode/h07-subagent-delegation-swarm-protocol-*` 分支评审）
- 日期：2026-09-29
- 关联：ADR-0187（Specialist Swarm 总控）、ADR-0104（Subagent 并行委派）、
  ADR-0101（角色档与层级预算）、ADR-0134 D7（Harness 程序化委派）、
  ADR-0184 D6（副作用词表）

## 背景

master 上委派执行存在三个并存语义，且没有一个拥有完整的请求生命周期：

1. `app/services/subagent.py`（ADR-0104，活跃 LLM 工具路径
   `spawn_subagent`）：`SubagentResult` 扁平 refs；`run_parallel` 信号量
   2；**单发路径无任何每会话并发上限**；
2. `app/services/agent_swarm/`（ADR-0187，`GIS_SWARM_ORCHESTRATOR` 门）：
   `SpecialistAssignment`/`SubagentReceipt` + `SwarmConcurrencyGovernor`
   （全局 ≤3），但任务生命周期只有图节点词表，无请求级因果台账；
3. `app/services/gis_harness/delegation.py`（ADR-0134 D7，实验性）：
   `DelegationSpec`/`DelegationRecord` 台账，docstring 自认缺
   「并发去重、预算扣减、取消收尾」。

已登记的实现缺陷（PR #1529 body + pinned 测试
`test_governor_acquire_failure_leaves_task_nonterminal`）：
`SwarmOrchestrator._launcher` 的 acquire 相位（READY→RUNNING 之前）抛
非取消异常时无任何兜底 —— receipt 丢失、任务卡 READY、集群静默判
SUCCEEDED（fail-open）。同时 assignment 构造位于任何 try 之外，同一缺口。

其余缺口：迟到/重复子结果无 generation 纪律（取消后返回的结果无人
拦截）；parent/child 因果 id 散落各处，无有界台账可审计。

## 决策

### D1 — 单一委派生命周期基座：`agent_swarm/delegation.py`（叶子模块）

新模块只依赖 stdlib + pydantic + `delegation_contracts`，提供：

- **`DelegationPhase` 状态机**（请求级，非图节点级 —— ADR-0187 D2 的
  「不新造 DAG IR」纪律不变）：`CREATED→ACQUIRING→RUNNING→终态`
  （succeeded/degraded/failed/cancelled/expired）；ACQUIRING 可直达全部
  终态（acquire 崩溃/取消/排队过期不经过 RUNNING）。非法转移 fail-loud。
- **`DelegationRequest`**：委派唯一合法启动面。结构上**没有 payload
  字段**（`extra="forbid"`）—— 秘密/大对象没有合法入口；上下文只有
  `ref:` 提货券（validator fail-closed）；全部字段有界。
- **`DelegationLease`**（含 `generation` 单调代际）+ **`DelegationOutcome`**
  （终态 + `failure_reason` 词表 + refs-only 产物 + verdict + 因果 ids）。
- **`FairSlotScheduler`**：全局上限 + heavy 类上限 + 每会话上限 + FIFO
  等待队列（类感知唤醒：队头不可就绪时跳过，不队头阻塞）+ 有界背压
  （队列满诚实拒绝 `queue_full`）+ 排队 deadline（过期 = EXPIRED，不是
  无限 READY）。resource_class 标签是 H06 resource envelope 的预留接缝。
- **`DelegationLedger`**：每 run 有界台账（64 委派 FIFO 出账），唯一
  合并点 `offer` 做 generation fencing：首终态 accepted；同代再投
  duplicate；旧代际投递 late（隔离区有界披露，绝不覆盖已结算事实）。
- **`DelegationGateway.execute(request, runner)`**：唯一执行语义，绝不
  raise 业务异常 —— acquire/run/verify 任一异常折算携带真实原因的诚实
  终态；`CancelledError` 落账后原样上抛（asyncio 纪律）；取消传播到
  runner task；结果先 `verify_outcome`（succeeded 空 summary → rejected；
  expected_outputs 未满足 → degraded；越权 capability → rejected）再
  offer（缺证据不得当成功）。
- **`FakeSubagentRunner`**：确定性离线 runner（succeed/crash/hang/sleep/
  park-late 脚本化），协议测试零 LLM/DB/网络。

### D2 — 生产接线（三条既有路径，不建第二个框架）

- **Swarm 总控**：`_launcher` 全量兜底重构 —— assignment 构造入 try、
  acquire 非 cancel 异常折算 FAILED 崩溃券、acquire 等待受
  `task.timeout_s` 约束（排队过期诚实 FAILED）、settle-once 守卫。
  每 run 挂 `DelegationLedger`（causal ids：run_id/task_id/assignment_id/
  generation），`SwarmExecutionStatus.delegation_snapshot`（additive）披露。
  图转移红线保持：READY→FAILED 非法，按 DEGRADED 先例走两步合法转移，
  真实失败语义由 receipt error 携带。集群并发仍由 SwarmConcurrencyGovernor
  承载（不双闸）。
- **Harness 程序化委派**（`gis_harness/delegation.py::delegate`）：执行体
  经共享 `DelegationGateway`（`dispatcher` 注入 seam 不变）；attempt
  作用域 delegation_id（`<base>-aN`）使旧代际结果被 fencing 隔离；
  degraded receipt 诚实记 failed（不折算 completed）。台账 schema
  `delegations.v1` 不变。
- **`spawn_subagent` 单发路径**：经
  `delegation_adapters.run_single_delegation`（`SubagentDispatcherRunner`
  包装 legacy dispatcher，内部预算/角色/取消语义零改动）；每会话并发
  租约激活（env `GIS_DELEGATION_SESSION_CONCURRENCY`，默认 4 —— 此前该
  路径无上限）；结果 dict 逐字节同形（因果 id 增量进 `lineage` 自由
  字段）。`run_parallel` 不动（既有信号量语义保留）。

### D3 — 明确不做

- 不建第二个 Agent Framework；swarm 是「主 Agent 之上的委派织物」
  （ADR-0187 D1）不变。
- 不做跨进程分布式 lease（进程内有界对象，与 swarm v1 同纪律）。
- 不替换 legacy `SubagentDispatcher` 内部（823 行预算/取消语义原样）。
- 不以提高并发为目标：全局/会话上限默认保守，正确性与隔离优先。

## 后果

- **fail-open 清零**：acquire/run/merge 任一阶段异常都有诚实终态；
  PR #1529 登记的缺口清偿，pinned 测试按其 docstring 承诺翻转。
- **可追因**：parent/child 因果链（delegation_id/generation/lease_id/
  parent_turn/run）+ 有界 phase 历史 + 隔离区计数，`snapshot()` 一跳可查。
- **可验证**：receipt 验证把「缺证据的成功」折算 degraded/rejected；
  harness 台账不再出现零证据 completed。
- **兼容**：三条路径的既有测试（swarm 377 + subagent 套件 + harness v7）
  全部保持通过；新增字段/键均为 additive。

## 后续方向（Out of Scope）

- H06 resource envelope 对接：`resource_class` 标签 + scheduler 端口已
  预留，接入时只换 scheduler 实现或调上限。
- run_parallel 迁移到 gateway（本轮保守不动）。
- swarm 子任务作为 V5 DB 实例节点（ADR-0187 D2 预留的 K 级后续）。
