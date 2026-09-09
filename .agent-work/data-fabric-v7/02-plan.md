# V7 实施计划（waves → commits）

基线 8a33e3a5；架构见 01-architecture.md（含 Subagent-A 挑战修订 R-C1/C2/C3、R-M1/M2/M3）。

| Wave | 内容 | 主要文件 | 测试 |
|---|---|---|---|
| W1 | fabric 包 + ConnectionRegistry（scope/revision=content-hash/TTL/health/SecretStore/生命周期驱逐） | fabric/connection_registry.py | test_fabric_connection_registry.py |
| W2 | 修 P1：DB 重建 profile 恢复凭证字段；manager/工具桥接 registry | manager.py, connection_manager.py | 同上 + test_data_fabric_services 回归 |
| W3 | capability 探测服务 + scoped 缓存 + rate-limit 观测 + ArcGIS maxRecordCount | fabric/probing.py, capabilities.py(models additive) | test_fabric_probing.py |
| W4 | SourceFacts（采集 plumb-only + durable 表 + migration 0034） | fabric/source_facts.py, models, migration | test_fabric_source_facts.py |
| W5 | CQL2-JSON 编译器 + OGC cql2-json + STAC filter 探测 + 注入回归 | compilers.py, ogc/stac adapters | test_fabric_cql2_json.py |
| W6 | Arrow 批通道（可选依赖，typed 回落） | physical.py, adapters/geoparquet | test_fabric_arrow_lane.py |
| W7 | cost V7：rate-limit 成本 + 不确定性乘子 | costing.py, enumerator.py | test_fabric_cost_v7.py |
| W8 | server CRS placement（R-C2 全案：不变量修复 + 交付账本 + ArcGIS 验证重试） | physical.py, enumerator.py, costing.py, adapters | test_fabric_server_crs.py |
| W9 | safe aggregate pushdown（R-C1 五条件） | enumerator.py, physical.py | test_fabric_aggregate_pushdown.py |
| W10 | bushy adaptive（子树 pinned 重排，R-M3 口径） | adaptive.py, executor.py | test_fabric_bushy_adaptive.py |
| W11 | distributed feedback（R-C3 守卫 + 持久/衰减/回写） | fabric/feedback.py, executor.py | test_fabric_feedback.py |
| W12 | result cache + 负缓存 + owner 隔离 + 失效 | fabric/result_cache.py, federation.py | test_fabric_result_cache.py |
| W13 | counters + EXPLAIN v7 fabric 段（caps_basis/失败计数） | fabric/counters.py, explain.py, executor.py | test_fabric_counters_explain.py |
| W14 | SSRF/安全加固回归 + 连接过期语义 | security 相关 | test_fabric_security_v7.py |
| W15 | 3~4 源差分语料（exact/序不敏感、mixed CRS、replan on/off） | tests | test_fabric_v7_differential.py |
| W16 | perf/chaos 结构性 benchmark（请求数/字节/行数预算） | tests | test_fabric_v7_perf.py |
| W17 | consolidation 验证 + ADR-0119 + docs/CHANGELOG/.env.example | docs | — |
| R1/R2 | 两轮 review 修复 | — | — |

## 已知风险
- W8 触碰 V6 物理/枚举核心 —— 每步跑 test_federated_* 全量防回归。
- W12 `use_cache` 默认 true 是有意行为 delta（缓存披露 + fingerprint 失效 + TTL 300s 背书）。
- migration 链 0033；rebase 时单 head 复核 + 必要时重编号。
