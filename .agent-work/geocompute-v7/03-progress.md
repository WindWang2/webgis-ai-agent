# GeoCompute V7 — 实现进度

## Waves 状态（基线 origin/master 8a33e3a5）

| wave | 内容 | 状态 | commit |
|---|---|---|---|
| 1 | Phase A 审计 00-baseline.md | ✅ | df84836e |
| 2 | Phase B 架构冻结 + Subagent-A 挑战修订 | ✅ | a5077764 |
| 3-8 | contracts/DB/migration0034 + capabilities/events/locality/placement/object_cache 模块 + durable 穿透/自适应退避 + worker 准入守卫/input handoff | ✅ | c2806dde |
| 9-11 | placement gating 接线/分布式事件/straggler/级联清理/capability 注册/bridge 重写 | ✅ | de160e98 |
| 12 | REST（resource/events/admin/metrics）+ OpenAPI 快照（310 additive） | ✅ | c08c6fea |
| 13 | V7 单测 core(29) + cluster(15) | ✅ | （本 wave） |
| 14 | real-broker E2E（5 场景） | 🔄 主线 happy/cancel/dup/restart 验证中；crash 对账修复中 | |
| 15 | 结构性 perf 预算 | ✅ 已写，待跑 | |
| 16 | ADR-0119 + CHANGELOG + docs | ✅ | |

## 执行中发现并修复的问题（真实记录）

1. **V6 潜伏 P1（最重要）**：`worker_ready` 闭包 handler 被 GC
   （kombu Signal.connect 默认 weak=True）→ 真实 worker 的注册/心跳
   **从未生效**。V6 全部测试 eager、real-services lane 不跑 geocompute
   worker → 缺陷潜伏至 V7 E2E 暴露。修复 = `weak=False` 强引用注册，
   已用真实 worker 验证（geocompute_workers 行出现 + capability 非空）。
2. **E2E 环境层缺陷链**（测试侧，均已修）：
   - conftest 把 CELERY_BROKER_URL 钉进 os.environ（env 源优先于 conf
     赋值）→ 按 production fixture 惯例 setenv；
   - jobs 子系统（submit 的 db_session、await 轮询的 session_factory）
     必须与共享控制面同库 → 测试注入工厂；
   - 跨进程会话存储：conftest 默认 memory → 测试进程与 worker 都指向
     同一 Redis db（session ref 交接才成立）；
   - pytest-timeout 杀死测试时 fixture teardown 不跑 → 孤儿 worker 用
     死库领走消息（真实事故：n2 消息被孤儿消费出 no such table）→
     broker db 随机化 + atexit 兜底；
   - 测试用绝对阈值墙钟断言（bridge 8×20ms < 0.08s）在满载下脆弱 →
     改自相对结构断言（并发 < 串行×1/2）。
3. **架构 round1 挑战全部采纳**（见 01-architecture.md 修订注记）：
   placement 三层语义 / events 分层预算+fail-open / progress 读投影 /
   owner 缓存契约 / bridge 挂死防护 / required_profiles union 语义。

## 环境注记

- worktree 缺 data/webgis.db 完整 schema（organizations 表缺）→
  `Base.metadata.create_all` 补齐（本地开发库，非代码问题）。
- 本地 Redis 可达（db 0-15）；Postgres 不可达 → E2E 控制面用共享
  SQLite（WAL + busy_timeout 30s），CAS 语义单语句条件 UPDATE 与 PG
  同构。
