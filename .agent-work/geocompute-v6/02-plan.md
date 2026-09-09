# GeoCompute Cluster Runtime V6 — 实现计划（Phase C waves）

每个 wave：先测试/契约 → 最小闭环 → targeted tests → 静态检查 → 小步 commit。

| Wave | 内容 | 主要文件 | 验收 |
|---|---|---|---|
| 1 | Cluster 契约：ClusterRunStatus 状态机（含 preempted）、RunLease/epoch、WorkerCapability、ResourceClaim、转移表（幂等/终态出边禁绝） | `cluster/contracts.py`、plan.py additive | 契约单测：转移表完整性、幂等重复、终态拒绝 |
| 2 | 持久存储：3 张表（runs/workers/usage）+ migration 0032 + CAS store | `cluster/store.py`、`app/models/db_model.py`、`migrations/versions/0032_*` | CAS 争用/幂等终态/cancel 旗标/重入迁移测试 |
| 3 | Durable scheduler：leadership、tick、lease→执行→terminal、重启恢复 | `cluster/scheduler.py`、executor.py（run_id 注入 + yield_check + PREEMPTED） | 双 coordinator 无双重执行；kill/recover 测试 |
| 4 | 分布式取消：任意进程 cancel 落库 → 心跳线程 → token → durable 级联；cancel 竞态 | scheduler/store、routes | 取消延迟有界、终态幂等、竞态测试 |
| 5 | Lease 过期 reclaim + fencing：epoch CAS、旧 coordinator 写拒绝 | store/scheduler | stale epoch 拒绝、reclaim attempt++ / max→failed |
| 6 | 集群资源账本：条件更新 reserve/release、enforcing/advisory、崩溃清理；P0-3 修复（worker 侧 budget 注入） | store、tasks.py、scheduler | 守恒测试、不超容量拉起、崩溃清理、worker budget 生效 |
| 7 | 资源感知通道：worker 能力注册（celery signals + 心跳线程）、profile 槽位、HoL 削减 | `cluster/workers.py`、task_queue | 能力匹配、通道隔离、worker prune |
| 8 | 公平/准入：加权轮转（可重建状态）、背压上限、burst 共享 | `cluster/fairness.py`、scheduler | 多租户 starvation-free、背压 429、burst |
| 9 | Retry/recovery：attempt 有界、WORKER_LOSS 分类、checkpoint resume、无重复副作用 | scheduler、既有 reuse_index | 恢复测试、复用命中、幂等副作用 |
| 10 | 抢占：priority、safe point（yield_check）、protected 段、checkpoint 后回队 | scheduler/executor/plan | 边界抢占、无 mid-node kill、高优先级前插 |
| 11 | Coordinator failover + chaos corpus：kill worker/scheduler、延迟心跳、重复投递、DB 瞬断、分区模拟 | `tests/unit/test_geocompute_v6_chaos.py` | chaos 矩阵全绿 |
| 12 | 可观察性 + REST：metrics 快照（封闭维度）、submit/list/cancel 端点、OpenAPI 再生成 | `cluster/metrics.py`、routes、snapshot | 基数上界断言、authz 测试、snapshot 一致 |
| 13 | 性能预算 + 差异基准：tick 查询数上界、dispatch 形状、内存有界 | `tests/benchmarks/`、`.agent-work` 记录 | 结构断言 gate；wall-clock 仅 INFO |

## REST 面（additive）

- `POST /geocompute/plans/runs` → 202 {run_id, status:"queued"}（auth + session 归属 + plan JSON ≤256KB + 背压 429）
- `GET /geocompute/runs?status=&limit=&offset=` → owner 域列表（cluster runs ∪ 证据快照）
- `POST /plans/runs/{id}/cancel` 既有端点升级：本地未命中 → 落库取消旗标（跨进程生效）
- `GET /geocompute/runs/{id}` 升级：内存 → cluster 行活投影 → 证据快照（三级回退）
- `GET /geocompute/cluster/metrics`（require_admin）

## 风险与对策

- SQLite 测试并发：全部 CAS 用单语句条件 UPDATE；会话短事务。
- executor.py 改动最小化：`run_id` 可选参数 + `yield_check` 可选参数 + PREEMPTED 枚举成员，
  默认行为逐字节不变（既有 180 用例为回归闸）。
- OpenAPI snapshot / generated catalogs：wave 12 一次再生成，diff 仅本任务端点。
