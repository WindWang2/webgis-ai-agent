# 方向 8：Agent–User–Map Mutation Transaction System — P0 勘察报告

> 线：`harness/map-mutation-transactions-v1` · 基线：origin/master @ `580b33e9` · 日期：2026-09-14
> 方法：只读勘察（S1 Explore subagent 全景 PR/ADR/审计对账 + 主 agent 深读核心路径）。
> 本文为 P0 交付物；行号以基线为准，执行中如漂移以代码为准并在 ledger 记录。

## 0. 摘要

MapSpec mutation 的**事务底座已经存在且成熟**：per-session 分布式锁（Redis 跨 pod，fail-closed）、
user 强制 CAS（`expected_revision`，不匹配 → superseded）、单事务提交（spec+revision+指纹+runtime layers
单 WATCH/MULTI）、事务回滚（含 fresh-session 语义）、W15 锁 guard、user-wins presentation 守卫
（spec owner 印记 + ring 兜底）、durable presentation 继承（`_preserve_durable_presentation`）。
任务书要求核验的历史审计 P1（409 风暴 / 僵尸复活 / agent 整层覆盖 / stale SSE revision / registry-spec
错位 / finalize 双端职责）**在 master 上已全部有结构性修复**，不重做。

真正的缺口是**统一元模型缺失**（Direction 8 的剩余价值）：

1. **无幂等键**：全 mutation 路径 grep 无 mutation_id/idempotency key —— 响应丢失后的重试可能双执行；
2. **无统一 precedence 阶梯**：user-wins 语义散落在 `presentation_owner` 印记、`classify_override`、
   W15 guard、前端 `_userPinned`/turn-focus 四处，无单一确定性 resolver，无 TEMPLATE/SYSTEM_DEFAULT 分类；
3. **pending 状态无代际**（audit ST-P3-3 半开放）：前端 pending presentation 是合并 map，无 per-op 身份；
4. **agent 工具写入绕过门面**：`mapspec_store` 适配器直调 `engine.apply_mutation`（默认 origin="agent"、
   无 actor、无 provenance、无 collab 事件、无 user-presentation ring 守卫）；
5. **`ws_service` 遗留 socket 直写 runtime layers**（ST-P3-4 仍在，设计性旁路）；
6. **无服务端 reconciliation**：spec vs runtime layers vs pending 的偏差无 anomaly 检测面。

本线在这些缺口上收敛，**不新建并行系统**：envelope/precedence/dedup 挂接既有
`gis_world_state` 门面 + `MapSpecLifecycleEngine` + 前端 `enqueueUserMutation` 链。

## 1. 生产调用链 before（mutation 入口 → 状态）

```
┌─ 用户浏览器 ─────────────────────────────────────────────────────────────┐
│ UI toggle/opacity/style/view/remove/reorder/workbench                    │
│   → user-mutation.ts enqueueUserMutation 串行链（每笔读游标 revision）     │
│   → POST /chat/sessions/{id}/mapspec/mutations {intent, expected_revision}│
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
mapspec_mutations.py（origin="user", actor="mapspec_route"，user 必带 CAS）
        │
        ▼
gis_world_state.mutation.apply_gis_mutation / _batch（门面）
  ├─ user-presentation 守卫（spec 印记 + ring；pre-lock + 锁内 pre_commit_check）
  ├─ engine.apply_mutation / apply_presentation_batch
  │    ├─ session_lock_registry.lock（Redis 跨 pod，degraded/lost fail-closed）
  │    ├─ deleted 检查 → CAS（user 必带；不匹配 → superseded）
  │    ├─ W15 guard_intent_locks（agent/system 撞用户锁 → 整笔拒绝）
  │    ├─ intent 分派（COW candidate + 质量门禁钩子 + upsert presentation 继承）
  │    ├─ checkpoint → save_mapspec（spec+rev+指纹+layer_op 单事务）→ 失败回滚
  │    └─ revision = prior+1（单调，磁盘 sidecar 复活）
  ├─ provenance.append（64 条 ring，best-effort）
  └─ collab bus publish（doc/delta/presentation/op，seq=revision）

其余生产入口：
  agent 工具 authoring（tool_dispatch_service._author_display_result ×2）
    → mapspec_store.layer_upsert → engine.apply_mutation   ← 绕过门面（无 provenance/actor/事件/守卫环）
  workflow_engine / project restore / report / artifact_registry → mapspec_store 适配器（同上）
  runtime_repair（origin=agent, actor="runtime_repair"）→ 门面 batch/单笔 ✓
  map_finalizer（completion/repairs.py）→ 门面 batch ✓
  finalize_display（layer_manager.py, actor="finalize_display"）→ 门面 batch ✓
  map_product_service.py:403 → engine 直调
  ws_service 遗留 socket（layer_toggled/opacity/removed）→ update_layer_in_state 直写 runtime layers（无 CAS 无 spec）
```

## 2. 与最近/在途 PR 的重叠矩阵

| PR | 状态 | 与本线交叠文件 | 风险 | 本线策略 |
|---|---|---|---|---|
| #1270 CI hygiene | open | mapspec/coordinator.py（仅 CLI 包装） | LOW | 不吞入；无阻断不接触 |
| #1273 QC-loop | open | gis_harness/runtime_state_machine.py、tools.py | MEDIUM | 本线不改这两个文件 |
| #1274 Pi typed tool surface | open | registry/bridge/pi_input_gate | LOW-MED | 不接触 dispatch 前置门 |
| #1275 Situation/World model | open | gis_situation/**（只读投影） | MEDIUM | 只读投影不冲突；不写其文件 |
| #1276 Capability graph | open | gis_harness/capability*、planner、recipes | LOW-MED | 不接触 |
| #1277 Harness kernel + SessionPlan | open | harness_kernel/**（新增）、chat.py、execution_engine、agent_pi_bridge | **HIGH（概念近邻）** | 本线落点在 mapspec/gis_world_state/frontend，零新文件重叠；chat.py 只加 schema 字段透传（若必须）则保持最小行面 |
| #1278 Skill library | open | gis_harness/skills/**（新增） | LOW | 不接触 |
| #1279 Resource governor | open | governor/**（新增）+ tool_dispatch_service 15 行 wrap | LOW | 本线改 tool_dispatch_service 仅 2 处 actor 传参（行面 <10 行），冲突面可忽略 |
| #1271/#1272 AC-V11/ads-v1 | merged | — | — | 已含于基线 |

**结论**：本线全部落点（`gis_world_state/**` 新增 3 文件 + `mapspec/lifecycle_engine.py` 加参 +
`mapspec/store.py` 加可选 extra_fields + `mapspec_mutations.py`/schema 加可选字段 +
`mapspec_store.py` 适配器加 actor 透传 + `tool_dispatch_service.py` 2 行 actor +
`ws_collab.py` sync 载荷附加 anomalies + frontend user-mutation/session-cursor）与在途 PR 文件交叠
≈ 0（tool_dispatch_service 仅与 #1279 的 wrap 点同文件不同区域）。

## 3. 历史审计 finding 复核（任务书必验项）

| Finding | 状态 | 证据（基线） |
|---|---|---|
| (a) user mutation 并发 409 | **已修** | user-mutation.ts:100-112 串行链（ST-P1-2）；引擎 CAS + superseded 语义（lifecycle_engine.py:1253-1321） |
| (b) remove superseded 僵尸复活 | **已修** | removeLayerFromSpec 一次重试 + pendingRemoved 保留压制 compose（user-mutation.ts:413-563）；layer_op 单事务消灭 spec/layers 错代（store.py commit_mapspec_state） |
| (c) agent 整层 upsert 覆盖用户 visible/opacity | **已修** | `_preserve_durable_presentation`（lifecycle_engine.py:894-951，#1070 F-3 含显式 visibility 剥离）+ 门面 UserPresentationGuard（mutation.py:68-108） |
| (d) stale SSE revision 倒退 | **已修** | session-cursor.ts:104-110 单调 guard；commitMapSpecDocument 旧代次拒绝（:117-127）；use-sse-stream.ts:555-561 提交前复核 |
| (e) layer registry vs MapSpec visibility 不一致 | **已修（结构性）** | layer_op 并入单提交事务（lifecycle_engine.py:2345-2375 + store.py:377-389）；遗留 ws_service 直写通道除外（见 §1） |
| (f) finalize_display 双端职责 | **按设计收敛** | 审计已撤 ST-P2-1；服务端 batch 收口（layer_manager.py:130-230，show+hide 均 agent durable）；前端保留 pending 局部收敛权 |
| (g) pending 无代际 | **半开放 → 本线 U4 关闭** | session-cursor pending 为合并 map 无 per-op 身份；本线加 mutationId+gen |
| 附加：ws_service 遗留直写（ST-P3-4） | **仍在（设计旁路）** | ws_service.py:108-131 update_layer_in_state 直写 runtime layers，无 CAS 无 spec 同步；socket 协议无 revision 语义，迁移=客户端破坏性变更 → 本线只做 reconcile 检测 + provenance 记录（decisions D-05） |
| 附加：非 409 静默回滚 | **已修** | toastRollback（user-mutation.ts:261-275，U-3/#885） |

## 4. 复用 / 扩展 / 不做清单

**复用（不改语义）**：`session_lock_registry`（唯一序列化器）、`engine.apply_mutation` 事务骨架、
`MapSpecStore` 原子落盘+sidecar、`apply_gis_mutation(_batch)` 门面（唯一 provenance/守卫/事件挂点）、
W15 `guard_intent_locks`、`classify_override`、前端 `enqueueUserMutation`、`visibility-transaction`
重试分类、collab bus/adopt revision 门控、`_cartographic_observation` 盖章。

**扩展（本线增量）**：
- envelope：`MutationEnvelope`（mutation_id、producer_class、explicitness、client_optimistic_id、reason、ts）
  挂进门面/引擎/ProvenanceEntry.detail/collab op 事件（additive 字段）；
- precedence：`precedence.py` 阶梯 + 确定性 resolver + fill-undeclared 合并规则（模板语义）；
- 幂等：engine 锁内 mutation_id dedup（`_mutation_dedup` map_state 键，随 commit 单事务落地，有界 64）；
- 生产接线：`mapspec_store` 适配器 mutating 方法透传 actor/producer 并经门面记录 provenance+事件；
- U7：`reconciliation.py` 纯函数（spec vs runtime layers vs pendingRemoved，anomaly codes）接 ws_collab sync；
- U4：前端每笔用户 mutation 携带 client_mutation_id、pending per-op 身份、队列观测（深度/时延）。

**不做（防重复/越界）**：不重写 MapSpec 结构；不重做 layout/symbology 修复算法；不新建第二套
tool loop/store/registry；不把 hover/mousemove 持久化；不用全局进程锁；不迁移 ws_service socket 协议
（客户端破坏性）；不吞 #1270；不与 #1277 kernel 抢 turn/step ledger 语义（它管 harness 运行代，
本线管地图状态事务，字段不重叠）。

## 5. 语义依据（precedence 阶梯落码前的事实）

- 现行 origin 三值：agent|user|system（lifecycle_engine.py:21）；actor 已有生产值：
  `mapspec_route`/`runtime_repair`/`map_finalizer`/`finalize_display`（门面调用点）——
  工具路径 actor 缺失（default "unknown" 不适用，因绕过门面连 provenance 都没有）。
- `presentation_owner` user 印记（lifecycle_engine.py:863）+ `expected_visible` 是 spec 内 durable
  user-wins 权威；ring provenance 是 legacy 兜底。
- W15 三分类（semantic/presentation/transient）+ `strip_transient_state` 是「什么算 mutation」的边界。
- 用户锁（workbench `lockedLayerIds/lockedComponentIds`）是 USER_PINNED 的既有 durable 载体；
  agent/system 整笔拒绝、user 自操作豁免（guard_intent_locks origin=="user" 放行）。
- 因此阶梯落码：USER_PINNED=用户锁/pin 决策面；USER_EXPLICIT=origin=user；AGENT_EXPLICIT=
  agent 工具 authoring（显式挂层/样式）；REPAIR_AUTOFILL=runtime_repair/map_finalizer/finalize_display；
  TEMPLATE=模板/配方路径（apply_template 类 actor）；SYSTEM_DEFAULT=自动骨架/restore/对账。
