# GeoCompute V7 — 实现计划（waves 对齐 Epic §14）

执行序（按审计重排，scope 不减）：

1. ✅ v6 audit（00-baseline.md）
2. ✅ 契约层：ResourceRequest / capability 词表 / 事件词表（contracts.py + 各新模块）
3. ✅ DB：workers.capability / runs.resource_request 列 + geocompute_run_events /
   geocompute_worker_cache 表（migration 0034，单 head，可重入 DDL）
4. ✅ capability 探针（诚实降级，worker_ready 一次）
5. ✅ events store（分层预算 / fail-open / after_id 游标 / 读时 progress 投影）
6. ✅ locality（键派生 / owner 域缓存注册表 / TTL / prune 级联）
7. ✅ placement（三层语义：run 级 gating 强制 / worker 守卫有界收敛 / rank advisory）
8. ✅ worker 准入守卫 + input handoff + worker 本地载荷缓存（object_cache.py）
9. ✅ scheduler 接线（gating / waiting_resource 去重 / run 事件 / straggler /
   purge 级联 / cache TTL sweep / queue-wait 采样）
10. ✅ executor emit_events（opt-in，节点终局单一来源）+ durable run_id/resource 穿透
11. ✅ _async_bridge 专用 loop 重写（supervisor / 超时 / 同线程护栏）
12. ✅ REST：submit resource（union + 422）/ events 端点 / admin 四端点 / metrics 扩展
13. ✅ 单测：core 29 + cluster 15 + perf 6（含 bridge 相对加速比）
14. 🔄 real-broker E2E（5 场景；happy 已过；crash/cancel/dup/restart 收敛中）
15. ✅ ADR-0119 + CHANGELOG + docs + 进度/测试矩阵记录
16. ⏳ Review Round 1（Subagent-A）→ 修复 → Round 2（Subagent-B）→ 修复
17. ⏳ rebase master（共享文件语义合并）→ 本地验证矩阵 → push → PR

关键设计决议（与 01-architecture.md 一致）：
- 不加 progress 列（读投影）；不加 per-run seq（全局 id 单调）；
- 不做 worker 间取数协议（缓存是位置声明，失败方向 miss）；
- 不声称节点级硬绑定（共享队列模型的诚实边界）。
