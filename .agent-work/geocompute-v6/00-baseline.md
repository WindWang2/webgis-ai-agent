# GeoCompute Cluster Runtime V6 — Baseline（Phase A 审计结论）

- 仓库：`WindWang2/webgis-ai-agent`；worktree：`/home/kevin/projects/webgis/geocompute-v6`
- 分支：`feat/geocompute-v6-cluster-runtime`；baseline = origin/master `445ad30`
- 审计方式：只读（subagent context-sweeper 全模块阅读 + 主 Agent 关键文件复核）

## V5 现状事实图（摘要，证据 file:line 见审计报告）

### 模块清单（app/services/geocompute/）
| 模块 | 职责 | 持久化 | 进程边界 |
|---|---|---|---|
| executor.py (1368) | 就绪集调度/预算准入/复用/取消/重试 | 无（终态快照 fail-open） | **run 注册表/取消令牌全 process-local**（:265-275, :470-480） |
| plan.py | ExecutionNode/Plan/Run 契约 | 无 | 纯 pydantic |
| graph.py | 校验/拓扑/失效/复用键 | 无 | 无状态 |
| ops.py | 类别→算子注册表（11 类） | session ref / Artifact | 每节点自开 session |
| api.py | facade：JSON→Plan、run_plan_sync、GOVERNOR | 无 | governor per-process |
| tasks.py (100) | Celery 任务体 run_geocompute_node | AnalysisTask 状态机 | worker 进程；**无 budget/governor**（P0-3） |
| durable.py (291) | dispatch/await/6-profile 队列路由 | 读 AnalysisTask | await 50ms 忙轮询在发起进程 |
| budgets.py (427) | 层级 governor 树（L1 权威） | 无 | per-process；GLOBAL_GOVERNOR 死单例 |
| resource_counter.py | 跨进程 advisory 计数器（Redis） | Redis | fail-open、默认关闭 |
| run_evidence.py | run 终态 ≤16KB 快照 | `geocompute_run_evidence` | fail-open |
| reuse_index.py | durable 节点跨进程复用索引 | `geocompute_node_results` | 每 owner LRU≤64 |
| tracing.py / replay.py | trace ring 1024 / 校验器 | 进程内 deque | process-local |
| reproducibility/drift/normalization/errors/_async_bridge | 证据/漂移/归一/错误/桥 | 无 | bridge 全局串行锁（ADR-0096 Deferred） |

### run 生命周期（现状）
`POST /plans/execute` → to_thread → run_plan_sync（GOVERNOR 作用域链）→ engine.execute_plan：
validate → admission → governor reserve → **内存注册 run + token** → 就绪集调度（ThreadPool≤2）
→ durable 节点：dispatch（幂等键，profile 队列）+ await 50ms 轮询 → 终态判定 → 快照落库 → 释放 → 丢载荷。
run 级状态机 `pending→running→completed|failed|cancelled` 仅存在于内存；
**没有持久 run 行**（P0-2）。durable 节点 worker 侧经 `durable_job`（AnalysisTask：
mark_running 条件更新 / heartbeat / cancel watchdog / stale sweep 已存在）。

## 已确认缺陷（V6 必须收敛）

### P0
- **P0-1** run 注册表与取消令牌 process-local：多副本下 cancel/get_run 只见本进程（executor.py:265-275,470-480；routes 268-281 docstring 自认）。
- **P0-2** in_process plan 无持久 run 行：HTTP 线程内同步跑完，进程死 → 在飞 run 无记录、不可恢复；deadline ≤3600s。
- **P0-3** worker 侧 durable 节点绕过 plan 预算/governor（tasks.py:54-61 无 budget）→ 只剩 HARD_NODE_ROW_CAP=500k。
- **P0-4** governor per-pod L1 权威；跨进程层 advisory/fail-open 默认关（resource_counter.py）→ N pod = N 倍全局限额。
- **P0-5** eager 悬崖：无 Redis → task_always_eager，durable 语义静默塌缩（诚实标注但不改变事实）。

### P1
- P1-1 in_process 节点复用不跨进程（NodeResultStore LRU 256/128MB）。
- P1-2 await_node_job 50ms 忙轮询占请求线程；2 个慢 durable 节点占满 run 并行度。
- P1-3 _async_bridge 全局串行锁（吞吐瓶颈，ADR-0096 Deferred）。
- P1-4 run_cache_size=128 逐出竞态（>128 并发 run 在飞注册表丢条目）。
- P1-5 trace ring process-local 1024 条。
- P1-6 无 run 列表端点（索引 idx_gc_run_evidence_owner 已就位但无 API）。
- P1-7 单 worker 消费全部 6 队列（profile 只是路由真相，无物理隔离）。

### P2（卫生）
- GLOBAL_GOVERNOR 死单例；run_evidence/reuse_index read-then-insert 非原子（fail-open 丢证据不损坏）；
  快照 64+ 节点截断（诚实标注）；cancellable=false 节点不接 token；重启后无法取消快照回放 run。

## jobs 子系统可复用机制（不重复建设）
- `analysis_tasks`：worker_id/heartbeat_at/cancel_requested_at 列 + `idx_task_status_heartbeat`
  + mark_running 条件更新（CAS）+ request_cancel 两段式 + stale sweep（300s）+ lifespan 后台清扫。
- `durable_job` 上下文：入口守卫（防 broker 重投）、CancelWatchdog 0.5s、finish_job(result_ref)。
- 幂等键 idempotency_key unique → 重派不产生第二 job 行；result_ref 载荷交接（载荷不入 DB）。

## 测试矩阵现状
- geocompute 12 个测试文件 ≈180 用例，全部 eager Celery + 临时 SQLite worker simulation；零真实 broker 覆盖。
- `tests/real_services_celery_app.py` = 真实 Redis smoke 独立 app（无 geocompute 任务）。
- perf：bench 脚本 wall-clock 全 INFO 不 gate；唯一 wall-clock 断言取消延迟 <2s；
  data_control_plane_v5 规模测试多为结构/计数断言（良好范式）。
- 边界锁：test_geocompute_boundary.py AST 禁 import gis_harness/pi/chat/tools。

## 与其他平台边界
- data_fabric：下游消费者（QUERY/FILTER/AGGREGATE/JOIN 复用其原语；准入谓词镜像）。
- GIS Harness / Pi：零 import（AST 锁）；WorkflowEngine：仅共享 runtime_manifest 与 outcome taxonomy。
- geocompute **不注册进全局 registry**；算子接线是本地 ops.REGISTRY；工具面 4 个 tier-2 工具。
- 前端零 geocompute 消费（durable 节点以通用 job 行出现在任务中心）。

## Scope 冻结（本 Epic ownership）
**做**：run 级持久生命周期（durable run registry + lease/epoch/fencing）、cluster coordinator
（调度/恢复/failover/公平/准入/抢占）、分布式取消、集群资源账本（有界 fail-open/closed）、
worker 能力注册与 profile 通道、retry/recovery 幂等、可观察性、chaos corpus、结构性 perf 预算、
REST（submit/list/cancel/metrics）、P0-3 修复。
**不做**：数据格式/湖仓身份（Data Lakehouse V6）、查询计划（Query Optimizer V6）、科学算法语义
（Science V4）、_async_bridge 串行锁重构（ADR-0096 Deferred，记录 known limitation）、
真实 broker e2e（保持 worker-simulation 测试范式 + 诚实标注）、trace 跨进程汇聚（P1-5 后续）。
