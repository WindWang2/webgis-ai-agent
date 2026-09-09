# GeoCompute Cluster Runtime V6 — Data Plane Guide

V6 把 GeoCompute V5 从「具备 capability queue / cancellation / persisted run
evidence 的计算执行层」升级为面向多 coordinator、多租户、长时作业的
**Cluster Runtime**：run 级持久生命周期、lease/epoch fencing、分布式取消、
集群资源账本、公平调度、安全点抢占与 coordinator failover。

前置文档：`v5-data-control-plane.md`（V4 执行面 + V5 异构调度）。
实现主线：`app/services/geocompute/cluster/`（contracts / store / scheduler /
fairness / metrics / workers / errors）。

## 事实源边界（单一真相，不重复）

| 域 | 真相 | 说明 |
|---|---|---|
| run 生命周期 | `geocompute_runs`（V6 新表） | 状态机/lease epoch/心跳/取消与抢占旗标/优先级/attempt 计数/plan 快照（≤256KB） |
| 节点 job | `analysis_tasks`（不动） | V6 没有建第二任务状态机 |
| 终态证据 | `geocompute_run_evidence`（V5） | cluster run 行不复制 snapshot |
| 载荷 | session ref | 载荷绝不进控制面 |
| 集群容量 | `geocompute_workers` + `geocompute_resource_usage` | worker 注册/leadership epoch + 三级资源账本 |

## Run 状态机

```
queued → leased → running → completed|failed|cancelled
  ▲        │        │
  └────────┴─ lease 过期 reclaim（attempts+1，超过预算 → failed[WORKER_LOSS]）
running → preempted → queued（安全点让出；preempts 计数，≠失败）
queued/preempted/leased/running → cancelled（持久取消旗标，任意进程可写）
```

- 状态转移全部是**单语句条件 UPDATE**（CAS；SQLite/PG 同语义），terminal
  无出边；同状态重复转移幂等。
- **fencing**：run 行 `lease_epoch` 每次认领 +1；心跳/终态写路径带 epoch
  CAS —— 旧 coordinator 复活后无法覆盖新 attempt（split-brin 防护）。

## Coordinator（调度者）

- **Leadership**：`geocompute_workers` 行上的 epoch CAS 当选；续约带
  epoch + 「无其他在任者」互斥校验（旧 leader 续约失败即卸任）。
- **tick**（`ClusterCoordinator.tick()`）：acquire/renew leadership（续约
  失败立即卸任，绝不带过期权威做破坏性 sweep）→ reclaim 过期 lease
  （attempt 预算内回队，耗尽 failed[WORKER_LOSS]）→ cancel sweep（排队
  run 的取消旗标直接收敛终态）→ prune 失联 worker → 终态行 retention →
  fairness 派发 → 抢占判定（受害者仅限本 coordinator 的在跑 run）。
- **执行**：认领（含账本 reserve）→ 本地线程 `run_plan_sync` 全链路
  （governor/预算/复用/分类重试不变）→ fenced 终态落库。
- **心跳 watchdog**（每 run 一线程，0.5s）：续 lease；读取消/抢占旗标；
  lease 丢失立即点燃本地取消（停写）—— fencing 失败的本地结果诚实丢弃。
- **抢占**：高优先级等待 ≥ `WEBGIS_CLUSTER_PREEMPT_WAIT_S`（默认 5s）→
  对低优先级在跑 run 写持久 `yield_requested_at`（跨 coordinator 生效）；
  执行侧只在**节点边界**让出（yield_check 安全点），绝不强杀线程。
- **公平**：加权轮转，排序键 `(tenant_last_dispatch, -priority, id)`；
  状态可由 `MAX(dispatch_seq) GROUP BY tenant` 重建 —— 无隐藏内存态；
  突发共享 + starvation-free。
- **通道匹配**：durable 节点推导必需 profile；无存活 worker 覆盖该通道的
  run 留队（队头阻塞削减）；eager 模式恒匹配（诚实：本地执行）。

## REST（全部 additive；tags `GeoCompute / Cluster Runtime V6`）

| 端点 | 语义 |
|---|---|
| `POST /geocompute/plans/runs` | 202 提交（auth + session 归属校验）；413 plan 快照超限；429 租户/全局背压；422 契约非法 |
| `GET /geocompute/runs?status=&limit=&offset=` | 我的 runs：cluster 行 ∪ 终态快照（去重，行域优先） |
| `GET /geocompute/runs/{id}` | 三级回退：内存注册表 → 证据快照 → cluster 行活投影（`source` 诚实标注） |
| `POST /plans/runs/{id}/cancel` | 两级幂等：本地 token + 持久旗标（跨进程生效） |
| `GET /geocompute/cluster/metrics` | require_admin；封闭维度（status×7/priority×3/role×2/profile×6），账本 ≤20 scope，取消延迟样本 ≤128 |

同步 `POST /plans/execute` 行为不变（V6 起它同样写入证据快照域，
`run_evidence` 默认工厂修复后生产路径首次真实生效）。

## 环境变量

| 变量 | 默认 | 语义 |
|---|---|---|
| `WEBGIS_CLUSTER_COORDINATOR` | 未设（关） | `=1` 时 API 进程 lifespan 启动 coordinator |
| `WEBGIS_COORDINATOR_SLOTS` | 2 | 本进程并发执行的 run 数（防 OOM 第一道闸） |
| `WEBGIS_CLUSTER_LEDGER_ENFORCING` | 未设（advisory） | `=1` 集群账本强制准入（显式部署决策） |
| `WEBGIS_WORKER_PROFILE_SLOTS` | 全 profile ×1 | celery worker 的 profile 槽位 JSON |
| `WEBGIS_CLUSTER_PREEMPT_WAIT_S` | 5.0 | 高优先级等待多久才允许发起抢占 |
| `WEBGIS_CLUSTER_RUN_RETENTION_H` | 24 | 终态 run 行保留时长（小时；证据快照不受影响） |

## 与 V5 缺陷的收敛

- P0-1（run 注册表/取消 process-local）→ 持久 run 行 + 持久取消旗标 ✅
- P0-2（无持久 run 行、不可恢复）→ plan 快照 + lease 恢复 ✅
- P0-3（worker 绕过 plan 预算）→ budget 穿透 task_kwargs ✅
- P0-4（跨进程治理 fail-open 无权威）→ 集群账本（advisory 默认 / enforcing 显式）✅
- P1-6（无 run 列表）→ `GET /runs` ✅
- P1-2（50ms 忙轮询占请求线程）→ cluster 提交即返回，轮询移到 coordinator 工作线程（有界）✅
- 既有 P1 bug：`run_evidence`/`reuse_index`/cluster store 默认 session 工厂返回
  sessionmaker（2.0 无上下文协议）→ 生产默认路径证据/复用记录被 fail-open 静默丢弃 —— 已修复。

## Known limitations（诚实边界）

1. **eager 悬崖仍在**：无 Redis 时 durable 节点仍在本地同步执行
   （`backend_variant="in_process_eager"`）；集群控制面（run 生命周期/
   账本/调度）不依赖 broker，已真实生效。
2. **lease 丢失后的短暂重叠执行**：分区 coordinator 在本地取消生效前可能
   与新 attempt 并行 —— 节点级幂等键 + 缓存/证据幂等覆盖使后果有界
   （与 Celery visibility timeout 同级取舍）。
3. **PG 双当选极窄窗口**：READ COMMITTED 下两个同时首到场的 coordinator
   可能短暂双 leader —— 所有写路径另有 run 级 epoch CAS，无状态损坏，
   最坏是重复 reclaim 尝试被 CAS 拒绝。
4. **_async_bridge 进程级串行锁**未动（ADR-0096 Deferred，V7 候选）。
5. **trace 仍 process-local**（1024 条 ring）；控制面事件走结构化日志。
6. **多机时钟偏移**：lease TTL 的写与过期判定用各进程本机时钟
   （naive UTC）—— 偏移直接换算成误 reclaim/迟 reclaim；与 Celery
   visibility timeout 同级取舍，部署需 NTP。
7. **抢占的等待时间**从提交时刻（created_at）起算；被抢占 run 的已完成
   durable 节点经复用索引减损，进程内节点会重算。
