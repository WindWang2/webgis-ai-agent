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

## Review Round 2（性能/安全/兼容视角）

（进行中，见 git log 后续 commit）
