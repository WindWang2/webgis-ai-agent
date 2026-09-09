# GeoCompute V7 — Phase A 基线审计（2026-09-09）

- base：origin/master `8a33e3a5`；migration head：`0033_geocompute_v6_cluster`（34 个迁移，单 head）
- 最新 PR：#1172 quality-v2（已合并）；与本 Epic 同域：#1163（Cluster Runtime V6）、#1164（Lakehouse V6）、#1166（Query V6）、#1165（Harness V5）
- open issues 无本域重叠项（#1107-#1113 均已 CLOSED）
- 本地 real-services：Redis 可达（PONG）→ broker E2E 可真实验证；Postgres 不可达（E2E 用 SQLite 文件库）

## 1. 当前真实生产入口（V6 状态）

| 入口 | 路径 | 证据 |
|---|---|---|
| 同步执行 | `POST /geocompute/plans/execute` → `run_plan_sync` → `engine.execute_plan` | app/api/routes/geocompute.py:203 |
| cluster 提交 | `POST /geocompute/plans/runs`（202）→ `geocompute_runs` 行 | app/api/routes/geocompute.py:245 |
| coordinator | `ClusterCoordinator.tick()`（leadership epoch 选举，opt-in `WEBGIS_CLUSTER_COORDINATOR=1`） | app/main.py:171-190, cluster/scheduler.py:137 |
| 节点 broker 派发 | durable 节点 → `submit_durable_job` → Celery `run_geocompute_node`（profile 队列） | durable.py:158-213, tasks.py:29 |
| 取消 | REST cancel → 持久旗标 + 内存 token 两级 | routes/geocompute.py:463 |

## 2. 事实源清单（无新增第二真相为前提）

- run 生命周期：`geocompute_runs`（db_model.py:343，CAS/epoch/lease/旗标）
- 节点 job：`analysis_tasks`（durable job 状态机，jobs/store.py）
- 终态证据：`geocompute_run_evidence`；节点复用：`geocompute_node_results`（reuse_index.py）
- 载荷：session ref（session_data_manager；`_async_bridge.run_coro_sync` 桥接）
- DataObject 身份：lakehouse content-addressed manifest（data_object.py:241 publish / :354 resolve / :400 materialize，owner 校验 :102）
- 账本：`geocompute_resource_usage`（条件 UPDATE 记账，store.py:893）
- worker/coordinator 注册：`geocompute_workers`（profiles JSON + 心跳，workers.py:98）

## 3. 与 V7 Must-have 的差距（分级）

### P1（本 Epic 核心缺口）

- **G1 Worker Capability Profile 缺失**：worker 只注册 `{profile: slots}`（workers.py:35）。
  无 CPU/内存/GPU/backend/版本指纹/健康。`geocompute_workers.info` 是自由 JSON（≤16 项 str），
  无类型契约。→ wave 4-5。
- **G2 Placement 只做 profile 集合包含**（fairness.py:59 `matches_profiles`）+ 租户轮转。
  无资源 envelope、GPU/CPU 区分、内存 guard、locality。→ wave 6-7。
- **G3 Data Locality / worker cache 为零**：无 DataObject 内容位置感知，无 worker 侧缓存
  注册表。`node.locality_hint` 只映射到队列名（durable.py:103）。→ wave 7-8。
- **G4 分布式 trace 无跨进程聚合**：tracing.py 是进程内 1024 环形缓冲；worker 进程的
  节点事件对 coordinator/用户不可见（run 行只有 run 级状态）。→ wave 15-16。
- **G5 Straggler/worker loss 只覆盖 run 级**：`reclaim_expired` 管 run lease；durable 节点
  stale sweep 300s（jobs 层）但无 straggler 事件、无「worker 死后 output-before-ACK」的
  显式对账测试。→ wave 17-19。
- **G6 Real broker E2E 缺席**：real-services lane 只有 toy app + `run_heatmap_generation`
  单任务往返（test_real_services_smoke.py:354）；geocompute coordinator↔worker 全链路
  （dispatch/progress/cancel/retry/crash/reconnect/duplicate/visibility）无真实 broker 测试。→ wave 2-3。
- **G7 Admin 可观察性不足**：metrics 无 queue-wait 分布、无 waiting_by_profile、无 stuck-run
  视图、无 reset/reclaim 动作、无 ledger limit 管理（enforcing 模式 limit 只能改代码）。→ wave 22。

### P2（性能/资源，结构性修复）

- **B1 `_SERIAL` 全局锁（已记录瓶颈，wave H）**：`_async_bridge.py:23` 进程级
  `threading.Lock` 包住整个协程执行 —— coordinator 上 N 个并发 durable 节点的载荷回取
  （`await_node_job` → `run_coro_sync(session_data_manager.get)`，durable.py:294）完全串行。
  修复：单一专用 event-loop 线程 + `run_coroutine_threadsafe`（保留单循环语义，
  消除跨循环 asyncio.Lock 争用 + 线程级串行）。→ wave 21 + 结构基准。
- **B2 durable 节点轮询 50ms 固定间隔**：`await_node_job`（durable.py:30 `_POLL_INTERVAL_S=0.05`）
  每在飞节点 20 DB qps；100 节点并发 = 2000 qps 纯轮询。修复：自适应退避 0.05→0.5s
  （取消传播延迟上界仍受 run 心跳 0.5s 主导，不劣化）。→ wave 3。
- **B3 无 queue-wait 观测**：created_at→leased 延迟无采样。→ wave 22（metrics）。

### P3（正确性细节，随 wave 顺手收口）

- **M1 worker 容量可见性窗口不一致**：prune cutoff=180s（store.py:751）> live_workers
  within_s=90s（store.py:760）> worker TTL=30s（workers.py:30）。心跳停 30-90s 的 worker
  仍被 `_available_profiles` 认为存活 → 派发到死通道（run 留队等 reclaim，无损坏但有
  假容量窗口）。→ capability wave 统一为 TTL 派生。
- **M2 `upsert_worker` SELECT-then-write**：并发首注册可能 IntegrityError（心跳退避重试
  兜底，无损坏）；capability wave 改为单语句 upsert 语义（SQLite ON CONFLICT / 手术条件
  UPDATE + 重试）。
- **M3 eager 降级可见性已有**（backend_variant="in_process_eager"，durable.py:211）——
  保持，不虚报 distributed。

## 4. 测试有效性评估

- V6 契约/调度/store/chaos 测试（tests/test_geocompute_v6_*.py，~9 文件）全部 eager/in-process：
  状态机、CAS、fencing、公平性证明真实（真 DB 行为）；但 **broker 路径从未被真实验证**
  （G6）。contract-only 的部分：`queue_for_node` 路由与真实 worker `-Q` 消费集合的
  一致性只有 compose 注释约束（task_queue.py:25）。
- 生产 consumer 链完整的路径：submit→coordinator→run_plan_sync→durable dispatch→worker
  task→session ref→await→evidence —— 每环有独立测试，**全链无组装测试**（仅 eager）。

## 5. 资源复杂度（现状）

- coordinator tick：O(batch) 查询常数上界（V6 perf 测试 B1 已锁：5 queued=16q / 200 queued=2q）
- `await_node_job`：O(in-flight durable nodes) × 20 qps（B2）
- `_SERIAL`：串行度 1（B1）
- trace ring：1024 条/进程（有界）；events 落库后 per-run 上界待引入
- NodeResultStore：256 条 / 128MB LRU（有界）；reuse_index per-owner ≤ 上界（_prune_owner）

## 6. 失败/取消/恢复语义现状（确定）

- 双 coordinator：leadership epoch CAS + run lease epoch CAS（旧者写全部失败）
- coordinator 崩溃：lease 过期 reclaim（attempt++ ≤3 → WORKER_LOSS）
- worker 崩溃：durable stale sweep（300s）→ NodeExecutionError[WORKER_LOSS]（retry_safe=True）
- 取消：持久旗标（任意进程写）→ 心跳 0.5s 点燃本地 token → durable request_cancel_sync 级联
- 幂等：节点幂等键（params 摘要）+ durable 状态机守卫 + reuse/证据表幂等覆盖

## 7. 共享文件冲突风险（与其他并发 Epic）

- `app/models/db_model.py`（追加类/列：additive，冲突面小）
- `migrations/versions/`（新迁移 0034 down_revision=0033；合并时检查 head）
- `app/api/routes/geocompute.py`（V7 端点 additive 追加）
- `app/services/task_queue.py`（不动，除非新增队列词表 —— 本 Epic 不加队列）
- CHANGELOG.md（最小追加一条）；ADR 编号用 **0119**（当前最大 0118）
- OpenAPI snapshot：新增端点后按 repo 惯例再生成

## 8. 结论

V6 的「集群知道谁该执行什么」已成立（run 级 CAS 控制面 + eager 全覆盖测试）。
V7 纵向深化 = 能力/放置/局部性/分布式证据四条主线 + real-broker 诚实验证，
全部 additive 于既有事实源，无第二状态机。
