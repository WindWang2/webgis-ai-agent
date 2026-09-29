# ADR-0216: Durable Harness Turn Journal（会话级可恢复执行账本）

- 状态：Accepted
- 日期：2026-09-29
- 关联：ADR-0180（Pi-Native Harness Kernel / SessionPlan）、ADR-0208（canonical
  turn lifecycle + versioned event journal）、ADR-0183（mutation transactions v1）、
  ADR-0214（trace/replay oracle v3 持久化缺口 gap3/4）、issue #1551（时序口径）、
  issue #1553（pre-existing 测试失败基线）
- 落地：`app/services/turn_journal/`、`app/models/harness_journal.py`、
  migration `0096_harness_turn_journal`、`app/lib/runtime/clock.py`、
  `app/api/routes/turn_journal.py`、`scripts/turn_journal_inspect.py`

## 背景与问题

F03 收敛后的 canonical 事件 journal（`plan.decisions`）是**envelope 内有界环**：
24 行 FIFO、随整个 envelope blob 重写持久化、判重靠环内线性扫描。三个直接后果：

1. **头部驱逐即失忆**：第 25 个事件起旧行静默丢失，崩溃取证/因果查询没有
   幸存副本；envelope 丢失（Redis 故障/误清）= 全部执行历史消失。
2. **因果断链**：`workflow_instances` 只有 `session_id` 没有 `turn_id`——
   turn → workflow → mutation 的链在账本之外无处可查；attempt 只是裸计数器。
3. **时序三态并存**：epoch float（envelope）/ naive-UTC（DB）/ aware ISO
   （provenance），没有共享时钟入口（issue #1551 的结构根因）。

## 决策

### D1 — envelope 仍是唯一 live 权威；账本是 append-only 事实投影

`turn_events` 表**不是**第二状态源：无生命周期语义（`status` 仅为存储态
`recorded|compacted`），不驱动 phase 转移，无消费者 ACK/重投递（不做成消息
队列）。写入只发生在 kernel 既有的 `_event()`/`_journal()` seam 旁路
（`_ledger_record`），canonical 行**复用 envelope 的确定性幂等键**
（`kind:turn_id:causal_id`），legacy 行用内容稳定哈希键
（`legacy:{kind}:{turn_id}:{at_ms}:{digest}`，明确 at-least-once 边界）。

### D2 — fail-open 与 envelope 的 fail-closed 刻意不同

envelope 保存走 session lock 降级拒绝（#1401 契约）；账本投影任何故障
（DB 不可用/队列满）只丢投影并计数（`journal_sink_drop` /
`journal_append_failed`，限频日志）——chat 热路径永不因账本阻塞/报错。
丢的是崩溃取证能力，不是正确性。

### D3 — exactly-once 边界

- 副作用 mutation：既有 `mutation_id` receipt（dedup index + CAS revision，
  ADR-0183）不变；账本只**引用** receipt（`mutation_revision` 列 +
  `map_mutated` 行），不替代。
- 账本 append：`event_id` UNIQUE 判重（先查后插 + 唯一约束竞争兜底）。
- 恢复分类（`resume.py`）是**advisory 只读**：`settled_receipt_present` /
  `needs_receipt_check`（建议同 mutation_id 重放，engine dedup 保证幂等）/
  `safe_replay`（纯读白名单）/ `needs_replan`（保守默认）。绝不自动执行副作用。

### D4 — 统一时钟入口（#1551 起点而非全量清偿）

`app/lib/runtime/clock.py`：进程内 aware（`utc_now`/`epoch_to_utc`），DB 边界
显式 naive-UTC（`to_db_utc`，全库既有列口径），读取边界把历史 naive 按 UTC
兼容抬回 aware（`from_db_utc`）。本方向新模块由 AST gate 禁止裸
`datetime.utcnow()`/无参 `datetime.now()`（`test_clock_discipline.py`）。
全库存量 134 处清偿仍归 issue #1551。

### D5 — 有界性与 compaction 纪律

- 查询硬帽 `MAX_JOURNAL_QUERY=200`；`detail` ≤2KB canonical JSON
  （`sanitize_detail` 确定性截断）；大内容只存调用方显式 `payload_ref`。
- compaction：已终局 turn（任意位置存在 `turn_ended`，兼容 late callback）
  的非凭证行折叠为一行 `turn_compacted` 摘要；`map_mutated` 与带
  `payload_ref` 的行**永不压缩**（mutation receipt 链必须全量保留）。
- retention：按 `occurred_at` 分批删除（lifespan 周期任务，默认压缩 7d /
  删除 30d，env 可调，0 关闭）。压缩后因果查询在 turn 粒度仍正确
  （摘要行携带 counts/terminal_status/last revision，参与聚合）。

### D6 — 因果桥：workflow_instances 加 nullable turn_id/run_id

创建实例时从环境 `RuntimeContext`（ContextVar，随 to_thread 穿透）捕获；
REST 直启/恢复扫描无 turn 上下文 → NULL。additive，不改任何既有列语义。
`WorkflowEventRow` 不加列（instance 级桥接已足够因果查询，避免双写面）。

## 拒绝的替代方案

- **把 envelope 决策环改成 DB-backed**：改写 ADR-0208 的 fleet-safety 界
  （24 行界是滚动部署约束），写放大与延迟进入 chat 热路径——拒绝。
- **账本带消费者语义（队列/ACK）**：与 spatial_events 的 at-least-once
  控制面职责重叠，第二套投递语义——拒绝（H04 明确 Out of Scope）。
- **让 trace recorder 读账本**：F09 录制件有独立 schema/契约（ADR-0214），
  本期保持互不依赖；账本是否成为 trace 的 backing store 留给 F09 后续。

## 后续方向（Out of Scope）

- 全库 naive utcnow 清偿与 ratchet 化（#1551）。
- `WorkflowEventRow` 级 turn 关联、per-attempt 身份（当前是计数器）。
- 账本 → trace/replay 的投影接线（ADR-0214 gap4 的 legacy host 盲区）。
- 诊断面的前端可视化（当前 API + CLI）。
