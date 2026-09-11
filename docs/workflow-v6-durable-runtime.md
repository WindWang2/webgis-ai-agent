# Workflow V6 — Durable Distributed GIS Workflow Runtime

Workflow Runtime V5（单进程波次调度）升级为**可持久、可恢复、可取消、可重试、
可分布式调度**的执行底座。设计原则延续 V5：单一裁决点（状态机/CAS）、诚实
边界（绝不假装执行）、复用既有真相（不新造平行事实域）。

## 架构总览

```
┌────────────────────────── workflow_runtime ───────────────────────────┐
│ service.py      门面：编译→注册→实例化→驱动→取消→变更→克隆→恢复        │
│ driver.py       波次执行驱动（claim/租约/重试/超时/取消/补偿收口）      │
│ machine.py      纯函数状态机 + DAG 就绪/闭包计算（零 I/O）              │
│ contracts.py    状态词表/转移表/事件词表/证据模型                       │
│ store.py        实例+节点两级 CAS 持久层 + journal + 两级租约           │
│ recovery.py     启动/周期恢复清扫（crash → recover 的主动面）           │
│ retry.py        RetryPolicy + 错误分类（复用 geocompute FailureClass）  │
│ compensation.py 补偿钩子（半提交产物清理）                              │
│ cluster.py      worker 能力注册表（workflow_workers 表）                │
│ dispatch.py     local/durable 派发面 + 全局资源上限                     │
│ adapters_*      GeoCompute / Science(data_fabric) / Cartography 真实栈 │
└────────────────────────────────────────────────────────────────────────┘
```

## Durable 语义（Phase B/C）

### 两级租约

| 租约 | 持有者 | 真相列 | 过期语义 |
|------|--------|--------|----------|
| run 租约 | driver（coordinator） | `workflow_instances.run_lease_*` | 谁在**驱动**实例；恢复清扫的门 |
| 节点租约 | 执行者（driver/worker） | `workflow_instance_nodes.lease_expires_at` | 谁在**执行**该节点；过期 → 孤儿 → 接管 |

- claim（READY→RUNNING）即写节点租约；终态转移清租约；`heartbeat_node`
  仅 RUNNING 且持有人匹配可续（fencing：被接管的旧 worker 写入被拒）。
- **执行期续期**：`_run_node` 派发后启动独立续期任务（ttl/3 周期）——
  单次执行超过 TTL 不再被误判孤儿（消除双重执行竞态）。
- coordinator 存活 ≠ worker 存活：run 租约活着时节点租约过期同样可被
  接管（worker 死亡独立恢复）。

### 事件 journal（workflow_events）

- **与状态转移同事务**写入（atomic truth）；取消/恢复/重试/补偿经
  `append_event`；词表见 `contracts.EventKind`。
- 读路径分页有界（≤200/次，`id` 升序 = 因果序）；`GET /instances/{id}/events`。
- 注意实现陷阱：SQLAlchemy 2.0 `session.execute(update())` 默认
  `synchronize_session` 会就地刷新会话内 ORM 属性 —— journal 的
  `from_state` 必须在 UPDATE **前**捕获。

### 恢复清扫（recovery.py）

`list_recoverable_instances`：RUNNING 且（run 租约过期 / 无主超过 TTL /
挂着取消旗标）。逐实例：

1. 消费遗留取消旗标（实例级 → 全部非终态 CANCELLED + 实例终态）；
2. 孤儿 RUNNING 节点复位 READY（attempts 保留，journal 记
   `recovery_orphan_reset`）；
3. 节点级取消旗标兜底消费；
4. 终态愈合：全部节点已决但实例行 RUNNING（crash 在 finalize 边界）→
   补落终态。

全部收敛走条件更新 —— 多副本 API 同时扫安全。接线：`app/main.py` lifespan
周期任务，`GIS_WORKFLOW_RECOVERY_INTERVAL_S`（默认 60，0 关闭）。

### 重试（retry.py）

- `RUNNING→FAILED`（attempt 证据）→ 可重试且预算未尽 → `FAILED→READY` +
  `next_ready_at` 退避门（**持久化**，crash-safe）→ journal
  `retry_scheduled`；预算耗尽 → `retry_exhausted`。
- 分类单一真相：geocompute `FailureClass`（ADR-0101 D5）白名单 ∪
  workflow 自产码（`NODE_TIMEOUT`/`NODE_EXCEPTION`/`DB_BUSY`/`WORKER_LOSS`）；
  未知错误保守不重试（fail-closed）。
- per-node 超时 `node_timeout_s` → `NODE_TIMEOUT`（可重试）。
- 补偿（compensation.py）：取消/失败路径已 materialize 的产物按 ref 方案
  分派清理（默认 `ref:` → session delete_ref），fail-open + journal 证据；
  完成边界 late-success 收敛为 cancelled（与 jobs 同纪律）。

### 节点级取消

`request_node_cancel` 持久旗标（终态不追改、幂等）；driver 波界观察：
在飞节点点燃 cancel token（协作中止）、queued 节点直接 CANCELLED；
`service.cancel_nodes` 传播 `downstream_closure` 后代闭包；恢复清扫兜底
消费。CANCELLED 保持吸收态不变量 —— 恢复走 `clone_run`（全新实例不携带
取消事实）。

## 分布式调度（Phase D）

- **worker 注册表**（`workflow_workers`）：能力（CPU/内存/GPU/profile
  槽位/IO/后端，词表白名单 + 值钳制防自授）+ 心跳（stale 须显式 register
  复活）；`WorkerRegistry.sweep_dead` 并入恢复清扫。
- **派发面**（`GIS_WORKFLOW_DISPATCH`）：
  - `local`（默认）：进程内执行，行为与 V5 一致；
  - `durable`：经 geocompute durable 通道（`dispatch_node` —— 幂等键/
    心跳/WORKER_LOSS 分类/stale 重派全部复用既有真相，不新造任务表）；
  - `auto`：重 profile（raster/heavy_cpu/high_memory）且有活跃 durable
    worker 覆盖 → durable，否则 local。
- **全局资源上限**：进程级派发信号量 `GIS_WORKFLOW_DISPATCH_SLOTS`
  （默认 4）—— 多实例并发 run 共享爆炸半径上界。
- **大任务隔离**（`GIS_WORKFLOW_ISOLATE_LARGE_TASKS=1`）：超行数阈值节点
  必须 durable 执行；无合格 worker → `NO_CAPABLE_WORKER`（诚实暴露）。
- **优先级/公平**：批次内按节点 `priority`（-10..10，默认 5）降序派发，
  同优先级保持 DAG 声明序（FIFO，无饥饿）。
- **硬截止**：run deadline 到点停止**等待**在飞节点（点燃 cancel token +
  放弃等待）—— 节点保持 RUNNING 认领态由租约/恢复面收尾（V5 的 gather
  无界等待缺陷修复）。

## 增量/克隆/嵌套（Phase E/F）

- `clone_run`：同包同版本新实例；data 角色绑定继承（会话事实）；skip
  unchanged 由复用索引自由结论（同指纹零重算）；分支工作流 `only_nodes`
  （keep-set = 祖先闭包 ∪ 白名单 ∪ 后代闭包，闭包外 SKIPPED）/
  `skip_nodes`；**只允许终态源实例**（否则 supersede 纪律会反向杀死源）。
- `retry_failed_nodes`：FAILED→READY 预算内重排（耗尽诚实拒绝）。
- 嵌套：父取消**全异步传播**（watcher 轮询父旗标 → 子实例持久取消）；
  deadline 继承（子 ≤ 父剩余 `remaining_s`，嵌套链不再重置时钟）。

## Inspector API（Phase G）

| 端点 | 语义 |
|------|------|
| `GET /instances/{id}/events` | journal 顺序读（limit/after_id/kind） |
| `GET /instances/{id}/nodes/{node_id}` | 节点明细（attempts/转移/租约/复用证据） |
| `POST .../nodes/{node_id}/retry` | 人工重试（耗尽 409；CANCELLED 不可单点复活） |
| `POST .../nodes/cancel` | 节点级取消（后代闭包开关） |
| `POST .../clone` | 克隆运行 |
| `GET .../debug` | 投影 + explain + 事件尾 + 子工作流树 |

全部强制认证 + owner 过滤（他人 404 语义）。

## 真实 GIS 执行面（Phase H）

执行面选择（确定性优先级）：测试钩子 > cartography > science > 派发面 >
geocompute in-process；`attempt_log.backend` 如实记录。

- **science**（`adapters_science.py`）：stats 类 capability 直接调用
  data_fabric `compute_aggregates`（SQL 对齐）；wired：
  `category_breakdown` / `admin_aggregation` / `rate_aggregation`；未接线
  capability 保持诚实 `NODE_NOT_EXECUTABLE`。
- **cartography**（`adapters_cartography.py`）：matplotlib 真实渲染
  （Point 散点 / Polygon 填色 / rows 条形图，按 value field 着色 + 图例）
  → `generate_map_pdf` A4 合成 → blob store 内容寻址落存（`blob:<key>`）；
  重依赖缺席 → `CARTOGRAPHY_UNAVAILABLE`。

## 环境变量

| 变量 | 默认 | 语义 |
|------|------|------|
| `GIS_WORKFLOW_RECOVERY_INTERVAL_S` | 60 | 恢复清扫周期（0 关闭） |
| `GIS_WORKFLOW_RETRY_MAX_ATTEMPTS` | 2 | 节点重试预算（≤3） |
| `GIS_WORKFLOW_RETRY_BASE_DELAY_S` / `_MAX_DELAY_S` | 0.5 / 8 | 退避 |
| `GIS_WORKFLOW_DISPATCH` | local | local / durable / auto |
| `GIS_WORKFLOW_DISPATCH_SLOTS` | 4 | 进程级派发槽位 |
| `GIS_WORKFLOW_ISOLATE_LARGE_TASKS` | 0 | 大任务强制 durable |

默认配置下所有行为与 V5 兼容（除缺陷修复）。

## 迁移

`0035_workflow_v6_durable`（可重入 DDL）：新表 `workflow_events` /
`workflow_workers`；`workflow_instance_nodes` 新列 `lease_expires_at` /
`heartbeat_at` / `cancel_requested` / `next_ready_at`。

## 测试

`tests/unit/workflow_runtime/test_v6_*.py`：journal 原子性/分页、心跳
fencing、恢复清扫三场景、退避重排/耗尽、超时、queued+running 取消、
后代闭包、补偿（取消/失败/fail-open）、重试不重复提交、worker 注册表/
槽位上限/派发决策矩阵、worker 死亡接管、在飞续期、克隆零重算/分支、
嵌套传播/deadline 继承、Inspector API 契约、真实 GIS 贯穿
（Data→buffer→Science→Cartography，heavy 标记）。
