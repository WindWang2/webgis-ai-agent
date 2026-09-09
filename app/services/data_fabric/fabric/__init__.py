"""Data Fabric V7 — Adaptive Distributed Spatial Data Plane（ADR-0119）。

联邦数据面治理层，位于 V6 优化器（``query/federated/``）之下：

- ``connection_registry``：Connection Registry V7（owner/org/project 作用域、
  content-addressed revision、健康状态机、过期与生命周期驱逐、secret 分离）；
- ``probing``：provider capability/统计探测 + 作用域缓存 + rate-limit 观测；
- ``source_facts``：SourceFacts 记录/采集/持久层（advisory，fail-open）；
- ``feedback``：分布式执行反馈（持久、作用域、时间衰减 → SourceFacts 回路）；
- ``result_cache``：联邦查询结果缓存（revision 键控、命中必披露）；
- ``counters``：per-execution 结构性计数器。

设计红线（与 V6/statistics 同纪律）：advisory 层 fail-open 绝不阻断查询；
secrets 永不进 record/feedback/EXPLAIN；新增公共契约全部 typed + 有界。
"""

from app.services.data_fabric.fabric.connection_registry import (
    ConnectionExpiredError,
    ConnectionRecord,
    ConnectionRegistry,
    InMemorySecretStore,
    SecretStore,
    TenantScope,
    get_connection_registry,
)

__all__ = [
    "ConnectionExpiredError",
    "ConnectionRecord",
    "ConnectionRegistry",
    "InMemorySecretStore",
    "SecretStore",
    "TenantScope",
    "get_connection_registry",
]
