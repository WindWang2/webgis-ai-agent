# GeoCompute V7 — 架构（Phase B 冻结稿）

## 0. 总纲

V7 不新建执行平面：coordinator 仍是 DAG 编排者（进程内 ready-set 调度 + durable
节点经 broker 派发），V7 把这条链从「能跑」升级为「按能力放置、按局部性取数、
失败可对账、证据跨进程」。全部 additive 于 V6 事实源；eager/本地模式逐字节兼容。

## 1. 事实源与投影（不变式）

| 域 | 唯一事实源 | V7 变化 |
|---|---|---|
| run 生命周期 | `geocompute_runs` | + `resource_request` JSON（可空）。**不加 progress 列**（round1 #5：CAS 递增在 reclaim/复用下重复计数 —— progress 改为读时从 events 投影，total 取 plan_snapshot.nodes 长度，零漂移） |
| 节点 job | `analysis_tasks` | 不改 schema |
| 终态证据 | `geocompute_run_evidence` | 不改 |
| 节点复用 | `geocompute_node_results` | 不改（resume 仍走它） |
| **分布式 trace** | **新表 `geocompute_run_events`** | per-run 有界 append（见 §4） |
| worker 能力 | `geocompute_workers` | + `capability` JSON（typed 投影） |
| **worker 对象缓存** | **新表 `geocompute_worker_cache`** | 注册表（缓存本体在 worker 本地盘，注册表只是位置真相） |
| 账本 | `geocompute_resource_usage` | limit 列获得 admin 管理 API（行仍唯一真相） |

migration **0034**（down_revision=`0033_geocompute_v6_cluster`，单 head；全部
additive 列/表，无数据迁移）。

## 2. 组件（新增 4 模块 + 3 处既有模块深化）

```
cluster/capabilities.py   WorkerCapabilityProfile（typed pydantic）+ probe() + 词表
cluster/placement.py      ResourceRequest + eligible()/rank()（纯函数，可测）
cluster/locality.py       LocalityKey 派生 + 缓存注册表 API + 放置局部性加分
cluster/events.py         RunEventStore：per-run 有界 append/读窗口/progress 投影
cluster/workers.py        注册时附 capability 探针快照；心跳携版本指纹
durable.py                自适应轮询退避；派发记录 events（dispatch/complete/lost）
_async_bridge.py          专用 event-loop 线程替代全局 _SERIAL 锁
```

### 2.1 WorkerCapabilityProfile（wave 4）

```python
class WorkerCapabilityProfile(BaseModel):
    cpu_cores: int(1..1024)          # os.cpu_count
    mem_mb: int(1..4_194_304)        # /proc/meminfo → 未知=0（诚实）
    gpu_count: int(0..64)            # 无 nvidia-smi/torch → 0
    gpu_mem_mb: int(0..)
    backends: dict[str,str] ≤16      # raster/science/vector → 版本（缺席=""）
    capabilities: list[str] ≤16      # 扩展可用性（封闭词表：raster|vector|science|gpu|network|external_io|light_cpu|heavy_cpu|high_memory）
    zone: str ≤64                    # WEBGIS_WORKER_ZONE，缺省 "default"（locality 域）
    queue_slots: dict[str,int]       # 既有 profiles（沿用）
    version: str ≤64                 # 执行栈指纹（python 主次版本 + geo 栈版本哈希前 12）
    started_at / pid / hostname 不入 profile（info 域已有同事实则去重）
```

- `probe()` 全部 try/except 诚实降级（无 GPU 探测库 → gpu_count=0，绝不虚构）；
- 探针在 worker_ready 执行**一次** + 心跳只更新 `heartbeat_at`（profile 静态，
  避免每 10s 重探）。`capability` JSON 列上限 4KB（写入侧钳制）。

### 2.2 ResourceRequest 与放置（wave 5-6）

```python
class ResourceRequest(BaseModel):     # run 级（submit 可选字段 resource）
    min_mem_mb: int(0..2_097_152)=0
    min_cpu: int(0..1024)=0
    gpu: int(0..8)=0                   # >0 → 只放置 GPU worker
    zone: Optional[str ≤64]            # 局部性偏好（软约束）
    required_profiles: list[str] ≤8    # 既有词表（迁移自 submit 派生逻辑）
```

放置管线（coordinator tick 内，纯内存计算）。**诚实边界（round1 #3 CRITICAL 修订）**：
Celery 共享队列消费模型下，节点级「消息 → 特定 worker」不可强制绑定。V7 的放置
语义分三层，逐层声明约束力：

1. **run 级准入 gating（强制）**：coordinator 仅当存在合格 worker（profiles 覆盖 ∧
   mem/cpu/gpu 满足 run 聚合 envelope）时才 claim run —— 无合格 worker → run 留队 +
   `waiting_resource` 事件（存在性去重，batch ≤32/tick）。这是硬承诺。
2. **worker 侧准入守卫（强制 + 有界收敛）**：`run_geocompute_node` 认领后用本地
   capability 校验节点级要求（gpu/min_mem/min_cpu）；不满足 → celery `self.retry`
   有界重投（≤3 次，countdown 2s）后类型化 `PLACEMENT_MISMATCH`（retry_safe）失败
   —— 事件 + 证据诚实可见。多 worker 异构部署下 GPU 节点经 CPU worker 快速拒绝
   重投，收敛到 GPU worker；全部消费者不合格时在 ≤3 次内诚实失败，无 livelock。
3. **rank / locality（advisory）**：locality 加分与过配惩罚只影响派发前的
   队列容量判断与统计投影，不承诺绑定。

候选 = live worker（心跳 ≤ `WORKER_TTL_S` 单一来源）；eligible 如上；
rank：`(locality_score desc, -overprovision, last_dispatch asc, worker_id)`。
非 cluster 路径（eager / 本地直跑）不经过放置 —— 行为不变。

### 2.3 Data Locality 与 worker 对象缓存（wave 7-8）

**LocalityKey**：durable 节点的输入身份集合：
`node.dataset_fingerprints` 的值 ∪ 参数中声明的 `data_object_id`（sha256 词表校验）
∪ `dataset_id` 等 provenance 键（有界 ≤16 个）。纯函数 `locality_keys(node)`。

**`geocompute_worker_cache`**（位置**声明**注册表，非缓存本体、非位置真相 ——
worker 本地盘真实存在性以命中时校验为准）：

```
worker_id, cache_key(=sha256(owner_scope + ":" + locality_key)), owner_scope(哈希), size_bytes,
cached_at, last_hit_at; PK(worker_id, cache_key); per-worker 容量闸：entries ≤64 / bytes ≤ 4GiB（写入侧 LRU 逐出）
```

- **跨 owner 不泄漏（round1 #2 钉死）**：三层防护 —— (a) cache_key 含 owner_scope
  哈希（查找/登记/打分全部按 owner 派生键精确匹配，placement 打分查询同域）；
  (b) **worker 本地缓存键 = (owner_scope, locality_key)**，每次复用（不只首次物化）
  均过 owner/digest 校验；data_object_id 键走 `materialize_data_object`（owner+digest
  校验，lakehouse 既有）；fingerprint/session 键的本地复用同样绑定 owner 域 ——
  同一公共数据集的两个 owner 之间不共享本地文件；(c) 注册表只回答「本 worker 是否
  持有本 owner 的键」，无跨 worker 拉取协议。
- **一致性方向**：注册表与本地盘的一切不一致（TTL 清理竞态、prune 后复活、
  注册失败）全部失败方向为 miss → 走 session/BlobStore 重物化（性能损失，
  非正确性损失）。
- **登记语义**：成功物化**之后**注册；`INSERT ... ON CONFLICT(last_hit_at/size)`
  upsert；任何失败 fail-open（计 metric），绝不抛进节点执行。
- **失效**：TTL（默认 24h，`WEBGIS_WORKER_CACHE_TTL_S`）由 coordinator tick
  批量清理（每 tick ≤64 行）；worker 注销/prune 同批删除其缓存行。

### 2.4 分布式 DAG 执行与中间产物（wave 9-11）

- ready-set 仍在 coordinator 进程（`_run_ready_set`）；durable 节点按 §2.2 放置。
- **中间产物契约不变**：session ref 有界交接（载荷不进 DB 行）。V7 增量 =
  worker 侧 `ref_id` 写成后立即发 `node_output_ready` event（含 rows/bytes），
  coordinator 聚合 fanout 消费计数（单进程内 `outputs` dict 已共享，fanout=
  多 dependents 读同一载荷，无复制）。
- **fanout/fanin**：多父节点（fanin）由 ready-set indegree 语义天然覆盖；
  fanout（一父多子）共享同一 payload dict 条目。测试锁定「fanout N 子不重复
  执行父节点」「fanin 等全部父完成」。
- **pipelined execution where safe**：ready 节点立即可派发（无波次屏障，V4 起
  已是）；V7 补「兄弟分支先完成不被慢波阻塞」的回归测试与 events 证据。
- **cancellation cascade**：run token → 在飞 durable job `request_cancel_sync`
  （既有）+ 新增：coordinator 收到 cancel 后对**未派发** ready 节点直接置
  cancelled event（V6 已 sweep，V7 补事件）。

### 2.5 分布式 Trace / Progress（wave 15-16）

**`geocompute_run_events`**（round1 #1/#4/#8 修订）：

```
id PK autoinc（全局单调；per-run 单调性由 id 序保证 —— 弃稠密 per-run seq，
  消除 seq 分配竞争面），run_id, event(封闭词表 ≤32 字符), node_id(可空 ≤128),
  worker_id(可空 ≤128), attempt(可空), status(可空), rows/bytes(可空), error_code(可空), created_at
INDEX(run_id, id)；游标 = after_id（断点续读）
```

- **词表封闭**：`run_started|node_dispatched|node_started|node_output_ready|
  node_completed|node_reused|node_failed|node_cancelled|node_lost|run_completed|
  run_failed|run_cancelled|run_preempted|waiting_resource|worker_cache_hit|straggler_detected`
- **有界（分层预算）**：节点级事件（node_*）per-run ≤512 条 —— append 前
  COUNT（(run_id) 索引，O(≤512)），超限丢弃 + `event_budget_exhausted_total`
  metric；**run 级/终态/治理事件（run_*、waiting_resource、straggler_detected）
  豁免预算**（全 run ≤~10 条，终态可见性永不因节点事件洪泛丢失）。
- **失败语义（fail-open 钉死）**：append = 独立短事务；任何失败（含 run 行已被
  retention purge 后的孤儿 append —— UPDATE/INSERT 落空）= 丢弃 + metric，
  **绝不抛进节点执行路径**。事件是尽力而为的 observability trace；
  **终态证据 of record 仍是 `geocompute_run_evidence`**（append-once 承诺不因
  events retention 受损 —— run_evidence 全文件无 delete）。
- **写入方**：coordinator（run 生命周期 + waiting_resource 存在性去重：append 前
  SELECT 存在性，batch ≤32/tick 有界）+ worker 任务体（dispatch/start/output/
  complete，经 `RunEventStore.append` 直写 DB；**run_id 经 task_kwargs 穿透**
  —— 不进 params，幂等键不变，budget 同款先例 durable.py:193）。Celery 边界
  丢 contextvars（jobs/worker.py:348 自证），显式传参是唯一可靠通道。
- **读投影**：`GET /geocompute/runs/{id}/events?after_id=`（owner 域校验，
  窗口 ≤200/页）；**run 行被 retention purge 后 events 一并删除 → 该端点 404**
  （与 GET /runs/{id} 的 evidence 回放不同源 —— API docstring 明示）。
  progress：`done = COUNT(DISTINCT node_id WHERE event ∈ node_completed/reused/
  cancelled/failed 且 attempt 为该节点最新)`（读时投影，天然幂等跨 attempt），
  `total = len(plan_snapshot.nodes)` —— 零新增列、零重复计数。
- **孤儿清理**：events 自带 TTL 清理（created_at < retention，每 tick ≤256 行，
  与 run 行清理独立）—— worker 晚到事件不产生永久孤儿。purge_terminal 改为
  单事务：同批 events + run 行同删。
- **OpenTelemetry seam**：事件词表与 tracing.emit 字段同形，后续 OTel SDK 映射
  不改事件源。

### 2.6 Straggler / Worker Loss / Duplicate（wave 17-19）

- **straggler 判定（结构性的，非墙钟魔法）**：durable 节点 running 时长 >
  `node.deadline_s`（若声明）→ `straggler_detected` 事件 + metrics 计数；
  worker 心跳断（geocompute_workers 行消失）但其 job 仍 running → 同事件。
  处置仍交给既有 stale sweep（300s）→ WORKER_LOSS 分类重试（不发明新状态机）。
- **worker dies after output before ACK**：acks_late=True 下 redelivery →
  durable 状态机守卫（终态行幂等键拒绝/入口守卫）→ 第二次投递 no-op；
  测试断言：结果只解析一份、输出不重复、job 行终态一致。
- **visibility timeout（duplicate delivery）**：同上测试以真实 broker
  `worker_died_between` 场景模拟（kill worker 后重投递同 job_id）。
- **speculative retry guard**：重派只发生在「stale/lost 已确认」（job 行状态
  迁移后），幂等键保证同 job 单飞行；**并发投机执行被状态机禁止**（wave 19
  以测试证明：同 job_id 双投递第二份必被入口守卫拒绝）。投机重试的准入 =
  `node.deterministic and node.reuse==ALLOW and attempts 余量>0`（既有
  RetryPolicy，不新增开关）。
- **partial partition**：worker 网络分区（心跳停、broker 断）→ prune_workers
  容量收缩 + reclaim；broker 恢复后 retry affinity 同队列（queue_for_node
  确定性）。

### 2.7 coordinator restart / run resume（wave 20）

- 重启语义不变（lease 过期 → 新 coordinator reclaim → attempt++）；V7 增量 =
  resume 正确性经 `reuse_index` 跨进程命中已完成 durable 节点（既有），并
  断言 events 流水线连续（新 attempt 的 seq 续接，无重置）。
- **events 的 seq 在 reclaim 后不重置**（真相是 append-only 历史）。

### 2.8 `_async_bridge`（wave 21，round1 #6 修订）

```python
# 现状：_SERIAL threading.Lock 串行全部协程执行（审计 B1）
# 目标：单一专用 event-loop 线程（daemon）+ asyncio.run_coroutine_threadsafe
#   - supervisor：loop 线程死亡 → 检测重建（调度前 is_alive 检查 + 死 loop
#     future 永不 resolve 的挂死防护）；同线程调用直跑护栏
#   - result(timeout) 显式有界（默认 30s，调用方可传）—— 超时类型化
#     TimeoutError，绝不永久占用节点线程槽位
#   - loop 内协程真并发（IO await 交错），消除**bridge 调用方之间**的
#     跨线程串行
# 诚实边界：REST 事件循环 ↔ bridge loop 的跨 loop 争用（session_data 的
#   asyncio.Lock 首用绑定 loop）依旧存在 —— ADR-0096 Deferred 既有结论，
#   本 wave 不声称解决，只消除 _SERIAL 引入的进程级串行
```

证据 = 结构基准：8 线程 × 每次 5ms async sleep 的桥调用，修复前总时长
≈ 串行 40ms，修复后并发加速比 > 2（结构性成立）。基准断言加速比，
不 gate 绝对墙钟。

### 2.9 Admin / Observability（wave 22）

全部 `require_admin`（沿用 /cluster/metrics 纪律）：

- `GET /geocompute/cluster/workers`：capability/健康/缓存占用投影（有界 ≤100 行）
- `GET /geocompute/cluster/runs/stuck`：running 且心跳过期 > lease TTL 的 run 视图
- `POST /geocompute/cluster/runs/{id}/reset`：CAS 强制回队（attempt++；语义与
  reclaim 完全一致 —— 复用 `reclaim_expired` 的单行版本，不新造转移）
- `POST /geocompute/cluster/ledger/limits`：设置 scope limit（NULL=解除）；
  enforcing/advisory 模式不变
- metrics 扩展（封闭词表）：`waiting_by_profile`、`queue_wait_p50/p95`
  （leased_at-created_at 采样 deque ≤128）、`straggler_total`、
  `event_budget_exhausted_total`、`cache_hit/miss_total`（进程内有界计数）

## 3. 状态机（无变化声明）

run 级：V6 七态白名单 + epoch CAS 原样保留。V7 **不新增** run 状态；
`waiting_resource` 是事件不是状态（状态机复杂度零增长）。节点级继续穿
`analysis_tasks` 既有状态机。

## 4. 资源 envelope（全局无界路径审计承诺）

- events：per-run ≤512 行；行数随 run 数线性但单 run 有界 + run 行 retention
  （purge_terminal）级联清理（外键无，删 events 由 purge 同批删除 —— 同一
  helper，run 行删除前先删其 events）。
- worker cache：per-worker ≤64 条 / ≤4GiB 记账；TTL 批量清理。
- capability JSON ≤4KB；resource_request JSON ≤1KB（写入侧钳制 + typed 422）。
- metrics 维度全部封闭词表；采样 deque ≤128。
- placement 每 tick 计算量 O(batch × live_workers)，batch ≤32、workers ≤ 上限
  （live_workers 查询本就全量返回；新增防御上限 256 —— 超过截断并计数，
  防注册表被刷爆，admin metrics 可见 `workers_truncated`）。

## 5. 安全边界

- 新端点全部强制认证；admin 端点 require_admin；events/cache/ledger 读写全部
  owner/作用域校验（events：owner_scope 行校验后读；cache：cache_key 含 owner
  哈希 + 校验；ledger：admin only）。
- `zone` / `data_object_id` / capability 字段全部白名单或词表校验（无自由串进
  日志/DB 的注入面；data_object_id 强制 sha256 词表）。
- worker 缓存注册表不含路径（worker 本地路径绝不出 worker 进程 —— 注册表只记
  「持有 + 大小 + 时间」）。

## 6. 兼容与 rollout

- eager（无 Redis）：全部新路径短路（placement 返回 None→本地执行；events 仅
  coordinator 侧 run 生命周期事件；capability probe 不运行）→ 行为与 V6 逐字节
  一致（回归测试锁定）。
- 非 cluster REST 提交（/plans/execute）：零变化。
- cluster 提交：新 `resource` 字段可选；**required_profiles 合并语义（round1 #7
  钉死）：`最终 = derive(durable 节点) ∪ resource.required_profiles`（用户只能
  加宽不能收窄 —— 收窄会把「安全留队」劣化为「认领后消息滞留 → 3 次 reclaim →
  failed[WORKER_LOSS]」）**；用户传入词逐个 ∈ `EXECUTION_QUEUE_PROFILES` 校验，
  越界 typed 422（词表单一来源 = durable.EXECUTION_QUEUE_PROFILES）。
- DB：additive 列可空 + 新表；旧代码读新库无感；新代码读旧库 → fail-open
  （capability 缺省 = 只按 profiles 匹配，V6 语义）。

## 7. 测试 oracle 与性能预算

- 状态机/CAS：真 SQLite 临时库（V6 惯例）；
- broker E2E：`@pytest.mark.real_services` + REAL_SERVICES=1 + 生产 celery worker
  子进程 + 独立 SQLite 文件（DATABASE_URL 注入 worker env）+ 本地 Redis（可达）；
  服务缺席 → self-skip（诚实，不伪装通过）；
- 结构预算：tick 查询常数上界（扩展到 placement/事件路径）、bridge 并发加速比、
  events append O(1) 查询数、100/1000 小节点 DAG 的 ready-set 结构断言
  （节点数平方不可接受 → 断言 settle 次数 = N、查询数与 N 无关）；
- 差分：durable 节点经 placement 派发与 V6 直接派发的最终载荷指纹一致。

## 8. ADR

ADR-0119（编号已核实为下一个可用）：Distributed Adaptive Spatial Compute
Fabric —— 记录事实源边界、放置管线、局部性模型、事件事实源、兼容矩阵。
