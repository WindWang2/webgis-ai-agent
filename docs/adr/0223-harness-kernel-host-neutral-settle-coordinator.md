# ADR-0223: Harness Kernel Host-Neutral Settle Coordinator（K9 / agent_settled 编排收口）

- 状态：Accepted（随本 PR 交付）
- 日期：2026-10-16
- 关联：ADR-0180（K9 缓行项，本 ADR 清偿）、ADR-0204-f03（bridge 单结算 seam 与
  `settle_turn_projections`——本 ADR 的直接前驱）、ADR-0208（canonical turn lifecycle
  / ownership matrix / "不迁移 agent_settled 四连触发" 的显式 Out-of-Scope）、
  ADR-0210（Pi↔Harness 边界与 dispatch 披露管线）、ADR-0079（MapSpec authority）、
  ADR-0100（agent_settled 唯一 turn 终结者）

## Context

F03（ADR-0204-f03）把 Pi stream/non-stream 的 turn 结算收敛到 bridge 单 seam
`PiBridge._settle_turn_outcome`，效果管线落在 `app/services/chat/pi_post_dispatch.py`
的 `settle_turn_projections`。清偿后仍残留三块结构债：

1. **通用 GIS settle 编排寄居 host 域**：效果序（终验 final gate → WorkflowInstance
   → RuntimeState(turn_settled) → 九域 checkpoint → 证据链 USER_OUTPUT+persist）实现
   在 chat 域（Pi 侧私有模块），词表/映射（`_hk_turn_status`、`_ABORT_SOURCE_STATUS`、
   `TurnSettleOutcome.settle_class`）也散在 bridge/chat——kernel 台账的终态语义由
   host 侧代码拼装。
2. **legacy host 只有半个结算**：`execution_engine` 的 finally 仅做 kernel
   `end_turn`（经 `legacy_adapter.safe_end_turn`）+ tracker + replay 录制；终验 /
   workflow / runtime 投影 / checkpoint / 证据链 / parity / refusal 全部缺失
   （recon 矩阵见 `task_plan.md`）。每个新 Harness 能力仍要在两个 host 各接一次。
3. **幂等只到"no-op"粒度**：kernel `end_turn` 对重复结算返回 `[]`，无法区分
   「已结算 / 无此 turn / 被后继终态化」；效果管线无 settle 级幂等键、无迟到
   （stale generation）防护、无收据（receipt）——重启/双 finally/迟到 settle
   只能靠各效果自身门兜底，且 clean 分支存在级联跳过缺陷（终验抛错会连带跳过
   workflow/state/checkpoint，recon H1）。

锁约束（决定接线的硬边界）：`maybe_finalize_map_product` /
`maybe_update_runtime_state` / `save_session_plan` 等写路径效果**内部自取非重入
session lock**；legacy engine 整个 turn 持有该锁（RUN-03）。因此 legacy 不能在
持锁期间运行效果管线；而 kernel `end_turn` 又必须在锁释放前落终态（F03 S1：
后继 turn 的 `begin_turn` 不得把本 turn 误标 `interrupted`）。

## Decision

### D1 收口边界：coordinator 拥有「结算编排」，不拥有任何事实

新模块 `app/services/harness_kernel/settle.py`——**唯一的通用 GIS settle
orchestration 生产入口**。它编排既有效果函数，不复制任何权威：

| 关注点 | 权威拥有者（不变） |
|---|---|
| turn 终态 status/phase 台账 | `GISSessionRuntime.end_turn`（唯一终态写者，本 ADR 不改） |
| MapSpec desired state / map_product 裁决 | MapSpec store / `completion.pipeline`（效果，被编排） |
| WorkflowInstance / RuntimeState / context checkpoint | 各自模块（效果，被编排） |
| 证据链 / trace | `chain_emitters` / `trace_store`（效果，被编排） |
| 工具执行、RPC、tracker、SSE、abort 台账 | host（Pi bridge / legacy engine） |

coordinator 是 ADR-0208 ownership matrix 的新行："settle 编排 + 收据"归
`harness_kernel.settle`，输入输出都是引用/投影（evidence refs，不收 payload）。

### D2 Typed settle 契约（kernel 内、封闭词表、fail-closed）

- `SettleCause`（封闭词表）：`success / tool_error / model_error / user_cancel /
  system_abort / policy_abort / timeout / send_error / host_process_death /
  unclassified_error / host_interrupted`。
- `CAUSE_STATUS` 单表：cause → kernel `TurnStatus`
  （success→completed；user_cancel→cancelled；system_abort/policy_abort→aborted；
  host_interrupted→interrupted；其余失败族→failed）。未知 cause fail-closed 按
  `failed` 收口（继承 F03「未分类不伪装 completed」）。
- `settle_class_for_status`：completed/refused→clean；cancelled→cancelled；
  aborted→aborted；其余→failed（与 F03 `TurnSettleOutcome` 语义逐位一致）。
- `HarnessSettleInput`（frozen）：`session_id / turn_id / host("pi"|"chatengine") /
  cause / failure_class(≤64) / failure_detail(≤200) / reason / state_trigger /
  idempotency_key(默认 session:turn) / projections_pre_settled / pre_terminated`。
- `HarnessSettleOutcome`（frozen）：`kernel_status / settle_class / disposition /
  receipts / map_product / refusal / failure_class / failure_detail`。
- `SettleEffectReceipt`（frozen）：`effect（封闭词表）/ status(applied|skipped|
  failed|already) / detail(≤120)`。

effect 词表（顺序即权威序，测试锁定）：

```
E1 final_gate            clean only   maybe_finalize_map_product(final_gate=True)
E2 workflow_projection   all classes  maybe_update_workflow_instance(event="auto")
E3 runtime_state_projection all       maybe_update_runtime_state(turn_settled=True)
E4 context_checkpoint    all（开关）  runtime_state_enabled() → checkpoint_context_layers
E5 map_product_disclosure clean only  finalization_sse_payload / read_stored_map_product
E6 evidence_chain        all          emit_chain_for(USER_OUTPUT) + persist_turn_chain
K  kernel_end_turn                    GISSessionRuntime.end_turn（shield+5s 预算）
P  lifecycle_parity                    parity 快照 + phase_adapter 投影（观测）
```

相对 F03 的两处**有意**语义修正（均为 bug 面，测试钉住）：

1. E1-E4 **逐段独立守卫**：终验抛错不再级联跳过 workflow/state/checkpoint
   （recon H1；收据如实记录 failed/skipped）。
2. 非 clean 结算的 E6 同现状（task_complete=False 落链）。

### D3 幂等、stale 与收据

- **settle 级幂等键**：`idempotency_key`（默认 `session:turn`）。进程内
  bounded receipt cache（OrderedDict ≤256）+ per-`(session,turn)` asyncio 锁：
  同键重复调用**重放同一收据**（disposition=duplicate），不重跑任何效果。
- **disposition 判定**（先读 kernel 台账快照——复用既有只读
  `GISSessionRuntime.lifecycle_parity_snapshot(turn_id)`（phase/status），
  不新增 kernel API；successor 检查直接读 SessionPlan 信封）：
  - 无 plan / 无 turn 记录 → `unknown_turn`（零效果、零 end_turn、只回收据）；
  - 记录已终态：
    - `pre_terminated=True`（legacy 锁内预终态化后的正规首次调用）→ 走完整
      效果序，K 腿诚实记 `already`；
    - 已记录 status == 本 cause 应落 status → `duplicate`（重放收据）；
    - 否则（迟到 settle，如 turn N 在 turn N+1 `begin_turn` 被终态化为
      `interrupted` 后才到达）→ **`stale`：零效果**——迟到结算不得在
      新 turn 在飞时触发会话级终验/投影污染（用户不可见状态漂移）；
  - 记录 running → 正规路径（refusal 降级 → E1..E6 → K → P）。
- **successor-in-flight 守卫**（legacy 锁外效果的交错窗口）：引擎释放会话锁
  与效果函数自取锁之间存在微小窗口，若后继 turn 已在飞，本 turn 的会话级
  效果将评估后继的中间状态——coordinator 在效果前重读信封，发现比本 turn
  更新的 running turn 即跳过会话级效果（收据 `skipped: successor in
  flight`），由后继自身的 settle / 既有触发点以真实状态收口（终验本就是
  会话级幂等验证器）。Pi 在 turn 锁内结算，该守卫恒为 False（零行为变化）。
- **跨进程/崩溃**：kernel `end_turn` 幂等 + 各效果自身指纹门仍是权威防线；
  receipt cache 只是进程内编排幂等（不是第二真相源——「turn 是否已结算」的
  持久真相只在 kernel 台账）。
- **refusal 降级**仅正规路径（clean 且非 pre_terminated）：判据仍是
  `turn_refusal_candidate`（ADR-0204-f03 D3）。`pre_terminated`（legacy）的
  终态已由锁内 `safe_end_turn` 写定，降级窗口已关闭，不追改（user-wins /
  first-wins 纪律）。

### D4 接线：host 只剩薄适配

**Pi**（`agent_pi_bridge`）：`_settle_turn_outcome` 薄化为
「`pop_turn_abort_source` + flags → `cause_from_host_signals`（kernel 纯函数）
→ `settle_turn(input)` → tracker/rt_ev/SSE 投影」。clean in-try 预跑改为
coordinator 效果腿 `settle_effects(...)`（同实现、提前披露；seam 传
`projections_pre_settled=True`）。`_hk_turn_status` 保留为组合纯函数
（`status_for_cause(cause_from_host_signals(...))`，行为逐位不变，既有测试面）。
`TurnSettleOutcome` / `settle_turn_projections` / `log_lifecycle_parity` /
`resolve_turn_refusal` 在 `pi_post_dispatch` 保留为委托薄层（lab/契约面不变）。

**Legacy**（`execution_engine`）：锁内 finally 保留 `safe_end_turn`（S1 预终态化，
既有 ADR-0180 行为不变）；锁释放后调用
`settle_turn(pre_terminated=True, cause=cause_from_rt_ev(rt_ev))`
（stream：`chat_stream` 外层 finally，`acquired_lock.release()` 之后；非流式：
`chat()` 的 `async with lock:` 块外）。legacy 因此补齐终验/投影/checkpoint/链
收口（此前缺失），并获得 `aborted` 词表可达性；refused 不适用（D3）。
tracker/replay/flush 等 host 职责不动。

### D5 可观测性（有界）

- `hk_metrics.record("turn_settled", host=…, disposition=…, status=…)`（closed
  vocab，复用 kernel counters）；
- 一行结构化日志：`[HarnessSettle] session turn host disposition status
  effects=<E1:applied,…> duration_ms=<int>`；
- 收据进 outcome（内存对象，不落盘、不含 secrets/GeoJSON/CoT；map_product 仅
  披露负载引用既有 bounded payload）。

### D6 kill switch

`GIS_HOST_NEUTRAL_SETTLE`（stable，默认 ON；登记
`hotpath_convergence/flag_registry`，owner=`services/harness_kernel/settle.py`）。
OFF 时 coordinator 跳过效果序（E1-E6/P 逐段 skipped 收据），**K 腿（kernel
end_turn）照常执行**——authority 永不因开关漂移。legacy 接线随同门控（OFF 回退
到既有「仅 end_turn」行为）。

## 明确不做（边界）

- 不改 kernel `end_turn` 既有语义/幂等（唯一终态写者不变；新增的
  `turn_settle_snapshot` 是只读 API）。
- 不迁移 dispatch 披露管线（`apply_post_dispatch_disclosure` 留在
  pi_post_dispatch——它是 dispatch 域，不是 turn settle 域）。
- 不搬 Pi RPC/tool loop/事件 drain/tracker/active-turn Redis 表进 kernel。
- 不统一 legacy 内部执行方式（tracker 时序、孤儿修复、canonical 计划 flush
  均属 host）；不处理 `gis_world_state/mutation` 的 host 归因失真（另行方向）。
- 不新建持久化状态（receipt cache 进程内有界）。

## Acceptance

- contract：cause→status→class 单表、effect 序、收据词表封闭性有测试；
- 幂等：同键重复 settle 重放收据、零重跑；double-finally / lab 幂等用例保持；
- stale：turn N 迟到 settle 在 N+1 running 时零效果、kernel 不改写；
- recovery：settle 前/中 crash 语义 = 现有 D4b（下一 begin_turn 惰性 interrupted），
  收据不伪装；
- 跨 host 矩阵：Pi stream/non-stream/legacy × clean/cancel/abort/timeout/failed
  × finalizer-needed/no-map × duplicate × 录制器异常 × goal request_replan；
- 既有 seam/parity/pipeline/lab 测试全绿（除记录在案的 pre-existing 基线失败）。
