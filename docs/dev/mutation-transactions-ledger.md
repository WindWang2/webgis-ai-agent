# 方向 8：Mutation Transactions — 交付台账

> 线：`harness/map-mutation-transactions-v1` · 每里程碑更新 · 状态标记：☐/◐/☑

## M1 — 契约：envelope + precedence（U0/U1/U2） 状态：◐

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| U0 mutation inventory | `docs/dev/mutation-transactions-recon.md` §1 调用链 + §3 审计复核 | — | 9 入口枚举，producer/actor/CAS/回滚面成表 |
| U1 envelope | `app/services/gis_world_state/envelope.py`（新） | `tests/cartography/test_mutation_transactions.py` | MutationEnvelope 可序列化；producer 分类确定性 |
| U2 precedence | `app/services/gis_world_state/precedence.py`（新） | 同上 | 阶梯排序/覆盖规则/fill-undeclared 全分支 |

## M2 — 服务端幂等（U3） 状态：☐

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| engine 锁内 dedup | `lifecycle_engine.py`（加可选参）+ `store.py`（extra_fields） | 同上 | 同 id 重放单执行；重试（CAS 落后）返回 committed |

## M3 — 门面/调用点接线（U5/U6） 状态：☐

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 门面透传 envelope | `gis_world_state/mutation.py` | 同上 | provenance/collab op 载荷带 producer/mutation_id |
| 适配器 actor/producer | `mapspec_store.py` + `tool_dispatch_service.py`（2 处 actor） | 同上 | 工具 authoring 路径出现 provenance 条目 |
| repair/finalize 归因 | `runtime_repair.py`/`completion/repairs.py`/`layer_manager.py`（producer 参数） | 同上 | REPAIR_AUTOFILL 分类落账 |

## M4 — 前端队列 envelope（U4） 状态：☐

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| client_mutation_id 上行 + duplicate 收敛 | `frontend/lib/mapspec/user-mutation.ts` | `frontend/lib/mapspec/user-mutation.mtid.test.ts`（新） | 每笔携带；duplicate 按成功收敛 |
| pending 身份/代际 + 观测 | `frontend/lib/mapspec/session-cursor.ts` | `frontend/lib/mapspec/session-cursor.mtid.test.ts`（新） | per-layer gen 单调；深度/时延环 |

## M5 — Reconciliation（U7） 状态：☐

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| anomaly 检测纯函数 | `app/services/gis_world_state/reconciliation.py`（新） | `tests/cartography/test_mutation_reconciliation.py`（新） | 5 类 anomaly code 确定性 |
| 生产接线（sync 载荷） | `app/api/routes/ws_collab.py` sync 分支 | 同上 | sync_ok 附有界 anomalies |

## M6 — 竞态/多客户端确定性测试（U8/U9） 状态：☐

| 场景 | 文件 | 证据 |
|---|---|---|
| remove vs agent upsert（僵尸面） | `tests/cartography/test_mutation_transactions.py` | 事件驱动，零 sleep |
| 409→retry + duplicate、双浏览器同秒 toggle、stale response、重连 replay | 同上 + 前端两测试 | barrier 驱动 |

## M7 — Review / 文档 / PR 状态：☐

| 任务 | 文件 | 证据 |
|---|---|---|
| 独立 review（Subagent B） | `docs/dev/mutation-transactions-decisions.md` D-10 | P0/P1 全修 |
| ADR | `docs/adr/0183-mutation-transactions-v1.md`（新） | 0183 为安全号（0180-0182 被在途 PR 占用） |
| PR | — | 不 merge、不 auto-merge |
