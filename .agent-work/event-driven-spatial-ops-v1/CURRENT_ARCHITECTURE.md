# CURRENT_ARCHITECTURE — 真实生产调用图与事件插入点

（来源：4 路并行深读 + 主 agent 亲自核对关键 API；file:line 基于 baseline faa453a8）

## 1. 现状调用图（事件视角）

```
用户 turn ──► chat.py ──► agent_pi_bridge (Pi 子进程 RPC)
                            │ turn 生命周期（_mint_turn_id / _active_turns / Redis pi_turn_registry）
                            ▼
                 harness_kernel runtime（begin_turn/end_turn）
                            │
        ┌───────────────────┼──────────────────────────┐
        ▼                   ▼                          ▼
  gis_situation       session_plan               agent_swarm 委托
  (compile_situation   (MapProductPlan 章节)      (orchestrator→workflow_runtime.machine)
   5 源 fanout/turn)        │                          │
        │                   │ hotpath_convergence      │ settle
        │                   │ (mission_bind 默认 OFF)  ▼
        │                   ▼                   DurableSwarmBridge
        │             workflow_runtime          (gis_mission_swarm_runs 账本)
        │             hooks *_safe (fail-open)
        │                   │ apply_changes → ChangeApplier → STALE 标记
        │                   │ driver._run_loop → 节点租约执行
        ▼                   ▼
  session store        workflow_instances/_nodes/_events(journal)
  (map_state/refs/
   event_log 20条/
   _situation_snapshot)

MapSpec mutation ──► gis_world_state.apply_gis_mutation ──► CAS revision + _gis_provenance 环(64)
                            │ 成功后
                            ▼
                     collab.bus.publish(doc/delta/presentation/op, seq=revision)
                     （Redis per-session 频道 → 浏览器 WS；唯一服务端→前端事件面）

Artifact 晋升 ──► artifact_revisions.record_revision（幂等，(artifact_id, content_sha256) 复用）
                   + lineage_service.record_lineage（artifact_lineages 边表）
                   + artifact_registry（session 内存台账 valid/superseded/stale/expired，sweep 巡检）

Job/Simulation ──► jobs submit_durable_job → Celery worker → durable_job() 上下文
                   → finish_job → analysis_tasks 终态（完成感知=前端 3s 轮询，无事件）

MapProduct ──► map_product_service.record_version（五维 diff：data_changed 只记录不传播）

Governor ──► tool_dispatch_service 唯一强集成（admit_and_reserve fail-open；
             BackpressureManager 6 通道 + FairScheduler per-session 加权公平）
```

## 2. 已确认的"零接线"缺口（Gap 证据）

| 缺口 | 证据 |
|---|---|
| 无统一 SpatialEvent/Watch/Subscription/Trigger runtime | 全仓 grep 无 spatial_event/watch/subscription 模块 |
| evidence_claim 失效 API 零调用 | `invalidate_affected_claims`/`mark_evidence_stale`/`affected_descendants`（freshness.py:17-140）只有包内引用 |
| dataset/artifact 版本变化无失效传播 | versioning_gate drift 只进 DriftReport；MapProduct `data_changed` 只进 diff_summary |
| job/simulation 完成无事件 | 完成感知 = 前端轮询 `poll_after_ms=3000` |
| Mission 无事件 journal | `gis_missions` 行级状态，无伴随事件表 |
| collab bus 只有浏览器消费者 | 订阅方仅 ws_collab.py:320 |
| ClaimStore 进程内 per-session | hotpath_convergence/session_ctx.py:61（重启即失，多副本不共享） |

## 3. 事件插入点（本分支落地位置）

| # | 插入点 | 事件 | 方式 |
|---|---|---|---|
| E1 | `gis_world_state/mutation.py` 成功 mutation 发布点（与 bus.publish 同区） | `map.mutation_applied` | fail-open hook（flag-gated） |
| E2 | `artifact_revisions.record_revision` 返回 `(row, created=True)` 后 | `artifact.revision_committed` | fail-open hook（调用方事务提交后由 hook 自行短会话落账，避免嵌套事务污染） |
| E3 | `jobs/worker.py finish_job` 终态返回后 | `job.completed/failed/cancelled` | fail-open hook |
| E4 | `map_product_service.record_version` 成功后 | `mapproduct.version_recorded`（data_changed 标志） | fail-open hook |
| E5 | 外部 webhook | 任意（source=webhook） | 新 API route，HMAC 验签 + 白名单 + payload≤ref 纪律 |

## 4. 复用面（禁止重复施工 → 全部走 adapter/projection）

- Mission create/revise/resume：`MissionRuntimeService`（service.py:38/133/99）——**只调用**。
- CAS/lease/fencing：mission store lease_epoch/revision（不改）。
- 受影响子图：`WorkflowRuntimeService.apply_changes(instance_id, [PendingChange(dimension="data", source="data_hook")])`
  → `ChangeApplier` → `compute_affected_subgraph`（不改其内核）。
- Evidence 失效：`evidence_claim.freshness.invalidate_affected_claims`（首次生产接线，不改其内核）。
- 背压/公平：governor `BackpressureManager`/`admit_and_reserve` 公开 API（不改 governor）。
- 租户：`OrgContext`/`scoped_query`（app/core/tenancy.py:158/179）。
- 持久化：SQLAlchemy `Base`（app/core/database.py:9）+ alembic `00NN_` 迁移。
- SSE/WS：新增独立 bounded SSE 端点（复用 chat SSE 的 StreamingResponse 模式）。
