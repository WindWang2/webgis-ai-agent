# ADR-0094: Enterprise Geospatial Data Fabric V2

**日期:** 2026-09-03
**状态:** Accepted
**扩展:** ADR-0050 (Data Fabric V1), ADR-0093 (GIS Runtime Correctness & Concurrency V5)
**关联:** ADR-0092 (Reproducible GIS Runtime — lineage/fingerprint 约定)

## Context

Data Fabric V1（ADR-0050）交付了统一 adapter 模式（11 种源类型）、DatasetDescriptor 契约、
pushdown-first QuerySpec、fetch-on-demand ref_id 物化与 SSRF/零 secret 边界。十个独立审计
（架构/PostGIS/OGC/文件格式/安全/性能/前端/工具集成/数据生命周期/对抗性审查）发现 V1 的
结构性限制：

1. **QuerySpec V1 是 optional-fields 的堆叠**（`bbox/columns/fields/limit/offset/filter_expr/
   where/datetime_range/...`，`extra="allow"`），没有 CRS 语义、聚合、排序、结果模式或执行
   预算；`srs` 参数经 extra 字段静默失效。
2. **Agent 查询主通道是 raw where 字符串**，各 adapter 对其解释不一致：PostGIS 用单表达式
   安全解析器，ArcGIS/OGC API 原样透传到远端（注入面），WFS 静默丢弃，dict 过滤器静默丢弃。
3. **capability 声明不真实**：PostGIS 声明 projection 但 `SELECT *, geom`；GeoParquet 声明
   lazy_batching 但整文件载入；WFS 声明 pagination 但 offset 静默失效；S3 声明 range_request
   但从不读字节。
4. **无 planner**：pagination 策略、结果模式、fallback 决策散落在各 adapter 内部，不可解释、
   不可预算。
5. **无聚合下推**：区县统计类需求退化为全量下载 + Python Counter。
6. **catalog 同步无增量语义**（不删除消失的条目、失败 stub 被 fingerprint 锁死）、
   **工具面存在跨租户全局单例**、**demo/合成数据在 describe 失败路径泄漏且无 is_demo 标注**。
7. **物化路径双重管线**（REST `df` 前缀 vs 工具 `data-fabric` 前缀）与
   **内存目录/DB 目录两个世界** 互不相通。

V2 的目标是把 V1 演进为一条可审计的大规模 GIS 数据执行链：
discover → understand → plan → optimize → push down → execute → materialize only when
necessary → expose lightweight evidence → analyze → visualize。

## Decision

### 1. 核心概念与权威状态

| 概念 | 定义 | 权威归属 |
|---|---|---|
| `DataSource` / `ConnectionProfile` | 外部源连接契约（类型、endpoint、凭证引用） | `data_sources` 表（REST 面）；工具面 in-memory 连接管理器保留但按 owner 作用域化 |
| `DatasetDescriptor` | 数据集元数据契约（schema/geometry/CRS/extent/count） | `spatial_catalog_items.descriptor_json`（sync 后）；in-memory `SpatialCatalogService` 仅作为 agent 工具面的会话级投影，不再是第二真理 |
| `DatasetVersion` | 轻量版本记录：`descriptor_fingerprint + schema_fingerprint + content_hint + source_revision + observed_at + revision_strength` | 派生自 catalog sync，不新建表（存于 descriptor metadata 与 fingerprint 列） |
| `QuerySpecV2` | 结构化查询模型（select/filter/spatial/temporal/aggregate/group_by/order_by/page/output/execution） | 唯一查询输入真理；legacy QuerySpec 归一化为 V2 |
| `Predicate AST` | 类型化谓词树（比较/逻辑/空间/时间） | `app/services/data_fabric/query/predicates.py` —— LLM 永不直接生成 SQL |
| `QueryPlan` | planner 产物：pushdown/local 划分、pagination 策略、result mode、估算、fallback 原因、warnings | 每次执行派生，可序列化、确定性 |
| `AdapterCapabilitiesV2` | 结构化 truthful capability 矩阵 | adapter 静态声明 + 服务端探测修正 |
| `ExecutionFragment` | 单源执行片段（pushdown 编译结果 + 本地补算描述） | planner 派生 |
| `Materialization` | ref_id 物化记录 | `materializations` 表（审计）+ SessionStore（payload，ref_lifecycle 契约不变） |
| `QueryEvidence` | 执行证据：dataset/source fingerprint、query fingerprint、pushdowns、counts、duration、fallbacks、warnings | 附加在 QueryResult.metadata 与 materializations 行内，不建第二 lineage store |

### 2. QuerySpec V2（兼容归一化）

结构化模型（`app/services/data_fabric/query/models.py`）：

```text
QuerySpecV2
 ├─ select: [field...]                  # 投影；geometry 隐含可选
 ├─ filter: PredicateAST | None         # typed AST（属性谓词）
 ├─ spatial: SpatialPredicate | None    # Intersects/Within/DWithin/BBox(geometry, crs)
 ├─ temporal: TemporalPredicate | None  # Before/After/During(field)
 ├─ aggregate: [AggSpec] | None         # count/sum/avg/min/max/stddev/distinct_count
 ├─ group_by: [field...] | None
 ├─ distinct: bool
 ├─ order_by: [(field, asc|desc)]
 ├─ page: OffsetPage | CursorPage       # limit 必填有上限；cursor 优先 keyset
 ├─ output: OutputSpec                  # mode + crs + max_features/bytes
 ├─ sample: SampleSpec | None           # deterministic seed = f(dataset_fingerprint)
 └─ execution: ExecutionBudget          # deadline_s / max_rows / max_bytes / max_vertices
```

**Result modes**: `DESCRIPTOR | STATISTICS | SAMPLE | FEATURES | MATERIALIZE | VECTOR_TILE`。

**归一化规则**（`query/normalize.py`）：legacy `bbox` → `spatial=BBox(bbox, crs="EPSG:4326")`；
`where/filter_expr`（str dict）→ 受限解析器转 AST，解析失败即 `INVALID_QUERY` typed error
（不再静默丢弃）；`columns/fields` → `select`；`datetime_range` → `temporal=During(...)`；
`limit/offset` → `OffsetPage`。归一化输出 canonical form → `query_fingerprint`（SHA-256 of
canonical JSON，与 dataset fingerprint 组合用于缓存键与 lineage）。

**CRS 语义**：QuerySpecV2 显式声明 `query CRS`（bbox/spatial 的 crs 字段）与 `output CRS`。
planner 负责变换；无法变换时 typed `CRS_INVALID`，禁止静默假定 4326。bbox 跨 antimeridian
（minx>maxx 且 4326）→ 显式 split 或 `QUERY_INVALID`，禁止 silent wrong。

**地理距离**：`DWithin` 在 EPSG:4326 下以 meters 解释 → PostGIS 用 `geography` cast 编译；
planner 在 plan 中记录该决策。

### 3. 类型化谓词 AST 与安全编译链

```text
Agent/UI typed input → PredicateAST (validated)
  → adapter-specific compiler（postgis_sql / cql2 / arcgis_where / fes / local_eval）
  → parameterized query / encoded filter
```

- 属性谓词：`Eq Ne Gt Ge Lt Le In NotIn Between Like IsNull And Or Not`（含嵌套）。
- 空间谓词：`Intersects Within Contains Touches Overlaps DWithin BBox`。
- 时间谓词：`Before After During`。
- **编译器职责**：字段名必须来自 DatasetDescriptor schema（identifier 白名单），值一律参数化；
  ArcGIS `where` / OGC `filter` 同样只接受 AST 编译产物，raw where 透传通道移除
  （legacy REST 内部调用走受限解析器 → AST，与 Agent 同一条链）。
- WFS FES 过滤器由模板化 XML 生成（escape 全部值），经 `parse_safe_xml` 同级防护。

### 4. Capability Matrix V2（truthful）

结构化声明（`query/capabilities.py`）：

```text
bbox_pushdown, filter_pushdown, projection_pushdown, sort_pushdown, offset_pagination,
cursor_pagination, spatial_predicates[], temporal_filter, aggregation, group_by, count,
statistics, server_reprojection, vector_tiles, range_requests, streaming,
max_page_size, server_side_spatial_join
```

规则：**声明即契约**。adapter 宣称的每一项都由 AdapterContractTest 套件验证（含不支持路径的
typed `QUERY_UNSUPPORTED` 行为）。V1 中不真实的声明（PostGIS projection、WFS pagination、
GeoParquet lazy_batching、S3 range_request）在 V2 中要么实现、要么降级声明。

### 5. Spatial Query Planner 与 Explain

`query/planner.py`：输入 normalized QuerySpecV2 + DatasetDescriptor + capabilities +
budget → 输出 `QueryPlan`（deterministic、serializable）：

```text
QueryPlan
 ├─ source/dataset/fingerprints
 ├─ pushed_filters / local_filters / pushed_projection / pushed_spatial / pushed_aggregation
 ├─ pagination_strategy (cursor|offset|single_page) + order guarantees
 ├─ estimated_rows / estimated_bytes / execution_mode / result_mode
 ├─ fallback_reason / warnings（含"geometry 无索引"性能警告）
 └─ steps: [ExecutionFragment]
```

`explain_data_query` 能力（REST + agent tool）输出人类可读 plan；**永不包含** password/secret/
连接 URI。PostGIS describe/diagnostics 探测 geometry index（`pg_indexes`/`geography_columns`），
无索引时返回性能警告与建议 DDL 文本，**绝不自动执行 DDL**。

### 6. PostGIS Reference Adapter V2

- AST → 参数化 SQL 编译器（identifier 白名单来自 describe 缓存 schema；geom 列名同样校验）。
- 投影下推：显式列清单 + `ST_AsGeoJSON(ST_Transform(geom, out_srid))`，移除 `SELECT *, geom`
  双重传输。
- 排序：无显式 order_by 时自动附加稳定排序（PK 或 ctid 兜底），保证 OFFSET/keyset 分页确定性。
- Keyset/cursor pagination：存在稳定整数 PK 时优先；QueryResult 返回 `next_cursor/has_more`。
- 聚合下推：`SELECT district, count(*) ... GROUP BY`、`returnCountOnly` 等价 `count(*)`；
  STATISTICS mode 不取 geometry。
- statement_timeout 按 ExecutionBudget 设置（连接级 `options`）；psycopg3 conninfo 改 kwargs
  形式（修复密码注入）；连接池创建失败不再永久 memoize None。
- 同源 join 优先 server-side（`ST_Intersects` join SQL），联邦执行器只在跨源时介入。

### 7. Bounded Federated Query

`query/federation.py`：受限联邦（GIS agent 所需，非 Trino）：

- 支持：attribute join（等值）、spatial join（points-in-polygon / intersects）、aggregate+join。
- Planner 决定：哪侧先执行、哪侧 pushdown、哪侧物化（默认小结果侧）、join 策略
  （server-side 同源 / 本地 STRtree）。
- **硬预算**：`max_source_rows / max_local_rows / max_bytes / max_vertices / max_seconds /
  max_join_candidates` → 超限返回 `QUERY_BUDGET_EXCEEDED` + 缩减建议，绝不把两个百万级源拉进
  Python。
- 本地 join 使用 shapely STRtree（禁止 O(N·M) 双循环）。

### 8. Result Modes 与 Large Vector Data Plane

| mode | 语义 | 输出 |
|---|---|---|
| DESCRIPTOR | 仅元数据 | descriptor + fingerprint（零物化） |
| STATISTICS | 仅统计 | count/agg + evidence（零 geometry 传输） |
| SAMPLE | 确定性采样 | seed=f(dataset_fingerprint, sample_spec)，可复现 |
| FEATURES | 有界 inline 特征 | ≤ 阈值 GeoJSON |
| MATERIALIZE | 物化 | ref_id + 审计行 |
| VECTOR_TILE | tile 流 | z/x/y on-demand（服务器 tile 优先） |

规模策略（`query/large_data.py`）：阈值综合 `estimated_bytes / vertex_count / feature_count /
attribute_width` 决定 GeoJSON↔chunk↔MVT；物化执行器带内存预算（超限 chunk/spill/refuse，
不 OOM）。LLM 永远只看到 descriptor/statistics/sample/evidence（现有 feature-stripping 与
40k cap 保留并加强）。

### 9. Catalog V2 与 Freshness

- **增量同步**：sync 输出 `{added, updated, unchanged, removed}` diff；消失条目标记
  `availability=STALE_SOURCE_UNAVAILABLE`（列保留，UI/Agent 可区分"目录存在"与"源当前不可达"），
  不再静默保留也不物理删除（除 source 删除级联）。
- **失败 stub 不落库**：describe 失败 → 跳过该条目并计入 warnings，绝不把合成 descriptor
  fingerprint 锁进 catalog。
- **DatasetVersion seam**：`revision_strength ∈ {strong, weak}`（ETag/last_modified/schema
  fingerprint 可得为 strong；远端不可得为 weak，诚实标注，不用 current time 伪造）。
- query cache 仅在 `dataset fingerprint + query fingerprint` 可信时启用；weak revision 用
  保守 TTL；catalog sync 检测版本变化时经现有 `ref_lifecycle` 语义失效相关缓存。

### 10. Reliability & Security Runtime

- **Deadline 传播**：ToolRegistry timeout → DataFabric → Planner → Adapter（HTTP timeout /
  DB statement_timeout / pagination 循环 deadline 检查）；取消后连接归还、临时文件清理、
  无 ghost ref。
- **Retry**：仅 transient（连接/超时/429/5xx），认证/语法/权限/确定性 4xx 不重试；bounded
  指数退避。
- **Circuit breaker**：per-source 隔离不变；修复 half-open trial 泄漏与并发计数丢失
  （加锁 + trial 必释放）。
- **Secret 模型**：`sanitize_profile_dict` 扩展 Authorization/x-api-key/apikey/passwd/pwd/
  private_key；descriptor_json 中的 URL 统一 `redact_url`；不建 Vault（超范围）。
- **响应防护**：所有 HTTP adapter 强制 `stream=True` + Content-Length 预检 + 解压后字节上限；
  XML 输入加字节上限（defusedxml 保留）。
- **租户隔离**：工具面 connection_manager/spatial_catalog 按 owner scope 键控；
  `materialize_dataset` 工具增加 session 归属校验。
- **SSRF**：沿用 `validate_url` + per-hop re-validation；补 IPv6/decimal-IP 回归用例。
- **Never fabricate**：所有 demo/合成路径强制 `is_demo=true` 顶层标注；describe 失败返回
  typed error，不返回合成 descriptor。

### 11. Agent Tools 与 GIS Harness Seam

- 保留 `query_dataset / materialize_dataset / describe_dataset / search_spatial_catalog /
  connect_data_source / inspect_data_source / refresh_data_source`，内部走 V2 归一化+planner。
- 新增高价值工具（数量克制）：`plan_data_query`（dry-run explain）、`aggregate_dataset`、
  `query_federated_data`（受控联邦）。
- Tool schema 保持 compact：简单参数 → 后端归一化，不暴露完整 QuerySpecV2 JSON 树。
- **GIS Harness seam**：`materialize` 结果携带 `query_evidence`（dataset/source fingerprint、
  query fingerprint、pushdowns、counts、fallbacks）；`DataRequirement.bound_ref` 消费链不变，
  不触碰 semantic AnalysisPattern 内核。

### 12. Frontend Data Workspace

升级现有 `data_sources` 面板为 Data Workspace（复用 dataFabricApi/store/地图物化模式）：
Sources 面板（类型/状态/延迟/能力摘要，零 secret 展示）、Catalog 搜索（text/bbox/source/
geometry type/服务端分页）、Dataset Inspector（schema/CRS/extent/能力/freshness/fingerprint）、
安全 Query Builder（fields/filters/bbox/排序/limit/聚合 → typed QuerySpec）、Explain Panel
（pushdown/估算/警告/fallback）、大数据 Map Preview（小→GeoJSON ref，大→vector tiles，
禁止自动全量物化）。

## State Ownership（不变式）

1. 不新增第二 catalog/artifact/workflow 真理：DB `spatial_catalog_items` 是 catalog 真理；
   in-memory catalog 是工具面投影（sync 时从 DB/catalog 重建，owner-scoped）。
2. SessionStore/ref_lifecycle 契约（ADR-0093）不变：fabric 只选择 ref 前缀并委托 store；
   物化统一收敛到单一管线（`data-fabric` 前缀），REST 与工具共享同一 MaterializationService。
3. MapSpec/GIS Harness/ArtifactRegistry 所有权不变；Data Fabric 只通过
   ref_id + query_evidence seam 供数。
4. Raster pixel execution 属于 Raster Runtime（WMS/WMTS/PMTiles/STAC 仅元数据/asset 描述符）。

## Failure Semantics

- 错误分类沿用 V1 13 code + 新增 `QUERY_BUDGET_EXCEEDED / CRS_INVALID / QUERY_UNSUPPORTED
  / SOURCE_UNAVAILABLE`；全部 typed，禁止 in-band empty-success。
- 联邦超预算 → `QUERY_BUDGET_EXCEEDED` + 建议列表（加 bbox/加 filter/用 aggregate/减字段）。
- 取消 → `CANCELLED`，资源全回收（连接归还/临时清理/无 ghost ref），远端请求 abort。
- 源不可达 → catalog 条目保留并标注 stale；不删除、不伪造。

## Compatibility

- legacy QuerySpec 输入 → 归一化为 V2（REST 路由、旧工具签名、既有测试不动）。
- QueryResult 结构保留既有字段，新增 `next_cursor/has_more/result_mode/query_evidence/
  is_demo` 等字段为 additive。
- DB 迁移走 Alembic（`spatial_catalog_items` 增加 availability/summary 列等），SQLite
  batch + PG 双方言，禁 runtime CREATE TABLE。
- 既有 tile cache contract（ETag/content revision/epoch guard）不破坏；MVT 新路径独立
  cache key 前缀。

## Rejected Alternatives

- **通用分布式查询引擎（Spark/Trino/FDW/Ray）**：与"bounded GIS federation"目标不符，
  引入不受控依赖（非目标）。
- **替换 V1 adapter 框架**：V2 演进既有 base_adapter/registry；新增能力以 capability
  接口 + planner 层实现，避免并行期冲突。
- **独立 lineage store**：复用 materializations 行 + QueryResult.metadata evidence，
  不与 ADR-0092 的 artifact lineage 重复。
- **LLM 生成 SQL + 严格校验器**：不可证明安全；AST 编译链是唯一通道。
- **新建 secret vault**：超出本 PR 范围；先关死 egress/log 泄漏面。

## Deferred

- PostGIS FDW 式完全下推联邦（本版只做受控两源联邦 + 同源 server join）。
- WFS 3/OGC CQL2-JSON 完整方言（先 cql2-text 编译 + conformance 门控）。
- PMTiles 完整 tile 目录遍历服务化（保留元数据 + range probe；tile 服务归 Raster Runtime 协作）。
- 分布式断路器/跨进程 catalog 失效广播（进程内 TTL + sync diff 已满足当前规模）。
- 10m 行级真实 fixture（benchmark 用 EXPLAIN + 合成估算代替，避免磁盘耗尽）。

## Performance Red Lines

- 100k feature 查询不得触发全量 Python 物化（pushdown/projection/sample/MVT 分流）。
- 1m 行 count/group-by 只传聚合结果（pushdown_ratio ≈ 结果行数/源行数 → ~0）。
- 物化路径 payload 拷贝次数较 V1（6–9 次）至少减半。
- OFFSET 深分页在存在稳定 PK 时必须走 keyset。
