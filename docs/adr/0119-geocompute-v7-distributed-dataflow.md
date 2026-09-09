# ADR-0119: GeoCompute V7 — Distributed Adaptive Spatial Compute Fabric

## 状态（Status）

Accepted（2026-09-10，与 feat/geocompute-v7-distributed-dataflow 同枝落地）

## 背景（Context）

GeoCompute V6（ADR 无独立编号，实现于 PR #1163）建立了 run 级 cluster
control plane：持久 run 行、coordinator 选举（leadership epoch）、lease
fencing、分布式取消旗标、资源账本、worker 注册、加权公平、抢占与恢复。
但「集群能可靠执行完整 GIS DAG 并按资源/数据位置/失败/拥塞动态调度」
仍有明确缺口（.agent-work/geocompute-v7/00-baseline.md 审计 G1-G7）：

1. worker 只注册 `{profile: slots}`，无 CPU/内存/GPU/backend/版本剖面；
2. 放置只有 profile 集合包含 + 租户轮转，无资源 envelope、GPU 区分、
   局部性；
3. 无 DataObject 内容位置感知、无 worker 对象缓存；
4. 分布式执行事件（worker 侧）对 coordinator/用户不可见；
5. straggler / worker 丢失 / 重复投递缺少显式对账证据；
6. geocompute 全链从未在真实 broker 上验证过；
7. `_async_bridge` 进程级 `_SERIAL` 锁把全部会话存储 IO 串行化。

## 决策（Decision）

### D1 放置的三层诚实语义（不可谈判）

Celery 共享队列消费模型下「消息 → 特定 worker」**不可强制绑定**。
V7 的放置承诺分层声明，绝不虚报：

1. **run 级准入 gating（强制）**：coordinator 仅当存在合格 worker
   （profiles 覆盖 ∧ capability 满足 envelope）时才 claim run；
2. **worker 侧准入守卫（强制 + 有界收敛）**：`run_geocompute_node`
   认领后以本地 capability 校验 envelope；不符 → celery 有界重投
   （≤3 次）→ 类型化 `PLACEMENT_MISMATCH` 诚实失败（多 worker 异构
   部署下 GPU 节点收敛到 GPU worker；全部消费者不合格 ≤3 次内失败，
   无 livelock）；
3. **rank / locality（advisory）**：局部性加分与过配惩罚只影响统计
   投影，不承诺绑定。

### D2 事实源边界（延续 V6，零新增第二真相）

| 域 | 真相 | V7 变化 |
|---|---|---|
| run 生命周期 | `geocompute_runs` | + `resource_request` JSON（≤1KB 钳制） |
| 节点 job | `analysis_tasks` | 不变（run_id/input_refs 经 task_kwargs 穿透，不进幂等键） |
| 终态证据 of record | `geocompute_run_evidence`（append-once） | 不变 |
| 执行过程 trace | `geocompute_run_events`（新，有界） | 尽力而为；随 run retention 级联删 + 独立 TTL |
| worker 能力 | `geocompute_workers.capability` JSON（新列） | 缺省 = V6 profiles 语义 |
| 缓存位置声明 | `geocompute_worker_cache`（新表） | 位置**声明**非真相；不一致失败方向 = miss → 重物化 |
| 载荷 | session ref | 不变 |

### D3 分布式事件的失败语义（架构审查 round1 修订）

- **分层预算**：节点级事件 per-run ≤512（超限丢弃 + metric）；run 级/
  终态/治理事件豁免 —— 终态可见性不因节点事件洪泛丢失；
- **fail-open 钉死**：append 独立短事务；任何失败（含 run 行已被
  retention 清理后的孤儿 append）= 丢弃 + 有界计数，绝不抛进节点执行；
- **per-run 单调序 = 全局自增 id**（弃稠密 per-run seq —— 消除多进程
  seq 分配竞争面）；断点续读游标 `after_id`；
- **progress 读时投影**：done = 事件 DISTINCT 节点最新终局、total =
  plan_snapshot 节点数 —— 放弃 CAS 递增列（多 attempt 下必然重复计数）；
- **终局事件单一来源**：节点终局（completed/reused/failed/cancelled/
  lost）只由 coordinator 发射；worker 只发 node_started / output_ready /
  cache_hit —— 双写会让「无重复输出」断言失真。

### D4 数据局部性与 worker 对象缓存

- cache_key = sha256(owner_scope + ":" + locality_key)：登记/查找/打分
  全部按 owner 派生键精确匹配（跨 owner 永不共享寻址）；worker 本地
  缓存键同样绑定 owner 域，每次复用均过 owner/digest 校验；
- 注册表是位置**声明**：一切不一致（TTL 竞态/prune 后复活/登记失败）
  的失败方向都是 miss → 走 session/BlobStore 重物化（性能损失而非
  正确性损失）；
- per-worker 容量闸：entries ≤64 / bytes ≤4GiB（写入侧 LRU 逐出）；
  worker prune/注销级联删其声明（防幽灵位置）。

### D5 `_async_bridge` 重写

单一专用 event-loop 线程（daemon + supervisor 重建）+
`run_coroutine_threadsafe`：调用线程只阻塞在 `Future.result(timeout)`
（默认 30s，类型化 `BridgeTimeoutError`）。消除 V6 `_SERIAL` 锁的
进程级串行与 loop 死亡挂死面。**诚实边界**：REST 事件循环 ↔ bridge
loop 的跨循环争用依旧存在（ADR-0096 Deferred 既有结论，不声称解决）。

### D6 Admin 面

全部 `require_admin`：worker 能力/健康投影（≤256 行）、stuck run 视图
（lease 过期未收敛）、强制回队（语义 = reclaim_expired 单行版本，
`require_expired=True` 防误杀健康 run）、账本限额设置（NULL = 解除）。
metrics 扩展全部封闭词表维度：queue_wait 分位（采样 ≤128）、
waiting_by_profile、事件计数器、gpu_workers。

### D7 兼容矩阵（eager 逐字节不变）

- eager（无 Redis）：placement 短路（本地全可执行）、无事件/无
  capability 探针 —— 与 V6 行为一致（回归测试锁定）；
- `/plans/execute` 同步路径零变化；`emit_events`/`resource_envelope`
  全部 opt-in；
- cluster submit 新 `resource` 字段可选；`required_profiles` 合并语义 =
  derive(durable 节点) ∪ 用户传入（用户只能加宽不能收窄），越界词
  typed 422；
- DB additive（可空列 + 新表，migration 0034 单 head）；新代码读旧库
  fail-open 退回 V6 语义。

## 顺带修复的既有缺陷（V6 潜伏）

- **P1：worker 注册从未在真实 worker 上生效** —— kombu
  `Signal.connect` 默认 `weak=True`，闭包 handler 在注册函数返回后被
  GC，`worker_ready` 永不触发。V6 全部测试 eager、real-services lane
  从未跑过 geocompute worker，缺陷直至 V7 real-broker E2E 才暴露。
  修复 = 显式 `weak=False` 强引用注册（cluster/workers.py）。
- **P2：SQLite 引擎无 busy timeout** —— 多进程控制面并发写下
  「database is locked」即失败；加 `timeout=30`（app/core/database.py）。
- **P2：durable 节点轮询固定 50ms** —— 100 并发在飞节点 = 2000 qps
  纯轮询；改自适应退避 0.05→0.5s（取消传播上界仍由 run 心跳 0.5s
  主导，不劣化）。

## 后果（Consequences）

- 正面：集群按能力/资源放置成为可验证事实（run 级强制 + 节点级有界
  收敛）；全 durable 节点链首次可执行（input handoff）；分布式执行
  过程对用户/运维可见（events + progress + admin）；V6 的 worker 注册
  缺陷被真实测试捕获并修复。
- 取舍：节点级放置不承诺绑定（共享队列模型的诚实边界，见 D1）；
  events 是尽力而为的 trace，不承载终态证据职责；worker 缓存 v1 只
  做位置声明 + 进程内 LRU，不做 worker 间取数协议。
- 后续（follow-up candidates）：per-worker 专用队列（强制绑定，若部署
  形态需要）；events → OpenTelemetry exporter；straggler 的主动迁移
  （当前只做可见性，处置交给既有 stale sweep）。
