# H04 — Durable Harness Turn Journal 设计与实现说明

> ADR-0216 是长期决策记录；本文是工程实现视图（数据流、接线点、运维面、验证）。

## 数据流

```
chat turn（Pi bridge / legacy engine）
  └─ GISSessionRuntime（envelope = live 权威，不变）
       ├─ _event()  ──┬─ push_bounded(plan.decisions)   ← 既有 24 行环（fleet 约束保留）
       │              └─ _ledger_record() → sink.record()   ← 新旁路（fail-open）
       └─ _journal() ─┘（legacy 行：内容稳定哈希键，at-least-once）
                          │
                 TurnJournalSink（有界队列 1024，to_thread 落库）
                          │
                 TurnEventLedger（turn_events 表，event_id UNIQUE 判重）
                          │
        ┌─────────────────┼──────────────────────┐
   崩溃取证 resume   因果树诊断（API/CLI）   compaction/retention sweep
```

## 接线点清单（改动面）

| 位置 | 改动 | 性质 |
|---|---|---|
| `app/services/harness_kernel/runtime.py` | `_event`/`_journal` 尾部旁路 `_ledger_record`；`end_turn` 的 `turn_ended` 行补 `detail.status/tool_calls` | additive；envelope 语义零变化 |
| `app/models/db_model.py` + migration 0096 | `workflow_instances` +`turn_id`/`run_id`（nullable） | additive |
| `app/services/workflow_runtime/store.py` | `create_instance` 捕获 RuntimeContext；`_row_to_instance` 投影补两字段 | additive |
| `app/main.py` | `_periodic_turn_journal_sweep`（600s 周期；压缩 7d/删除 30d，env 可调）+ 停机 sink flush | 新增后台任务 |
| `app/api/routes/turn_journal.py` | 3 个只读端点（`require_owned_session`） | 新增 |
| `scripts/turn_journal_inspect.py` | 运维 CLI（因果树/悬挂步骤/恢复建议） | 新增 |

## 运维面

- API：`GET /api/v1/harness/turn-journal/{session_id}`（报告：因果树+未终局+建议）、
  `.../events`（分页原始流）、`.../stats`（体量）。
- CLI：`python scripts/turn_journal_inspect.py <session_id> [--turn-id T] [--events] [--stats] [--json]`。
- env：`GIS_TURN_JOURNAL=off`（关投影，落库行仍可查）、
  `GIS_TURN_JOURNAL_QUEUE_MAX`、`GIS_TURN_JOURNAL_SWEEP_INTERVAL_S`、
  `GIS_TURN_JOURNAL_COMPACTION_AGE_S`、`GIS_TURN_JOURNAL_RETENTION_AGE_S`。

## 恢复分类语义（resume.py，advisory 只读）

| 分类 | 条件 | 建议 |
|---|---|---|
| `settled_receipt_present` | 悬挂步骤的 mutation_id 在本 turn 有 `map_mutated` receipt | no_action |
| `needs_receipt_check` | mutation 类工具 started 无结果、无 receipt | 同 mutation_id 重放（engine dedup 幂等），先查 MapSpec 当前 revision |
| `safe_replay` | 纯读白名单工具悬挂 | 直接重放 |
| `needs_replan` | 其他/未知悬挂（保守默认） | 交还规划层 |

## 验证与基线

- 新增 43 用例（`tests/unit/turn_journal/`）全绿：幂等判重/并发对撞兜底、
  崩溃注入（悬挂 mutation/receipt-after-commit）、压缩后因果正确（receipt
  链保留）、retention 只删老行、tz 往返、sink fail-open/背压/脏行、kernel
  端到端、workflow 因果捕获、时钟 AST gate。
- 零回归：kernel 5 套件 64 通过；workflow_runtime 与 pi_post_dispatch 的
  失败均为 master 既有（1F+7E / 1F，issue #1553 族），两侧一致。
- 性能（tmp SQLite 合成基准）：append 578/s（生产每 turn 数十事件量级）；
  查询硬帽 200 行 ~10ms；`sink.record` 队列路径 ~0.016µs（热路径零感）。

## 已知边界

- legacy `_journal` 行无稳定幂等键 → at-least-once（ADR-0216 D1）。
- `_compactable_turns_sync` 以"最后事件 = turn_ended"筛压缩候选：late
  callback 在终局后落账的 turn 不会被压缩（保守多留行，retention 兜底）。
- 诊断 API 鉴权复用会话所有权（`require_owned_session`）；跨 session 聚合
  视图未提供（需要 admin 面时另行立项）。
- flush 预算语义针对停机面：in-flight 单条 append 不可中断；若在活跃流量
  下调用 flush，预算重置后遗留的 drain task 恢复无界（生产 flush 只在
  停机路径调用）。
- 单 turn 事件数 >200 时因果树只展示该 turn 的头部窗口（turn 级聚合与
  恢复分类不受影响；后续可按 turn 分页）。
