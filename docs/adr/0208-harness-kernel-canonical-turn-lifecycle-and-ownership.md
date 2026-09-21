# ADR-0208: Canonical Harness Turn Lifecycle, Versioned Events & Ownership Matrix

- 状态：Accepted
- 日期：2026-09-20
- 关联：ADR-0180（Pi-native Harness Kernel + SessionPlan 契约）、ADR-0076（SessionPlan 计划真相）、ADR-0079（GIS Harness Runtime v2 原子状态）、ADR-0100（Pi Runtime v6 cancellation，"agent_settled 是唯一 turn 终结者"）、ADR-0134（V7 只读派生投影）、ADR-0182（Resource Governor）、ADR-0197（Durable Mission Runtime）
- 分支：`fix/harness-kernel-canonical-lifecycle`

## Context

ADR-0180 落地了 kernel 信封契约与 `GISSessionRuntime`（begin_turn / evidence /
end_turn / checkpoint），并已接入 Pi bridge 生产热路径。但 turn 的**生命周期
相位**仍然分裂：

1. **无 canonical phase**。kernel 只有终态 `TurnStatus`；"这个 turn 走到哪
   一步"的词表散落在 V7 `RuntimePhase`（12 值，任务级派生投影）、
   `StageState`（workflow）、`GoalNodeStatus`（goal graph）、
   `RequirementState`（goal satisfaction）等互不知晓的模块里——同域语义
   （executing/running/active、succeeded/satisfied/fulfilled）四种拼法。
2. **无聚合 turn context**。下游各取所需：bridge prompt 只收三个裸字符串，
   mapspec/memory/knowledge 各自查 Redis/session 全局。
3. **无 versioned 事件**。decision journal 只有 12 个 legacy kind，无 seq、
   无幂等键、无因果 id；SSE 家族冻结（ADR-0180 D-006）不适合承载内部 trace。
4. **无 ownership matrix**。谁拥有哪个字段、谁是唯一写者，只散在各 ADR 的
   散文里。
5. **重复表达式**。`_hk_status` 结算映射在 bridge 流式/非流式两处重复；
   late callback 只有 bridge 日志行，kernel 无台账。

## Decision

### D1 — Canonical Turn Phase（turn 级、kernel 拥有）

`models.TurnPhase` 封闭词表：`created → understanding → planning →
qualifying → executing ⇄ observing → verifying → terminal`，回路
`repairing` / `replanning`，终态 `completed / failed / cancelled / aborted /
refused / interrupted`（与 `TurnStatus` 终态 1:1 同名——一个 turn 只有一份
终态真相）。要点：

- **kernel 拥有、驱动点折叠进既有方法**：begin_turn→understanding、
  intent 成功→planning（含 replanning 回边、执行中重意图）、
  begin_step(命中计划能力)→qualifying→executing、证据(带 ref)→observing、
  patch 应用→repairing、end_turn→verifying→terminal。**零新增 bridge
  挂点**——最热文件（近 7 天 19 commits）不新增冲突面。
- **转移表 `PHASE_TRANSITIONS` fail-closed**：表外 (from, trigger) 组合
  拒绝推进，写 `phase_refused` 事件 + `OUTSIDE_TABLE` 历史行 + 计数器；
  绝不静默漂移，也绝不抛异常（增值披露不阻断）。
- **终态唯一写者 = end_turn**（对齐 ADR-0100 INV）：任何 phase 只能经
  `verifying` 到终态；终态后 `advance` 一律拒绝（record 非 running）。
- **词表语义区分**：`cancelled`=用户取消（执行中）；`failed`=工具/执行错误
  （可重试）；`aborted`=策略/系统中止（governor 预算、fencing；bridge 现有
  failed 族映射保持不变，作为兼容事实）；`refused`=未执行即拒绝（澄清/
  拒答，零副作用）；`interrupted`=进程死亡后下一次 hydrate 对账。
- **与 V7 的关系**：V7 `RuntimePhase` 仍是任务级派生投影（同输入同阶段，
  不迁移、不废弃）；`phase_adapter.project_runtime_phase` 提供全量
  canonical→V7 字符串投影供 parity 测试与可观测对照。turn 级
  `replanning` 是"本 turn 在等待/执行重规划"的投影；任务级
  `plan_runtime.replan_pending` 旗标仍归 plan_runtime 所有。
- 旧 turn 记录加载时 `phase="created"`（默认值）；存量行的终态权威仍是
  `status`。不做回写迁移。

### D2 — HarnessTurnContext（K2 有界投影）

`HarnessTurnContext`（`hk.ctx.v1`）typed + bounded：identity
（session/envelope/turn/host/mission_ref）、phase/status、请求与目标摘要、
capability 视图（名称+状态+ref，≤16）、执行摘要（步计数/tool_calls）、
证据 refs（≤8，只带 ref 不带 payload）、恢复状态。`build_turn_context`
是纯函数，跨域事实（mission/governor/knowledge）由调用方注入——kernel
不 import 这些服务。首个生产消费者：end_turn 结算时的单行结构化摘要
（`[HarnessTurnContext]`）。prompt 组装迁移到该投影是后续 seam（见
Non-goals）。

### D3 — Versioned Event Journal（K4：升级既有 journal，不建第二总线）

`PlanDecision` 升级为 canonical 事件行：`seq`（信封单调）、`event_id`
（幂等键）、`causal_id`（产生身份，tool_call_id/触发 id）。规则：

- **causal 行幂等**：`event_id = kind:turn_id:causal_id`，bridge
  lock-contention 重试/重复回调不会双写（与 `_settle_step` 的
  tool_call_id 幂等同纪律）。
- **seq 跨 supersede 迁移**：换目标重建信封时 `event_seq` 随
  turns/decisions/recovery 一起迁移——seq 单调/重放序不变量跨信封重建
  成立，不会撞号。
- **非 causal 行 seq 限定**：`kind:turn_id:#seq`，每次出现都是独立事实，
  seq 即重放序。
- **词表 `EVENT_KINDS ⊇ DECISION_KINDS`**：新增 phase_changed /
  phase_refused / intent_resolved / tool_started / tool_succeeded /
  tool_failed / tool_late / qualification_changed / observation_received /
  goal_evaluated / repair_requested / repair_applied；旧 `step_marked` 不
  再新发（存量行保留可读）。`map_mutated` 列为保留名（事实写者在
  MapSpec store 侧，见 Non-goals）。
- **发射点 = kernel 已有事实点**：intent 成功（intent_resolved）、
  begin_step（tool_started + 变化时 qualification_changed）、证据落定
  （tool_succeeded/failed + 带 ref 时 observation_received）、product
  里程碑（goal_evaluated）、patch 应用（repair_applied）、phase 推进
  （phase_changed/phase_refused）、late callback（tool_late）。
- **journal 环保持 24（hk1 界）**：事件行更密，但 checkpoint 环保存更完整
  的快照；抬高界会同时抬高落盘校验上限——滚动发布期旧 pod 对 >32 行信封
  model_validate 失败 → load None → 可能以空信封覆盖真实会话。重放深度
  与舰队安全之间取安全。
- SCHEMA_VERSION 保持 2（增量字段同规则加载，世代不动）。

### D4 — Late Callback 台账（#1407 的观测补全）

bridge 的 `_late_for_plan` 守卫（跳过晚到证据、防 successor 串号）保持
不变；新增 kernel `record_late_callback`：晚到回调以 `tool_late` 事件记在
**原始 turn** 名下（幂等 per tool_call_id），不重开 turn、不推进 phase、
不落 successor。bridge 侧经 `async` fire-and-forget task 调用（自身吞异常
+ 调度失败吞 TypeError/RuntimeError），绝不阻断回调路径。

### D4b — 重启中断的相位终态化

begin_turn 发现上一 running turn 时，先按转移表走
`settle_started → verifying → host_interrupted` 再写 `status="interrupted"`
—— 终态 turn 永远不会出现"终态 status + 运行态 phase"的混合真相。

### D5 — 结算映射去重 + ownership

bridge 流式/非流式共用的 `_hk_turn_status` 纯函数（行为逐位不变）。

**Ownership Matrix**（字段级权威；"写者"=唯一合法 mutation 点）：

| 组件 | 权威字段 | 唯一写者 | 派生/只读投影 | 持久域 | 版本/指纹 |
|---|---|---|---|---|---|
| Pi runtime | tool loop、RPC、turn token、dispatch cache | Pi/bridge | SSE 流 | Pi 域 | run_id |
| Harness kernel | turn 台账（status/phase/phase_history）、kernel steps+evidence、事件 journal、recovery 元数据、checkpoint 环 | `GISSessionRuntime`（单锁域） | `HarnessTurnContext`、`projection.*` 行、V7 投影输入 | SessionPlan envelope（sessionplan 前缀） | envelope revision + event_seq |
| SessionPlan（capability 层） | progress 行状态/supersede/user_goal/gis_chapter | `session_plan.apply_tool_result`（唯一真相） | kernel steps 关联不复制的 `PlanStep.capability`、SSE 4 事件 | 同一 envelope | envelope revision |
| Mission | mission 状态机 + lease_epoch | `mission_runtime.store.transition`（fencing） | hotpath session_ctx 视图 | DB 台账 | lease_epoch |
| GISWorldState / MapSpec | desired cartographic state | `commit_mapspec_state` 单事务（ADR-0079 D1） | ref-only 进 prompt/evidence；kernel 只持 ref | session_data map state | mapspec 版本 |
| Workflow runtime | StageState、意图差异同步、graph events | workflow_instance / hooks | V5/V7 投影输入 | gis_chapter["workflow_runtime_v6"] | instance version |
| V7 状态机/PlanRuntime | RuntimePhase 派生、plan version/fingerprint、replan_pending、loop 预算 | `runtime_state_machine` 派生器 / `plan_runtime` 写点 | 本 ADR 的 phase_adapter 对照 | gis_chapter["runtime_state"]/["plan_runtime"] | version/fingerprint |
| Evidence / Claim | 工具证据 ref、trace JSONL | ToolDispatchService / trace_store | kernel step evidence（ref 引用，不复制内容） | session_data / trace 分段 | seq 单调 |
| Resource Governor | 预算/租约/成本账 | governor facade（ADR-0182） | kernel 不复制预算（V7 复用 LOOP_BUDGETS 同纪律） | governor 域 | 自有 |

边界不变量：kernel 对 capability 行**只关联不复制**；对 MapSpec/GeoJSON
**只持 ref**；对 mission/governor **零写**。任何新能力先回答"写者在矩阵
哪一行"，没有行就不进 kernel。

## Consequences

- 一条生产 turn 的生命周期现在是单一词表 + 单一台账：`Request →
  understanding → planning → qualifying → executing ⇄ observing →
  verifying → completed/failed/...`，事件行可直接重放（seq 序 + causal
  链 + phase_history）。
- bridge 热路径新增成本：计划内 dispatch 每次 1-2 行事件（同一次 save 落
  盘）；turn 级 1 次 phase 推进；结算 1 次纯函数投影 + 1 行日志。锁域、
  锁次数、save 次数不变。
- 词表收敛可见化：phase_changed 事件 + V7 投影对照是后续把
  `StageState`/`GoalNodeStatus` 等同域词表逐步映射到 canonical 的锚点
  （本 PR 不动它们——它们的写者各自独立，投影映射属后续方向）。

## 明确不做（本 PR 边界）

- 不迁移 `agent_settled` 侧 finalize/workflow/runtime 四连触发（ADR-0180
  K9 缓行继续）；不动 hotpath_convergence session_ctx（mission 方向热区）。
- 不改 SSE 事件家族（ADR-0180 D-006 冻结）；kernel 事件不外发前端。
- `map_mutated` 只保留名（发射缝在 MapSpec store 侧，属 ADR-0079 域）。
- prompt 组装（`bind_turn_prompt` 等）切换到 `HarnessTurnContext` 投影：
  待 bridge 低冲突窗口做（依赖三字符串签名的调用方多，收益/风险本窗口不
  匹配）。
- `refused`/`aborted` 的生产发射者（拒答检测、governor 中止）不在本 PR；
  语义与测试先行，接口（`end_turn(status=...)`）已就绪。

## Acceptance

- 转移表封闭性/可达性/终态无出边有测试钉住；
- 生产主线（intent→dispatch→evidence→settle）驱动
  created→…→completed 全程可断言；
- 重复回调、lock 重试不双写事件；晚到回调不重开 turn；
- cancel/refused/replan/patch 路径有独立回归；
- V7 投影 parity、双会话隔离、v1 信封零漂移回归通过。
