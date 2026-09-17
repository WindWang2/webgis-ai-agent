# GAP_ANALYSIS — 目标能力 vs 现状

| # | 目标能力 | 现状 | Gap | 本分支方案 |
|---|---|---|---|---|
| G1 | versioned SpatialEventEnvelope，大 payload 只传 ref | 无任何事件契约 | 全缺 | `spatial_events/contracts.py`：schema v1、closed kind 词表、inline payload ≤2KB、payload_ref、idempotency event_id |
| G2 | durable event ledger/cursor（重复/乱序/重放/重启） | session event_log 仅 20 条环形易失；workflow_events 只服务节点转移 | 全缺 | `spatial_events` 表（自增 id=因果序、event_id UQ 去重、status CAS）+ `spatial_event_cursors` + 处理中崩溃恢复清扫（仿 workflow orphan reset） |
| G3 | 内部事件 adapter（revision/job/map mutation） | 零接线（见 CURRENT_ARCHITECTURE §2） | 全缺 | E1–E4 fail-open hooks + E5 webhook 安全 seam（HMAC+白名单） |
| G4 | SpatialWatch/TriggerRule（AOI/阈值/变化幅度/时间窗/连续N/进出AOI/版本变化） | 无 | 全缺 | `watch.py` 纯函数谓词 + `spatial_watches`/`spatial_watch_fires` 表 + cooldown；shapely AOI |
| G5 | event→Situation 投影 | situation 只在 turn 边编译；观察仅前端交互 8 kind | 无事件事实通道 | `situation_projection.py`：session store 有界 ring `_spatial_event_facts`（持锁 RMW，仿 advance_snapshot）+ compiler 第 6 源（flag-gated） |
| G6 | event→Mission create/revise/resume | mission 仅由 turn/swarm 委托驱动 | 全缺 | `mission_bridge.py`：TriggerAction → MissionRuntimeService 公开 API（复用 lease/fencing）；fire 幂等（watch+event UQ）；org 断言 |
| G7 | affected-subgraph 增量重算 | apply_changes/data_hook 机制存在但只有 API 手动触发；evidence 失效 API 零调用 | 接线缺 | `invalidation_bridge.py`：dataset/artifact 版本事件 → 实例 PendingChange(data) → STALE 仅受影响子图；同事件 → ClaimStore invalidate_affected_claims（首次生产接线） |
| G8 | backpressure/coalesce/priority/tenant fairness + governor 背压 | governor 只挂 tool dispatch；无事件面 | 接线缺 | worker drain：有界批 + per-org round-robin + priority；burst 时同 (kind,subject) coalesce；`governor_gate.py` 用 BackpressureManager 准入 trigger 副作用（ENFORCE 可测） |
| G9 | Project/Mission portfolio read model | 前后端均无 portfolio | 全缺 | `portfolio.py` 纯查询投影（missions×instances×events），无新状态真相 |
| G10 | bounded API/SSE diagnostics + timeline + replay | 无 | 全缺 | REST（org 域分页/cursor）+ SSE tail（bounded backlog+心跳）+ replay（dedupe 下 dry-run/执行） |
| G11 | 故障注入验证 | — | — | dup/out-of-order/worker restart/stale lease/burst/cancel/cross-tenant 测试矩阵 |

## 复用优先（避免重复施工）

- 增量失效数学：`gis_harness/workflow_v4/recompute.compute_affected_subgraph`（经 apply_changes 间接复用）
- 幂等/租约/fencing：mission store 既有机制（bridge 只传参）
- 租户谓词：`scoped_query` + 显式 org_id 参数（store 级）
- 公平/背压数学：governor BackpressureManager（只调不改）
