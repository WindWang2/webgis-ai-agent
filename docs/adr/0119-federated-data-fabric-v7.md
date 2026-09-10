# ADR-0119: Federated Data Fabric V7 — Adaptive Distributed Spatial Data Plane

## Status
Accepted（Epic 03，2026-09-09；前身 ADR-0118 Federated Spatial Query Optimizer V6）

## Context
V6 优化器（cost-based join 树枚举/流式批执行/Bloom/自适应链重排）之下缺少
真正发挥优化器价值的数据面治理：连接生命周期、provider 能力/统计探测、
作用域化事实、分布式反馈、结果缓存、server-side 放置。V6 Known Limitations
明确遗留：server CRS 变换放置（#2）、bushy 自适应（#4）、跨进程反馈与查询
结果缓存（#5）、聚合下推语义不安全（#6）。

## Decision
V7 **不是第三套查询引擎** —— 是 V6 之下的联邦数据面治理层（`data_fabric/fabric/`）
+ 对 V6 follow-up 的选择性兑现。`engine` 参数保持 `v5|v6`，无 contract drift。

### 1. Connection Registry V7（fabric/connection_registry.py）
- `TenantScope(org/owner/project)` 键控；显式全局域（legacy 语义）保留但
  **禁止入结果缓存**；content-addressed revision（redacted profile +
  secret_ref 的 sha256 前 16）—— 跨进程一致，进程内 CAS 锁内比较；
- secret 分离 seam（`SecretStore` Protocol + 进程内 TTL/容量双界实现）：
  record 只存 opaque ref；durable at-rest 加密是 hookable provider follow-up
  （DataSourceModel 明文 JSON 列不变，本 ADR 披露）；
- 健康状态机（unknown/healthy/degraded/unreachable/expired）+ idle TTL +
  容量上限 LRU 驱逐（修复 adapter 无界驻留）；
- 修复 P1：DB 重建 `ConnectionProfile` 恢复 username/password/credentials
  顶层字段（此前仅 options 幸存，持久源二次使用必失败）。

### 2. Provider Capability Probing（fabric/probing.py）
- 组合既有探测点（OGC conformance→CQL2-Text/JSON、ArcGIS maxRecordCount、
  STAC filter extension）；scoped 缓存（scope+profile+revision，TTL 300s）；
- `ProbeCost`（requests/bytes/latency）结构性记账 —— 探测代价可披露；
- rate-limit **被动观测**（Retry-After/X-RateLimit-* 头解析原语）；探测
  失败回落静态默认矩阵（caps_basis=default），绝不编造。

### 3. SourceFacts（fabric/source_facts.py + migration 0034）
- `SourceFactsRecord`：row_count（exact/observed/estimate/unknown 标注）、
  extent、crs、geometry_family、ndv、null_fraction、spatial_histogram、
  avg_geometry_complexity、temporal_extent、freshness、provenance（含
  sampled 标注）、TTL/revision；
- 采集纪律 **plumb, not scrape**：只收割响应本就携带的事实；持久层
  advisory fail-open（append-only + prune，scope 进行 —— 跨租户不串）。

### 4. Server CRS Transform Placement（兑现 ADR-0118 KL#2）
- 发现并修复 V6 潜在缺陷：PostGIS/ArcGIS 默认交付 4326 而规划器按声明
  原生 SRID 做本地对齐 → 原生≠4326 的源混合 CRS 链双重变换（静默错坐标）。
- 修复 = placement 本体：`QuerySpec` extra `output_crs`（已验证通道：
  PostGIS ST_Transform 输出包裹 → caps.output_crs_pushdown=True）；
  ArcGIS f=geojson 无响应端 SR 校验通道 → **不声明**（诚实）；WFS/OGC-API
  轴序/协商歧义 → 不声明；
- 执行期 CRS 账本以 **delivered_crs metadata 事实**为准（PostGIS/ArcGIS
  自报）；server 交付不符（忽略/硬拒绝）→ **去 output_crs 一次性重扫** +
  本地 pyproj 变换 + trace 披露（覆盖"报错≠忽略"缺口）；单扫描 reproject
  节点按交付 srid 数值变换；
- 双通道分离：过滤 bbox 语义不变（WHERE 作用于原生列）；output_crs 只
  影响交付几何编码。值白名单（EPSG:\d{4,5} / OGC:CRS84）。

### 5. Safe Aggregate Pushdown（收口 ADR-0118 KL#6）
- 语义陷阱：aggregate_join = join 后按右列分组聚合，右行随左侧匹配数
  重复计数；源侧 GROUP BY 每右行计一次 → fan-out>1 不等价（V6 拒绝的原因）。
- R-C1 五条件（全满足才下推）：纯属性等值 join；group_by ⊇ join 键；
  左键唯一性 measured 级证明（`stats_hints.unique_keys` 显式声明；估计
  NDV 不作数）；聚合函数 ⊆ {count,sum,min,max,avg}；右源 aggregation cap。
- 等价性论证：唯一左键 ⇒ 每右行至多一匹配；组含 join 键 ⇒ 组内右行全
  匹配或全不匹配（不匹配组被最终 join 丢弃）⇒ 源侧 GROUP BY ≡ join-后
  聚合（逐位）。差分语料锁定（下推 on/off 同结果）。

### 6. Bushy Adaptive Replan（兑现 ADR-0118 KL#4）
- 执行后观测基数偏差 ≥4×（与链形同阈）→ **一次**整树重排：观测行数
  pinned 回同一 planner 重枚举，新计划 hash 不同**且严格更优**才重执行；
  `order_strategy="given"` 禁用；MAX_REPLANS=1；
- 确定性口径（显式决策）：观测驱动重排是"计划可复述、不可逐位复现"；
  差分 oracle 用序不敏感比较；explain 披露 pinned 证据。

### 7. Distributed Feedback（fabric/feedback.py）
- `ExecutionFeedback`（per-source 行数/字节/时延/错误类/限流/unfiltered
  标注）；只学 outcome=ok；指数半衰期（30min）加权中位数修正因子；
- **R-C3 契约不破坏**：仅无过滤扫描的观测行数回写 SourceFacts
  （`observe_unfiltered_count`）；过滤观测只进修正因子；
- 持久层 advisory fail-open（表 `data_fabric_federated_feedback`，append-
  only + prune 上限 20k）；无 secret 面。

### 8. Query Result Cache（fabric/result_cache.py）
- 键 = sha256(scope + canonical request + Σ per-source descriptor
  fingerprint + engine)；TTL 300s + 条目/字节双界 LRU；
- **命中必披露**：`result_cache={hit, age_s, ttl_s, basis="ttl+fingerprint"}`
  —— 无 stale silent success；fingerprint 变化 → miss + 失效；
- 负缓存：仅 SOURCE_UNREACHABLE / SOURCE_AUTH_FAILED，TTL 30s、容量 64；
  预算/取消绝不缓存；全局域连接禁入。

### 9. Counters + Explain（fabric/counters.py）
- per-execution `FabricCounters`：remote_requests / pages / bytes /
  rows_materialized / peak_streaming_bytes / server placements / fallbacks /
  aggregate pushdowns / cache hit / replans / probe requests；
- `execute_chain_v6` 结果 additive `fabric` 段 + `per_source_delivered_srid`
  + `crs_fallbacks`。

### 10. Cost V7（costing.py additive）
- 估计不确定性乘子（measured 1.0 / estimated 1.15 / assumption 1.5 ——
  确定性、EXPLAIN 可复述）；rate-limit 每请求惩罚（已知限率 → base/limit；
  未知 = 0，绝不虚构）。

### 11. Standards（W5/W6）
- CQL2-JSON 编译器（结构化无注入面；OGC conformance 声明 cql2-json 时
  filter-lang=cql2-json，JSON 优先 text 回退）；STAC filter extension
  conformance 探测后才升级 filter 下推（POST /search `filter` +
  `filter-lang=cql2-json`）；本地余项求值保留；
- 联邦 Arrow 批通道：`iter_query_arrow_batches`（GeoParquet）+
  `fabric/arrow_lane`（行形状/谓词语义/预算行为与 dict lane 同口径）；
  pyarrow 缺失 → typed `VectorCarrierUnavailable` 诚实回落。

## Consequences
- 工具面 additive：`query_federated_chain` 新 `use_cache`（默认 true ——
  **有意行为 delta**：命中披露 + fingerprint 失效 + TTL 背书）与结果
  `fabric`/`result_cache` 字段；`ChainSource.source_type` 可选（能力注入）；
  `ChainSourceStats.unique_keys` 可选（聚合下推证明）。
- V5 回退路径原样保留；未删除任何旧引擎代码。
- 已知限制 / Deferred：
  - **接线缺口（R2-M2 披露，follow-up 单）**：ConnectionRegistry /
    CapabilityProbeService / SourceFactsService 治理面在本 Epic 交付为
    库 + 测试，工具路径的 adapter 解析仍走既有 connection_manager（12 处
    调用点切换属独立 PR）；feedback 衰减修正因子暂无 costing 消费者
    （捕获/持久/披露回路完整）；counters 的 bytes/peak/probe 维度为
    预留 seam（未接线恒 0，fabric 段如实为零值）；
  - V6 非 typed 异常回退 V5 的双执行无进程级熔断计数（R2-Mi-4，
    warnings 已披露）——持续故障源每请求付 V6+V5 两次成本；
  - ArcGIS outSR 无响应端校验通道（f=geojson）→ 不声明 output_crs_pushdown；
  - 结果缓存 TTL 窗口内远端变化不可见（fingerprint 只在 catalog 同步时
    更新）—— 由命中披露段如实标注 basis；
  - durable secret provider（at-rest 加密）；
  - 多源累积子树的执行期交付 SRID 全量账本（单扫描 reproject 已修正；
    链式混合交付场景依赖计划期声明）；
  - Redis 分布式结果缓存（进程内 LRU 已够单实例语义）。

## Migration
- 0034_data_fabric_v7_facts_feedback：2 个 additive advisory 新表
  （data_fabric_source_facts / data_fabric_federated_feedback），
  down_revision=0033_geocompute_v6_cluster，可重入 DDL。
