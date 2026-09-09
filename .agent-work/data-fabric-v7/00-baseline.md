# Epic 03 — Data Fabric & Federated Spatial Query V7 基线审计

- 分支：`feat/data-fabric-v7-adaptive-federation`
- worktree：`/home/kevin/projects/webgis/webgis-ai-agent-data-fabric-v7`
- base SHA：`8a33e3a5`（origin/master，2026-09-09 fetch 后一致）
- 前身：PR #1167（feat/query-v6 Federated Spatial Query Optimizer V6，ADR-0118）
- 并发 worktree：`feat/contextual-cartographic-harness-v6`（cartography 域，冲突面低）

## 1. 生产入口与调用链（file:line 证据）

| 入口 | 路径 | 说明 |
|---|---|---|
| Agent 工具（2 源） | `app/tools/data_fabric_tools.py:802` `query_federated_data` | 走 `FederatedExecutor.execute`（V5 内核） |
| Agent 工具（N 源） | `app/tools/data_fabric_tools.py:913` `query_federated_chain` | `engine="v6"` 默认（:920）；adapter 解析走 `connection_manager.get_adapter(pid, owner=session_id)` |
| REST 面 | `app/api/routes/data_fabric.py` | create/list/probe/query/materialize/explain；`create_data_source`（:306）→ `DataFabricManager.create_data_source` |
| 单源查询 | `app/services/data_fabric/manager.py:376` `query_catalog_item` | normalize → plan → breaker 包裹 adapter.query |
| 单源 explain | `manager.py:618` `explain_catalog_item` | 探测后 capability（:671）+ 统计注入（:684） |
| 联邦 V6 | `app/services/data_fabric/query/federation.py:527` `execute_chain` engine 分派；:1694 `execute_chain_v6` | 非 typed 异常回退 V5（:1771） |

## 2. 事实源盘点

| 真相 | Owner | 备注 |
|---|---|---|
| source_type → adapter | `registry.py` `_build_registry`（append-only AdapterRegistry） | 11 canonical types |
| 连接（DB 持久） | `app/models/data_fabric.py:14` `DataSourceModel`（data_sources 表，org_id/owner_id） | REST 路径 |
| 连接（进程内） | `connection_manager.py:266` `DataFabricConnectionManager` 单例，键 `(owner, profile_id)` | 工具联邦路径的 adapter 来源 |
| catalog | `CatalogItemModel`（spatial_catalog_items）+ descriptor fingerprint | |
| 统计 | `query/statistics.py` `DatasetStatistics`；`StatisticsStore`（进程 TTL 60s，fingerprint 键）+ `DurableStatisticsStore`（dataset_statistics 表，advisory fail-open） | **无 owner/租户作用域**（fingerprint 全局键） |
| capability | `query/capabilities.py` 静态默认矩阵 + adapter `capabilities_v2()` 覆盖 | OGC conformance 探测 60s 进程内 per-instance（`ogc_api_adapter.py:55`），**不持久** |
| 反馈 | `query/feedback.py` `PlannerFeedbackStore`（进程内，TTL 3600s，winsorized 中位数） | **无持久化、无租户作用域** |
| 结果缓存 | **不存在**（仅 tile cache `_DfTileCache` routes:241，MVT 专用） | |
| 预算 | `ExecutionBudget` / `StreamingBudget`（query/execution.py） | |

## 3. 关键缺口（对应 Epic Must-have）

### A. Connection Registry V7
- `connection_manager.py`：纯内存 dict；无 revision/CAS、无 TTL/过期、无生命周期清理（adapter 永驻）、无健康状态机（仅 connect 时 probe 一次）。`owner=None` legacy 全局连接对所有会话可见（:316-319）。
- DB `DataSourceModel.connection_profile` JSON **明文存储 password/secret_key/access_key**（`manager.py:159` 存 REAL profile）。egress 有 `sanitize_profile_dict`，但 at-rest 无加密 seam。
- **持久连接重建丢字段**：`manager.py:202-209 / 393-400 / 445-452 / 518-525` 从 DB 重建 `ConnectionProfile` 只带 `options` —— 顶层 `username/password/credentials` 被丢弃；DSN 内嵌凭证经 `model_post_init`（schema:43）幸存。durable reference vs secret separation 缺失。
- 无 expiration / revocation 语义；无 connection health 记录（`status`/`last_health_check` 列存在但仅在 create 时写一次）。

### B. Capability probing
- 静态默认 + OGC conformance（CQL2 升级 filter_pushdown）。缺失：探测结果不持久、无 scope、无 rate-limit / max page size / server cost hints 探测（ArcGIS maxRecordCount 仅注释提及，`capabilities.py:105`）、无 caps 变化检测。

### C. SourceFacts
- `DatasetStatistics` 已有 row_count/extent/crs/avg_vertices/spatial_histogram/collector/confidence/revision_strength。
- 缺：NDV 只有 postgis pg_stats 路径；null fraction 仅 pg_stats；无 temporal extent/freshness；统计缓存无租户作用域（durable store 按 fingerprint 全局共享——同一远程数据集跨租户其实是同一事实，可接受但需显式化）；无 TTL/revision 显式化到 planner 证据。

### D. Standards
- OGC API Features：有（CQL2-Text 探测门控）。CQL2-JSON 未实现（编译器只产 text，`compilers.py:291`）。
- STAC：有 search；无 CQL2 filter 扩展探测。
- WFS/GeoParquet/FlatGeobuf/PMTiles/ArcGIS：有。
- **server CRS transform placement**：`costing.py:202` `decide_crs_transform(allow_server=)` 已产 server 决策，但 physical 层从未实现（QuerySpec 无 output.crs 字段）——ADR-0118 Known Limitation #2。

### E. Cost/Placement V7
- V6 已有：传输字节/基数/CRS/Bloom 盈利/聚合下推收益估价；bushy DP（n≤4 全子集，k=3 剪枝）。
- 缺：rate-limit 成本、estimate 不确定性/置信度进成本、bushy 子树估计传播（adaptive 只支持链形，`adaptive.py:9`）、**安全聚合下推**（ADR-0118 KL#6：aggregate_join 语义是"连接后聚合"，源侧 GROUP BY 统计未命中行 → 语义不安全，被 EXPLAIN 拒绝）。

### F. Streaming physical
- `physical.py` `iter_scan_pages`：有界页 + 取消 + 预算。缺：Arrow/GeoArrow batch 路径未接入联邦执行（`arrow_ops.py`/`vector_carrier.py` 存在但服务于 materialization lane）；无 spill。

### G. Distributed feedback
- 进程内 `PlannerFeedbackStore`。缺：bytes/latency/throttle/error 捕获、持久化、owner/project 作用域、decay/versioning、SourceFacts 更新回路。

### H. Query result cache
- 完全缺失。

### I. Consolidation
- V6 已是工具默认；V5 fallback + 差分语料（`test_federated_v6_differential.py`）已存在。收敛需先证明 V7 等价。

## 4. 已知限制继承（ADR-0118 Known Limitations）

1. CQL2-JSON 下推（deferred）
2. server-side CRS 变换放置（QuerySpec 需 additive output.crs）→ **本 Epic 实现目标**
3. build 侧 fetch 窗口放宽（语义契约变更，需独立决策）
4. bushy 自适应重排（需子树 estimate 传播）→ **本 Epic 实现目标**
5. 跨进程 feedback / 查询结果缓存 → **本 Epic 实现目标**
6. 聚合下推语义不安全（连接后聚合 vs 源侧 GROUP BY）→ **本 Epic 需给出 safe 路径（如 count-only 下推 + 显式语义模式）或维持诚实拒绝**

## 5. 资源复杂度

- `iter_scan_pages`：O(pages × page_size)，页间预算检查 ✅
- build 侧 join 候选：`MAX_JOIN_CANDIDATES` 硬界 ✅
- Bloom：≤1M bits ✅
- StatisticsStore：TTL+LRU 1024 ✅
- feedback：512 keys × 8 obs ✅
- **connection adapters：无界驻留**（每次 connect 泄漏一个 adapter 实例直至 clear）❌
- conformance 探测：per-adapter-instance 缓存，adapters 频繁重建时重复网络请求 ❌

## 6. 失败/取消/恢复语义

- typed `DataFabricError` 族 + breaker registry（`circuit_breaker.py`）
- V6 非 typed 异常 → 一次性 V5 回退 + warning 披露
- 取消：`CancelToken`（deadline + event），逐页检查
- 统计/反馈：advisory fail-open，绝不阻断查询 ✅（文化保持）

## 7. 安全边界现状

- SSRF：`security.py` validate_url（全部 resolved A/AAAA、IPv4-mapped、metadata IP）+ `SSRFSafeHTTPAdapter`（每跳重校验）+ `ensure_same_origin_url`（cursor 防护）+ bounded_get（解压炸弹）
- 凭证：egress sanitize（递归）；headers 子树整体敏感
- XXE：defusedxml
- 本地路径：`resolve_safe_local_path`
- 缺：连接凭证 at-rest 加密 seam、credentials 与 durable reference 分离

## 8. 与并发 Epic 的共享文件冲突面

| 文件 | 风险 | 策略 |
|---|---|---|
| `app/core/config.py` | 中（additive settings） | 只追加 DATA_FABRIC_V7_* |
| `.env.example` | 中（模板奇偶闸强制登记） | 新 settings 必须同步登记 |
| `app/tools/data_fabric_tools.py` | 中 | additive 参数/字段 |
| `app/schemas/data_fabric_schema.py` | 中 | QuerySpec additive 字段 |
| `CHANGELOG.md` | 低 | 最小追加独立小节 |
| `app/models/data_fabric.py` + migration | 中 | 新 migration 链 `0033_geocompute_v6_cluster`（当前唯一 head） |
| ADR | 低 | 现最高 0118（5 个同名不同主题）；新用 **0119** |
| `docs/quality/generated-artifacts.json` / manifests | 低 | 仅当权威输入变化时再生成 |

## 9. 测试现状

- 联邦域测试 ~25 文件（unit）：V5/V6 差分、bloom、costing、enumerator、physical、adaptive、explain、spatial_stats、mirror parity、engine wiring、pushdown
- data lane：`scripts/quality_runner.py data`（tests/data/ + 指定 fabric 单测）
- 基准：`test_data_fabric_benchmark*.py`、`test_federated_v6_benchmark.py`（结构性预算）

## 10. P0/P1/P2/P3 分级

- **P1**：连接重建丢凭证字段（功能缺陷：带独立 password 字段的持久源二次使用必失败）——本 Epic Connection Registry V7 修复 + 回归测试
- **P1**：adapter 无界驻留（生命周期缺失）
- **P2**：凭证明文 at-rest（无加密 seam；redaction 只在 egress）——V7 提供 secret separation（durable reference + secret store seam），加密由后续 hookable provider 决定
- **P2**：capability/统计探测无持久化与 scope
- **P2**：无查询结果缓存、无跨进程 feedback
- **P3**：CQL2-JSON、STAC filter 扩展探测
