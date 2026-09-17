# DECISIONS — 关键决策（ADR 风格）

## D1 模块名与边界：`app/services/spatial_events/`
单向依赖（见 PARALLEL_OWNERSHIP），无既有模块反向 import（除 hook 点/main 注册）。
备选 `event_runtime` 弃用：与任务书术语 SpatialEvent 对齐，避免与 workflow_runtime 混淆。

## D2 存储：SQLAlchemy 行表（不是 Redis、不是 collab bus 扩展）
- 需求是**持久、可回放、去重、重启恢复**——Redis pub/sub 丢消息即丢；collab bus 明示
  "通知≠真相"。DB 行表 + 自增 id 因果序与 `workflow_events` 同构（有先例可循）。
- 同步 store（自传 sessionmaker factory，仿 MissionStore）——事件面是控制平面，
  与 mission store 同风格、可 hermetic sqlite 测试。

## D3 event_id 幂等键：producer 优先，缺省确定性派生
`event_id = provided_key or sha256(source|kind|subject_key|occurred_at|payload_hash)[:32]`。
重复投递 → unique 冲突 → 记 `duplicate` ack，**零副作用**。双层防护：ledger 去重 +
watch fire 表 (watch_id,event_id) UQ + mission create 带 idempotency key。

## D4 处理模型：DB pending 队列 + 单 worker task（每进程一个，DB CAS 抢批）
- 事件状态机：`pending → processing → processed`（+ `duplicate/coalesced/failed/skipped`）。
- worker 用条件更新抢批（`WHERE status='pending'` → processing + claimed_by/at），多副本安全；
  崩溃恢复清扫把 stale processing 复位 pending（仿 workflow recovery orphan reset）。
- at-least-once + 副作用幂等（D3）→ 重启不丢不双跑。
- ingest 后 `asyncio.Event` 唤醒 worker（低延迟），无事件时按 env 间隔轮询兜底。

## D5 Backpressure：三级
1. **Coalesce**：drain 前对 pending 中同 (org,kind,subject_key) 只留最新 occurred_at，
   其余标 `coalesced`（计数入 survivor.collapsed_count）——storm 下 O(subject) 而非 O(events)。
2. **Bounded batch + per-org round-robin + priority**：单批 ≤env（默认 32），org 轮转防垄断，
   priority（interactive>normal>batch）排序。
3. **Governor gate**：trigger 类副作用执行前经 `governor_gate`（BackpressureManager）准入；
   defer/reject → 事件回 pending（next_attempt_at 退避，attempts 有界）→ burst 收敛且不丢。
   gate fail-open（governor 本身 fail-open；kill-switch `GIS_SPATIAL_EVENT_GOVERNOR_GATE`）。

## D6 Kill-switch 分层（默认全 OFF，保证 Oracle-6）
- `GIS_SPATIAL_EVENT_RUNTIME`（总开关，默认 0）：ingest/ledger/watch/worker/adapters 全 no-op。
- `GIS_SPATIAL_EVENT_MISSION_BRIDGE`（默认 0）：watch fire 才允许触发 mission 变更。
- `GIS_SPATIAL_EVENT_INVALIDATION`（默认 0）：才允许 apply_changes/evidence 失效副作用。
- `GIS_SPATIAL_EVENT_GOVERNOR_GATE`（默认 0）：governor 准入门。
- API/诊断路由**始终注册**（disabled 时返回明确 disabled 状态——诚实可观测），需刷新 openapi 快照。
理由：与 `GIS_MISSION_HOTPATH` 默认 OFF 同纪律；控制平面先以观测模式灰度。

## D7 Situation 投影：独立 ring + compiler 第 6 源（flag-gated）
- `situation_projection.py` 写 session store `map_state["_spatial_event_facts"]` 有界环（≤32，
  持 session 锁 RMW，仿 `advance_snapshot`）；提供 `projected_facts(session_id)` 读 API。
- compiler `_gather_sources` 增加第 6 guarded 源（3s 超时 fail-open）；ring 空/flag off 时
  编译输出与 master **字节等价**（空源不产生 fact）。
- Mission 决策**只读投影后的事实**（`MissionTriggerPolicy.evaluate(facts, watch)`），
  不直接读原始事件 payload——满足"先投影再决策"红线。

## D8 Webhook：安全 seam only
`POST /api/v1/spatial-events/webhook`：HMAC-SHA256 验签（env 密钥，缺密钥=禁用路由）+
kind 白名单 + inline payload ≤2KB（超限必须 ref）+ 每租户速率限制。webhook 事件
`source="webhook"`，同样进 ledger 走全链路（无特权）。

## D9 Portfolio：纯读投影，零新表
SQL 聚合 `gis_missions`（by project/state）×`workflow_instances`×`spatial_events`（最近活动），
响应有界（≤200 missions、events ≤50/project）。绝不物化第二状态真相。

## D10 Replay：诊断语义
`POST /spatial-events/replay {from_id,to_id,dry_run,force}`：按 ledger 顺序重投；
非 force 时已 processed 事件跳过（dedupe 语义），dry_run 只报告会触发什么。
replay 不改 occurred_at、不破坏 cursor（独立 consumer name）。

## D11 SSE timeline
`GET /spatial-events/stream`：org 域 tail（最近 N=128 环形 backlog + 心跳 15s + 客户端断开清理），
服务端只推投影摘要（event_id/kind/subject/status），payload 不出网（ref 纪律）。

## D12 与 #1335/#1336 兼容
- mission_runtime 只调不改（#1335 在改 service/store）。
- main.py/conftest.py 加法触碰不同区域；PR 前再 fetch 若 #1335/#1336 已合入则 rebase 消解。
