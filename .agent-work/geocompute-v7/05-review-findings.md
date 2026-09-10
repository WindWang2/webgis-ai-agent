# GeoCompute V7 — Review 发现与修复记录

## Review Round 1（correctness 视角，全量 diff ~5280 行）

结论：BLOCKER=0 CRITICAL=2 MAJOR=5 MINOR=9 NIT=8 → 全部 CRITICAL/MAJOR +
正确性相关 MINOR 已修复（commit 740aaeec）。

### CRITICAL（已修复）

| # | 发现 | 修复 |
|---|---|---|
| C1 | worker 心跳走 upsert_worker 把 capability 每 10s 覆写回 NULL → 能力放置静默失效（E2E 断言落在首跳前窗口，未暴露） | WorkerHeartbeatThread 改用 worker_heartbeat 单列续期；E2E 就绪判定改为注册行轮询（天然跨心跳窗口） |
| C2 | eligible_workers 用 capability.capabilities 放宽队列覆盖 → 「raster 后端存在但不消费 raster_queue」的 worker 通过 gating → 必然 WORKER_LOSS；且探针把全部词写入 capabilities 使 union 恒真 | 覆盖判断只用 profiles（队列消费真相）；回归锚单测锁定（声明 raster 后端但不消费 raster_queue → 不合格） |

### MAJOR（已修复）

| # | 发现 | 修复 |
|---|---|---|
| M1 | admin reset 路由不传 ledger → 每次 reset 永久泄漏一份账本预留 | 路由构造与 store 同 factory 的 ClusterLedger 传入 |
| M2 | placement 守卫终局失败在 durable_job 认领前抛出 → job 行永久 queued、类型化证据全丢 | 守卫返回 "failed" → _finalize_placement_failure 经生产 mark_failed_sync 落 job 行 failed（终局事件仍由 coordinator 统一发） |
| M3 | 探针把全部能力词无条件写入每个 worker（无 rasterio 报 raster）→ 能力列零信息量 | capabilities 只由可证事实推导（backend import 成功 / gpu 计数 / cpu·mem 阈值）；network/external_io 不声明；probe 诚实性单测 |
| M4 | _detect_stragglers 用永不更新的 started_at → 任何健康 run 超 0.3s 即误报（实测吻合）；真卡死的 LEASED 行反被跳过 | _scan_projection 增加 heartbeat_at；判定改 heartbeat_at < now−max(3×interval, 1s) |
| M5 | p4b「同步路径零事件写」断言恒真（count >= 0） | QueryCounter 记录 SQL 文本，精确断言 statements_touching("geocompute_run_events") == 0 |

### MINOR（正确性相关已修复；其余按建议收口）

- m1 prune 漏失联 coordinator 行 → 已一并清理（standby 下次 acquire 重建）✅
- m2 缺 session 的 input_refs 静默跳过 → typed 失败 ✅
- m3 zone 落库不消费 → rank 增加 zone 契合 advisory 项 ✅
- m4 events TTL 误清活跃 run 事件 → purge 排除非终态 run ✅
- m5 worker 缓存字节总闸未实现 → record_put 补 SUM+LRU 逐出 ✅
- m6 worker 侧终局事件与单一来源契约矛盾 → 已删（保留 started/output_ready/cache_hit）✅
- m7 512 预算对 128+ 节点 DAG 提前触顶 → 1024（256 节点 × 3 非终局 + 余量）✅
- m8 crash 测试文档措辞 → 已在重写中对齐（直接驱动生产 transition_sync，sweep 谓词由 jobs 子系统测试覆盖）✅
- m9 恒真断言 → 行为化改写（ghost append 落表可读；probe 诚实性断言）✅

### NIT（已收口）

n1 run_started 不再误用 epoch 当 attempt ✅；n2 waiting_resource reason 短词表 ✅；
n3 input_keys 注解改 dict ✅；n4 mem_mb 上界 ✅；n5 exists() NULL 语义隔离
coordinator/worker 事件 ✅；n6/n7 竞态上界与计量口径注释 ✅；n8 保留（E2E 断言
已限定 AlreadyFinished 语义文本， OR 条件为防御未知收窄）。

### Round 1 确认无发现项

幂等键不含治理元数据 ✔ / 自适应轮询不劣化取消传播 ✔ / purge 级联同事务 ✔ /
progress 投影重跑覆盖语义 ✔ / emit_events fail-open 链 ✔ / bridge 并发与超时 ✔ /
路由认证与 owner 隔离 ✔ / E2E 真生产路径 ✔。

## Review Round 2（性能/安全/兼容视角，同一审查者复用）

结论：BLOCKER=0 CRITICAL=0 MAJOR=2 MINOR=4 NIT=6 → 全部 MAJOR/MINOR/NIT
已修复（commit 78fe2efb）。Round 1 修复的 16 项中 15 项被确认收口，唯一
残留（n3 注解）本轮已收。

| # | 发现 | 修复 |
|---|---|---|
| RM1 | E2E 内部时间预算超出 CI real-services 泳道 --timeout=180 → 负载下必红且 teardown 跳过 | 模块级 `pytest.mark.timeout(560)`（item marker 优先于 CLI）+ 内部预算压缩（boot 90→75、terminal 等待 240→150、kill 循环 120→75） |
| RM2 | 局部性 rank/registry 在生产路径只写不读；/cluster/workers docstring 承诺缓存占用但未实现；live_workers 无 LIMIT | admin 投影并入 cache_entries/cache_bytes（注册表获得生产读取方）；live_workers SQL 级 LIMIT 256；rank docstring 诚实收缩（生产 dispatch 不消费，供扩展） |
| Rm1 | capability 每 candidate×worker 重复 pydantic 解析（最坏 8192 次/tick） | per-worker dict 缓存（_parsed_capability），gating+rank 各阶段复用 |
| Rm2 | events 用户投影泄漏 hostname:pid 拓扑 | 读投影伪名化（w-<sha1:12>），DB 保留原文供 admin |
| Rm3 | _finalize_placement_failure 兜底声明不成立 + rowcount 未检查 | 检查 mark_failed_sync 返回值；False 时 raise（celery 可见），文档对齐 admin stuck/reset 面 |
| Rm4 | bridge 超时不取消协程、无在飞上限 | 超时 fut.cancel()；BoundedSemaphore(128, env 可调) 满载 typed BridgeTimeoutError |
| Rn1 | _resolve_inputs input_keys 注解 list→dict | ✅ |
| Rn2 | standby 心跳 churn（prune→重建每 TTL 循环） | acquire_leadership 对已存在行刷新 heartbeat_at ✅ |
| Rn3 | 缓存 TTL 逐行 DELETE | 合并 or_(*conds) 单条删除 ✅ |
| Rn4 | perf 测试无 marker 跑在主泳道 | 保持主泳道（实测 21s < 60s 含 coverage），不改 CI 枚举（注释说明） |
| Rn5 | zone 无字符白名单 | Field pattern ^[A-Za-z0-9_.-]{1,64}$ ✅ |
| Rn6 | bridge 加速比门偶红风险 | 3 轮取最小值 ✅ |

### Round 2 确认无发现项

tick 查询上界全部有界 ✔ / events 1024 预算量级论证 ✔ / SQLite 并发短事务
无饿死面 ✔ / 注入面（resource_request/capability 词表+钳制）✔ / admin
越权 ✔ / PayloadCache 跨租户无可达命中 ✔ / 秘密与隐私 ✔ / PG 可重入 ✔ /
eager 回归精确锚定 ✔。

### 过程中发现并即时修复的额外问题

- 数据面边界（ADR-0096 D1）：_finalize_placement_failure 误引
  app.tools._utils → boundary 测试拦截 → 改 jobs 层工厂（durable.py 同款）。
