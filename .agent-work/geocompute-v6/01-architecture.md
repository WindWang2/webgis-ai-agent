# GeoCompute Cluster Runtime V6 — 架构（Phase B）

## 设计原则

1. **单一事实源不破坏**：run 生命周期真相 = 新表 `geocompute_runs`（此前 run 无持久行，
   是新事实域不是第二真相）；节点 job 真相仍 = `analysis_tasks`；终态证据仍 =
   `geocompute_run_evidence`（cluster run 行不复制 snapshot，只引用）；载荷仍 = session ref。
2. **穿透而非另造**：durable 节点派发仍走 `submit_durable_job`（幂等键）；取消仍两段式；
   复用仍 reuse_index；执行仍 `GeoExecutionEngine.execute_plan`。V6 加的是 run 级控制平面。
3. **SQL 可移植 CAS**：所有并发控制用单语句条件 UPDATE（SQLite/PG 同语义），无 SELECT-then-UPDATE。
4. **默认关闭、显式开启**：cluster 提交端点与 coordinator 循环是 additive；`/plans/execute`
   同步路径行为不变（同时受益于 run 行持久化：run 行写入对两条路径一致开启）。

## 组件图（target state）

```
REST /plans/runs (submit, 202) ──┐
REST /plans/runs/{id}/cancel ────┤ write
REST /runs (list) ───────────────┘   │
                                     ▼
                    ┌── geocompute_runs（生命周期真相：status/lease/epoch/
                    │   heartbeat/cancel_requested/attempts/priority/plan JSON）
                    ├── geocompute_workers（coordinator + celery worker 注册/心跳）
                    └── geocompute_resource_usage（tenant/project/global 账本，条件更新）
                                     ▲ read/CAS
  ClusterCoordinator（每进程可选启动）:
    tick:
      1. leadership CAS（coordinator lease epoch）
      2. reclaim_expired_leases：过期 lease → CAS 回 queued（attempt++）或 terminal(FAILED/WORKER_LOSS)
         + 账本精确释放（同一事务，CAS 保证 exactly-once）
      3. cancel sweep：cancel_requested_at 非空 → 本地 token 点燃 + durable job 级联（既有）
      4. fairness pick：加权轮转（(tenant_last_dispatch, -priority, seq)），租户/项目准入
         （resource_usage 条件 UPDATE + 背压上限）
      5. lease & execute：CAS(queued→leased, epoch=+1) → 本地线程 run_plan_sync
         （run_id 预置 = run 行 PK；yield_check 接线抢占；心跳线程 0.5s）
      6. terminal persist：CAS(leased/running→terminal, expect_epoch) → 证据快照（既有）
```

## 状态机（run 级）

```
queued → leased → running → completed|failed|cancelled
   ▲        │        │
   │        │        └→ preempted → queued（attempt++，≤ max_attempts）
   └────────┴─ lease 过期 reclaim（attempt++，超过 max_attempts → failed[WORKER_LOSS]）
cancel：queued/leased/running →（cancel_requested_at 落库）→ cancelled
terminal 状态出边 = 无（CAS 拒绝）；同状态重复转移 = 幂等 no-op。
```

`ExecutionRunStatus` additive 成员 `PREEMPTED = "preempted"`（DB 列 String(20)，无约束冲突）。

## Fencing / epoch 语义

- run 行 `lease_epoch` 从 1 起每次 lease 递增；所有状态转移带 `expect_epoch`；
  旧 coordinator 复活后 CAS(epoch 不匹配) 失败 → 无法覆盖新 attempt（split-brain 防护）。
- coordinator leadership 同机制（geocompute_workers 行 role=coordinator）。
- durable 节点僵尸提交防护 = 幂等键（既有）+ 缓存/证据表幂等覆盖 + run 级 epoch CAS；
  节点不可逆副作用前的 fence 检查经 `job.ensure_not_cancelled()`（既有）。

## 公平与准入

- **加权轮转（DRR 简化）**：pick 排序键 `(tenant_last_dispatch_seq, -priority, seq)`；
  状态全部可从 runs 表重建（`MAX(dispatch_seq) GROUP BY tenant`），coordinator 无隐藏内存态。
- 突发共享：轮转只约束次序不约束总量 → 空闲容量被自然借用；轮转保证同租户 starvation-free。
- 背压：`max_queued_per_tenant`（默认 32）超限 → 429 typed；全局 queued 上限（默认 256）。
- 账本：tenant/project/global 三级，reserve=条件 UPDATE（usage+Δ≤limit），release=MAX(0,usage-Δ)；
  模式 `enforcing`（拒绝超限）/`advisory`（超限记 metric 放行，默认 advisory —— 与既有
  resource_counter 语义一致；enforcing 是部署级显式决策）。崩溃清理 = reclaim 时按 run 行
  `reserved_*` 列精确归还（与状态转移同事务）。

## Worker 能力与通道

- `geocompute_workers`：worker_id、role（coordinator|worker）、profiles JSON
  （{light_cpu: n, heavy_cpu: n, ...}）、heartbeat_at、started_at、lease_epoch。
- Celery worker 经 celery signals（worker_ready/worker_shutdown）+ 心跳 daemon 线程注册；
  profile 槽位 env 配置（WEBGIS_WORKER_PROFILE_SLOTS，默认每 profile 1）。
- coordinator 本地执行容量 = `WEBGIS_COORDINATOR_SLOTS`（默认 2）—— 防 OOM 的第一道闸；
  账本并发单位（重节点 2 单位，复用 slot_units_for 语义）是第二道。

## 取消路径（分布式）

```
任意进程 REST cancel → CAS 写 cancel_requested_at（幂等；owner 校验后）
  → 执行进程心跳线程（0.5s）读到 → 本地 CancellationToken.cancel（未启动节点收敛）
  → durable 节点：await 循环既有 request_cancel_sync 级联 → worker watchdog → checkpoint
  → 终态 cancelled（CAS expect_epoch）
延迟 = 心跳间隔 0.5s + checkpoint 协作粒度；metrics 记录 request→terminal 延迟。
```

## 抢占

- priority ∈ {low=0, normal=5, high=10}（submit 可选；默认 normal）。
- 抢占判定：高优先级 run 在 queued 等待 ≥ `preempt_wait_s`（默认 5s）且容量满 →
  选 `running` 的最低优先级 run（同优先级不抢占）。
- 执行侧 safe point：engine 调度循环每轮调 `yield_check()`（新可选参数）→ True 时
  协作收敛在飞节点（checkpoint）→ 终态 `preempted` → 回 queued（attempt++，retry affinity
  经复用索引成立：已完成 durable 节点重跑即命中）。
- 非抢占段：节点在飞不可强杀（线程约束，既有）；抢占只发生在节点边界收敛语义下。

## Retry / recovery

- coordinator 崩溃 → lease 过期 reclaim → attempt++ 回 queued（transient，WORKER_LOSS 类）；
  attempt > max_run_attempts（默认 3）→ failed（诚实 WORKER_LOSS）。
- checkpoint-aware resume：重执行经 reuse_index / NodeResultStore 命中已完成节点（既有，
  新增 cluster 路径行为测试）。
- 幂等：durable 节点幂等键防重复 job；账本 release 与状态转移同事务防重复归还。

## 可观察性（有界维度）

`cluster.metrics()`：{queued, leased, running, preempted}×{}、queue_depth_by_priority、
lease_age_max_s、reclaim_total、retry_total、preemption_total、cancel_latency（P50 样本 ≤128）、
workers_by_role、ledger_usage（scope 维度 ≤ 全局+租户数上界，只暴露前 N）。
**维度词表封闭**（status/priority/role/profile），绝无 per-run/per-user 基数。
REST `GET /geocompute/cluster/metrics`（require_admin）。

## 性能预算（结构性，非脆弱墙钟）

- tick 成本 O(候选批量)（LIMIT 批查询），与总 run 数无关（断言：200 queued runs 下单 tick
  查询数为常数带上界）。
- pick 决策内存计算 O(queued batch)；
- dispatch overhead：submit→首次 lease 的 tick 数 ≤ 配置间隔×2（结构断言）；
- 内存：coordinator 无未bounded集合（futures dict ≤ slots）；
- 差异基准：bench 脚本对比 submit/lease/terminal 路径与 V5 同步路径的形状（INFO 记录不 gate）。

## 安全边界

- submit/cancel/list：与 execute 同一 authz（get_current_user + owner_scope_for 读隔离 +
  session 写归属校验）；metrics：require_admin。
- plan JSON 落库上界 ≤256KB（typed 413 拒绝）——防止 DB 行膨胀 DoS。
- owner 域哈希（既有 owner_scope_for）；plan 参数经 redaction 证据路径不受影响（载荷不出平面）。

## 失败模式与降级

| 故障 | 行为 |
|---|---|
| coordinator 崩溃 | lease 过期 → 其他 coordinator reclaim（attempt++）|
| 双 coordinator（脑裂） | leadership/lease epoch CAS，旧者写失败（fencing）|
| DB 瞬断 | tick 异常计数退避（不 crash）；advisory 账本放行记 metric；enforcing 拒绝（fail-closed 有界）|
| worker 心跳丢失 | 既有 jobs stale sweep（300s）+ V6 worker prune |
| cancel 竞态 | CAS 幂等；终态后 cancel = no-op 200 |
| artifact 写失败 | 既有类型化失败 + run failed；无重复副作用（幂等键）|

## 与并行 Epic 的接口边界

- 不改 `analysis_tasks` schema；不改 data_fabric/science/workflow；
- 新表仅 geocompute 前缀；migration 0032 独立 additive（0031 之上，无并发 head）；
- OpenAPI snapshot 只做本任务端点的最小再生成；CHANGELOG 一条目。
