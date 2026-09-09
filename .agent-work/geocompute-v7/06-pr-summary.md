# GeoCompute V7 — PR 总结（06）

## Problem / Motivation

GeoCompute V6 建立了 run 级 cluster control plane（持久 run 行、coordinator
选举、lease fencing、分布式取消、资源账本、worker 注册、公平、抢占、恢复），
但集群只会「按顺序挑 run 本地执行」：无能力剖面、无资源放置、无数据局部性、
分布式执行过程不可观测、全链从未在真实 broker 上验证。本 Epic（Epic 05）把
「知道谁该执行什么」升级为「能可靠执行完整 GIS DAG，并根据资源、数据位置、
失败和拥塞动态调度」（ADR-0119）。

## Current-state audit

.agent-work/geocompute-v7/00-baseline.md：生产入口/事实源清单/P1-P3 分级
（G1-G7 缺口 + B1-B3 性能 + M1-M3 细节），全部带 file:line 证据。

## Architecture

.agent-work/geocompute-v7/01-architecture.md（含 Subagent-A 架构挑战修订注记）；
ADR-0119。核心决议：
- 放置三层诚实语义（run 级 gating 强制 / worker 守卫有界收敛 / rank advisory）
- 事实源零新增第二真相（events 是尽力而为 trace；终态证据 of record 不变）
- progress 读时投影（放弃 CAS 递增列）；per-run 事件序 = 全局 id（弃稠密 seq）
- worker 缓存 = 位置声明（失败方向恒为 miss → 重物化）

## Implementation waves（Epic §14 的 26 waves 全部执行）

1-2 审计+架构 → 3-8 契约/DB/五新模块 → 9-11 调度接线/事件/bridge → 12 REST
→ 13 单测 → 14 real-broker E2E → 15 结构 perf → 16 ADR/docs → 17-18 两轮
review+修复 → 19 rebase 验证。

## Key code paths

- 能力：cluster/capabilities.py（诚实探针）→ workers.py（worker_ready 注册）
- 放置：cluster/placement.py（eligible/rank）→ scheduler._dispatch（gating）
  → tasks._placement_guard（worker 侧有界收敛，终局经 mark_failed_sync 落库）
- 局部性：cluster/locality.py（键派生/注册表/TTL）+ cluster/object_cache.py
  （worker 本地 LRU）→ admin /cluster/workers 投影
- 事件：cluster/events.py（分层预算/fail-open）→ executor emit_events
  （opt-in，节点终局单一来源）→ GET /runs/{id}/events（after_id 续读）
- input handoff：executor._execute_durable → durable.dispatch_node(task_kwargs)
  → tasks._resolve_inputs（缓存优先，owner 域隔离）

## Data / persistence changes

migration 0034（down_revision=0033，单 head，fresh-DB up/down/up 已验）：
- `geocompute_runs` + resource_request JSON（可空，≤1KB 钳制）
- `geocompute_workers` + capability JSON（可空，≤4KB 钳制）
- 新表 geocompute_run_events（有界 trace；随 run retention 级联删 + 独立 TTL
  排除活跃 run）；新表 geocompute_worker_cache（位置声明，LRU/TTL/prune 级联）
全部 additive；旧代码读新库无感；新代码读旧库 fail-open 退回 V6 语义。

## API / contract changes（全部 additive）

- POST /plans/runs 新可选 `resource`（ResourceRequest；required_profiles 与
  durable 节点派生集 **union**，越界词 422）
- GET /runs/{id}/events?after_id=（owner 隔离，页 ≤200）
- GET /cluster/workers（admin，capability+缓存占用）；GET /cluster/runs/stuck；
  POST /cluster/runs/{id}/reset（单行 reclaim 语义+同库 ledger 归还）；
  POST /cluster/ledger/limits
- GET /cluster/metrics 扩展：queue_wait/waiting_by_profile/events_counters/
  gpu_workers（封闭词表）
- GET /runs/{id} 附 progress 读投影；OpenAPI 快照再生成（310 行 additive，
  零 breaking）

## UI changes

无（后端 Epic；所有新能力经 REST，前端未触及）。

## Security implications

新端点全部强制认证，admin 四端点 require_admin；events/cache/ledger 全部
owner/作用域校验（events 读投影伪名化 worker_id —— hostname:pid 不出控制面）；
data_object_id/zone/profile 词全白名单；resource_request/capability 经
forbid-extra pydantic + 写入侧尺寸钳制；worker 注册表面与 V6 相同未扩大。

## Performance evidence（结构性，非墙钟）

- tick 查询数与排队量/worker 数无关（5 vs 60 queued 对比断言）
- events append/window/progress O(1) 查询数（P2/P3）
- 100/1000 节点链 settle==N（P4；1000 按 max_nodes=256 契约拆 4 plan）
- bridge 相对加速比 > 2（自相对，抗负载；V6 _SERIAL 下两者相等）
- durable 轮询自适应退避 0.05→0.5s（100 并发节点 2000→≤数百 qps）

## Local test matrix（精确计数）

- 全量 geocompute 相关：**454 passed, 6 skipped**（unit -k geocompute + perf +
  migration wiring + alembic metadata；6 skip = offline real-services 诚实跳过）
- real-broker E2E（REAL_SERVICES=1，真实 Redis + 生产 worker 子进程）：
  **5 passed（两次连跑验证稳定，36s/42s）**——全 durable 链含 input handoff /
  crash→stale→重派→完成 / 跨进程取消 / 重复投递守卫 / broker 重连
- 迁移：fresh DB up head → down 0033 → up head ✔，单 head ✔
- ruff（全部改动文件）：0 errors；OpenAPI 快照：字节一致 ✔

## Review Round 1 findings/fixes

BLOCKER=0 CRITICAL=2 MAJOR=5 MINOR=9 NIT=8 → 全部 C/M + 正确性 m/n 已修
（详见 05-review-findings.md）。要点：心跳覆写 capability（C1）、覆盖判断
误用 capabilities（C2）、reset 账本泄漏（M1）、守卫终态落库（M2）、探针
诚实性（M3）、straggler 心跳判定（M4）、恒真断言（M5/m9）。

## Review Round 2 findings/fixes

BLOCKER=0 CRITICAL=0 MAJOR=2 MINOR=4 NIT=6 → 全部已修。要点：E2E 预算对齐
CI 泳道 timeout（RM1）、局部性注册表生产读取方 + live_workers 限界 + rank
诚实收缩（RM2）、capability 解析缓存（Rm1）、事件投影伪名化（Rm2）、守卫
finalize 返回值检查（Rm3）、bridge 超时取消+在飞上限（Rm4）等。

## Rebase / integration verification

origin/master 在分支生命周期内未移动（8a33e3a5）；rebase = up-to-date；
OpenAPI 快照与迁移链在 HEAD 复验一致。

## Backward compatibility

- eager（无 Redis）逐字节不变（P4b 零事件写精确锚 + V6 全量回归）
- /plans/execute 零变化；submit 新字段可选；DB additive 可空
- 行为 delta 显式披露：prune_workers 返回列表（原 int）、straggler 误报消除、
  events TTL 排除活跃 run

## Known limitations（诚实边界）

1. 节点级放置不承诺消息级绑定（Celery 共享队列模型）——强制层 = run 级
   gating + worker 守卫有界收敛（ADR-0119 D1）
2. events 是尽力而为 trace（分层预算/fail-open），终态证据 of record 仍是
   geocompute_run_evidence
3. worker 缓存 v1 = 位置声明 + 进程内 LRU，无 worker 间取数协议
4. rank/locality advisory 未接入生产 dispatch 决策（共享队列模型下无绑定
   语义；已提供 admin 可见性与扩展 API）
5. Postgres 未在本地验证（不可达）；控制面 E2E 用共享 SQLite（WAL +
   busy_timeout），CAS 单语句条件 UPDATE 与 PG 同构；CI real-services lane
   可复跑
6. straggler 处置仍交给既有 stale sweep/reclaim（事件只做可见性）

## Follow-up candidates

- per-worker 专用队列（若部署需要消息级绑定）
- events → OpenTelemetry exporter（词表已同形）
- straggler 主动迁移；events TTL 与 run retention 解耦为独立配置
- worker 缓存 v2：本地盘持久化 + 跨 worker 取数协议
