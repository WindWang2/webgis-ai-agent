"""Server-side collaboration primitives (Workbench V6).

分层不变式（ADR-0119）：真相（mapspec.workbench + `_cartographic_mutation_revision`
CAS）与通知平面（本包）严格分离 —— 正确性**绝不依赖**收到总线事件；任何一环
失效都退化为「下次自身提交时的 CAS 收敛 + revision 对账」，永不错误。

- ``bus``      —— 跨进程事件分发（Redis pub/sub；无 Redis = 进程内降级）。
- ``presence`` —— 瞬态参与者状态（TTL、有界、join/heartbeat/leave/快照）。
- ``leases``   —— 瞬态编辑租约（advisory；持久意图锁仍是 agent 硬闸）。

所有 Redis 访问 fail-open（进程内降级 + 退避重探），与 cache_broadcast 的
既定模式一致；瞬态记录全部带 TTL/惰性清扫 —— 无永久孤儿状态。
"""
