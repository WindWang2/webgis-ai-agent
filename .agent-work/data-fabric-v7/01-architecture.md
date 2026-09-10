# V7 架构冻结（Phase B）— Adaptive Distributed Spatial Data Plane

## 0. 定位

**V7 不是第三套查询引擎**。V6 优化器（logical/costing/enumerator/physical/adaptive/explain）保持为计划与执行内核；V7 是其下方的**联邦数据面治理层**（fabric layer）+ 对 V6 显式 follow-up 的兑现：

```
Connection Registry V7 (scoped/revision/TTL/health/secret-separation)
 → Capability/Stats Probe (probing.py, scoped cache)
 → SourceFacts (source_facts.py, scoped durable)
 → Logical Spatial Query (V6 logical, 不变)
 → Costed Distributed Plan (V6 enumerator + V7 rate-limit/uncertainty/server-CRS/safe-agg)
 → Pushdown/Placement (V6 pushdown + server CRS transform + safe aggregate pushdown)
 → Streaming Execution (V6 physical + Arrow batch lane)
 → Adaptive Replan (V6 chain + V7 bushy subtree)
 → Feedback (fabric/feedback.py, persisted/scoped/decayed → SourceFacts)
 → Cache (fabric/result_cache.py, revision-keyed, disclosed hit)
 → Explain (V6 explain_v6 + fabric evidence/counters)
```

工具面 `engine` 参数保持 `v5|v6`（不新增 "v7" 引擎值 —— 避免 contract drift；V7 特性经 V6 引擎路径生效）。

## 1. 新模块布局（全部新增，V6 文件只做 additive 修改）

```
app/services/data_fabric/fabric/
├── __init__.py            # 最小导出面
├── connection_registry.py # W1-2: Connection Registry V7
├── probing.py             # W3: capability 探测 + scoped 缓存 + rate-limit 观测
├── source_facts.py        # W4: SourceFacts 记录/采集/持久层
├── feedback.py            # W11: 分布式反馈（持久/作用域/衰减）
├── result_cache.py        # W12: 查询结果缓存
└── counters.py            # W13: 结构性计数器（remote_requests/bytes/rows/pushdown%/cache/replan/probe）
```

V6/既有文件 additive 修改：
- `query/federation.py`：execute_chain_v6 出入口接 cache/counters/feedback（字段 additive）
- `query/federated/costing.py`：rate-limit 成本 + 不确定性乘子（新纯函数）
- `query/federated/enumerator.py`：`allow_server` 由 caps 探测驱动；safe aggregate pushdown 分支
- `query/federated/physical.py`：server placement 消费（跳过本地变换）+ Arrow 页通道
- `query/federated/adaptive.py`：bushy 子树重排
- `query/federated/explain.py`：fabric evidence 段
- `query/capabilities.py` + `query/models.py`：AdapterCapabilitiesV2 additive 字段
- `app/schemas/data_fabric_schema.py`：QuerySpec additive `output_crs`
- adapters：postgis/wfs/arcgis output_crs 发射；stac filter 扩展探测；ogc CQL2-JSON
- `app/tools/data_fabric_tools.py`：query_federated_chain additive `use_cache` 参数与结果字段

## 2. 权威状态 / 投影 / 缓存（无第二事实源）

| 真相 | Owner | V7 关系 |
|---|---|---|
| 连接（REST/DB） | `DataSourceModel`（org_id/owner_id） | V7 Registry 引用其 id，重建 profile 时**恢复全部凭证字段**（修 P1） |
| 连接（工具/进程内） | `fabric/connection_registry.py` `ConnectionRegistry` | 包装（非替换）`DataFabricConnectionManager`；老 API 委托保活 |
| secrets | `SecretStore` seam（进程内实现；接口可换 Redis/KMS） | durable reference 只存 `secret_ref` 句柄；egress redact 沿用 |
| capability | 探测结果缓存（probing.py，scope=TenantScope+profile+revision，TTL） | 静态默认矩阵仍是 fallback 真相；探测是**缓存的事实**，来源可追溯 |
| SourceFacts | `fabric/source_facts.py` + 新 DB 表 `data_fabric_source_facts`（advisory fail-open，同 statistics 文化） | `query/statistics.DatasetStatistics` 字段 additive 复用，不建平行模型 |
| 反馈 | `fabric/feedback.py` + 新 DB 表 `data_fabric_federated_feedback`（advisory fail-open） | 进程内 `PlannerFeedbackStore` 保留（行数修正）；V7 加 bytes/latency/error/throttle 维度 + 持久 + scope + 衰减；观测行数回写 `observe_row_count`（既有单点） |
| 结果缓存 | `fabric/result_cache.py`（进程内有界 LRU） | **缓存永远披露**（`result_cache: {hit, age_s, key_basis}`）；键含 per-source descriptor fingerprint + 请求 canonical hash + owner scope |

## 3. typed contracts（新增公共面）

```python
@dataclass TenantScope: org_id: Optional[int]; owner: Optional[str]; project_id: Optional[str] = None
  # 规范化字符串键 scope_key()；None 组件记 "_"（无歧义拼接）

class ConnectionRecord(BaseModel):
  profile_id, scope, source_type, endpoint_ref(redacted URL), secret_ref(Optional[str]),
  revision: int, created_at, expires_at: Optional[datetime],
  health: Literal["unknown","healthy","degraded","unreachable","expired"],
  last_health_at, capabilities_revision: Optional[int]

class SecretStore(Protocol): put(secret)->ref; get(ref)->Optional[dict]; evict(ref)->bool
class InMemorySecretStore: TTL+条目双界；进程内（redact 后内容永不落盘）

class ProviderCapabilitiesRecord(BaseModel):
  profile_id, scope_key, revision, caps: AdapterCapabilitiesV2,
  rate_limit: Optional[RateLimitHints(requests_per_window, window_s, source: header|observed)],
  probed_at, expires_at, probe_cost: ProbeCost(requests, bytes, latency_ms)

class SourceFactsRecord(BaseModel):
  dataset_fingerprint, scope_key, profile_id, row_count, row_count_basis(exact|estimate|observed|None),
  extent, crs, geometry_family, ndv: Dict[str,int], null_fraction: Dict[str,float],
  spatial_histogram, avg_geometry_complexity, temporal_extent: Optional[[str,str]],
  freshness: {collected_at, collector, sampled: bool}, confidence, revision, expires_at

class ExecutionFeedback(BaseModel):
  plan_hash, scope_key, per_source: [{fingerprint, rows, bytes, latency_ms, pages, errors, throttled}],
  outcome(ok|error|cancelled), estimated_rows/actual_rows per hop（对齐既有 adaptive 观测）

class QueryResultCacheEntry: key, payload(rows 封装), created_at, ttl_s, source_fingerprints: Dict[sid,fp], bytes
```

错误语义：全部沿 `DataFabricError` 族；新 typed 错误 `ConnectionExpiredError`、`ScopeViolationError`（内部）。所有 advisory 层（facts/feedback/cache 写路径）fail-open：绝不阻断查询（statistics 文化）。

## 4. 关键设计决策

### D1 Connection Registry V7（W1-2）
- 键 `(scope_key, profile_id)`；owner 隔离：不同 owner 不可见不可枚举；`TenantScope(owner=None)` = 显式全局域（legacy 兼容，工具路径显式传 session owner）。
- revision：`update(profile, expected_revision)` CAS；不匹配抛 typed 冲突。
- 过期：`expires_at` 显式 + idle TTL 驱逐（访问惰性 + 有界注册表 LRU 1024）；驱逐时 adapter 关闭（`close()` best-effort）。
- health 状态机：probe 经 breaker；unknown→healthy/degraded/unreachable；unreachable 超过 `health_ttl` 后懒惰复探。
- **secret separation**：registry 存 ConnectionRecord（redacted）+ secret 在 SecretStore；`ConnectionProfile` 组装时注回。修复 P1：从 DB 重建 profile 时恢复 `username/password/credentials` 顶层字段（manager.py 现丢弃）。
- 不改 REST 契约：`DataSourceModel` 明文 JSON 列保留（加密 at-rest 是 hookable provider 的 follow-up，本文档披露）。

### D2 Capability probing（W3）
- `probe_profile_capabilities(profile, adapter)`：按源类型组合既有探测（OGC conformance→CQL2/crs；STAC filter extension conformance→cql2；ArcGIS `f=json` 服务根→maxRecordCount；PostGIS 静态）。
- 结果缓存 scoped（scope_key+profile_id+profile revision），TTL 300s，容量 1024；**探测代价计入 counters**（requests/bytes）。
- rate-limit 被动观测：响应头 `Retry-After`/`X-RateLimit-*`/429 捕获为 RateLimitHints（诚实：None=未知）。

### D3 SourceFacts（W4）
- 采集 = **plumb, not scrape**（沿 observe_row_count 契约）：无过滤 count（numberMatched/returnCountOnly/num_rows）、descriptor bbox/crs、采样页直方图（bounded ≤2 页，sampled=true 标注）。
- 持久表 `data_fabric_source_facts`（append-only 行 + expires_at + scope 列；advisory）。读路径：进程缓存 → DB → descriptor。
- 迁移链：down_revision=`0033_geocompute_v6_cluster`（当前唯一 head；rebase 时复核）。

### D4 CQL2/Standards（W5）
- `compile_predicate_cql2_json(node)`（compilers.py additive）：与 text 同 AST，输出 CQL2-JSON dict（值参数化问题不存在——JSON 结构天然无注入；键仍走 `validate_predicate_fields` 白名单）。
- OGC：conformance 声明 cql2-json 时 filter 下推优先 JSON 编码（capability flag `filter_encoding="cql2-json"|"cql2-text"`）。
- STAC：`/search` filter 扩展（filter、filter-lang=cql2-text）探测后才启用（诚实默认 off）。

### D5 Server CRS transform placement（W8，兑现 ADR-0118 KL#2）
- `QuerySpec` additive `output_crs: Optional[str]`（"EPSG:xxxx"）。
- 发射：PostGIS `ST_Transform(geom, srid)` 包裹几何列；WFS `srsName=EPSG::srid`；ArcGIS `outSR`。OGC API：仅 conformance 声明 crs 协商时（默认关）。**返回行 metadata 报告实际 CRS**（`delivered_crs`），缺失=服务器忽略 → 执行器回退本地 pyproj（正确性优先，placement 声明作废并在 explain 披露）。
- 枚举：`decide_crs_transform(allow_server=caps.server_reprojection and caps.output_crs_plumbing)`；physical `LogicalReproject(placement="server")` 不做本地变换、校验 delivered_crs。
- 安全：output_crs 仅接受 `EPSG:\d+` / OGC:CRS84 白名单格式（防注入进 SQL/srsName）。

### D6 Safe aggregate pushdown（W9，收口 ADR-0118 KL#6）
- 语义陷阱：aggregate_join=join 后按右列分组聚合，右行随左侧匹配数**重复计数**；源侧 GROUP BY 每右行计一次 → fan-out>1 时结果不同（KL#6 拒绝的原因）。
- **安全条件（计划期可证）**：join 是 attribute 等值 join 且左侧 join 字段 NDV == 左行数估计（唯一键证明，来自 SourceFacts/pg_stats/用户 stats_hints）→ 每右行至多匹配一左行 → 源侧 GROUP BY 与 join-后聚合**逐位等价** → 允许下推（拉组行）。
- 无证明 → 保持本地（现状），EXPLAIN rejected alternative 披露原因（`fan_out_unproven`）。
- 差分测试锁定：唯一键/非唯一键 × 下推 on/off 精确对照。

### D7 Bushy adaptive（W10，兑现 KL#4）
- 执行证据已有 per-source actual rows；bushy 重排：偏差越阈（沿用 4×）时对**剩余子图**重新 DP（同一 enumerator、观测行数作为 pinned 估计），新树严格更优才切换；MAX_REPLANS 仍 =1；`order_strategy="given"` 禁用。
- 空间跳 build 侧仍限定原始 scan（`__right_geometry__` 不变量不变，枚举方向守卫保留）。

### D8 Distributed feedback（W11）
- 捕获点：`PhysicalExecutor`（行/页/字节估计 per source）+ `iter_scan_pages`（HTTP bytes 如可得的 Content-Length；不可得 None 诚实）+ breaker（error class）+ 429/Retry-After（throttled）。
- 存储：进程有界 + `data_fabric_federated_feedback`（scope 列 + created_at + outcome；prune 上限 20k 行；fail-open）。衰减：样本指数半衰期（半衰 30min）加权中位数。
- 回路：观测行数 → `observe_row_count`（既有）；观测 selectivity → correction 因子供 costing（与 PlannerFeedbackStore 相乘，双夹界）。
- 无 secret 进 feedback（只有 fingerprint/scope/计数）。

### D9 Query result cache（W12）
- 键：sha256(scope_key + canonical(request:{sources,joins,bbox,limit,order_strategy,derive_projection}) + Σ per-source dataset_fingerprint + engine)。
- 命中条件：TTL 内 ∧ 每源 fingerprint 与 catalog 当前一致（fingerprint 查询 = 本地 catalog 读，无网络）；不一致 → miss 并异步失效。
- 负缓存：仅 `SourceUnreachableError`/`SourceAuthFailedError`，TTL 30s，容量 64；预算/取消类**绝不缓存**。
- 有界：256 条 / 64MiB 双界 LRU；owner 隔离进键。
- **无 stale silent success**：命中结果带 `result_cache={hit:true, age_s, key_basis}`；工具参数 `use_cache: bool = True`（显式 false 绕过）。

### D10 不确定性/成本（W7）
- 估计不确定性乘子：confidence measured×1.0 / estimated×1.15 / assumption×1.5，作用于基数→字节成本（确定性、EXPLAIN 可复述）。
- rate-limit 成本：`penalty = requests × (1/rate_per_window 若已知)` 常数权重并入源扫描成本；未知=无惩罚（诚实）。
- counters（W13）：`FabricCounters{remote_requests, bytes_fetched, rows_materialized, peak_streaming_bytes, pushdown_ratio, cache_hits/misses, replans, probe_requests}` 进 `explain_v7.fabric` 段。

### D11 兼容与 rollout
- 所有新请求字段 additive 且带安全默认（关/旁路）；`use_cache` 默认 true 是唯一默认行为变化 —— 以「缓存披露 + fingerprint 失效 + 差分语料（cache on/off 同结果）」背书，PR 行为 delta 显式记录。
- V5 回退路径原样保留（compat switch）；不删除任何旧引擎代码。

### D12 测试 oracle
- 差分：V7 fabric on/off × V6 引擎 × （V5 对照子集）三方同语料精确对照（合成 provider，可证明语义）。
- CRS：本地 pyproj 参考实现 vs server placement 输出几何对照（合成数据已知变换）。
- 聚合下推：唯一/非唯一键双路径 vs 本地聚合 reference。
- 性能：结构性断言（请求数/字节/行数上限、pushdown 比率、probe 请求数），wall-clock 仅参考。

## 5. 失败模式

| 失败 | 语义 |
|---|---|
| 探测失败 | 回落静态默认 caps（来源标注 default），绝不编造 |
| SourceFacts DB 不可用 | fail-open 无统计基线 |
| feedback 写失败 | 静默（advisory），计数器仍记录 |
| cache 反序列化失败 | 当 miss，删除坏条目 |
| server CRS 服务器忽略 output_crs | delivered_crs 校验失败 → 本地变换回退 + warning |
| 429/Retry-After | typed `SourceRateLimitedError`（若既有）或 breaker 记录 + RateLimitHints 更新 |
| 连接过期 | typed ConnectionExpiredError + 惰性重探（健康路径） |

## 6. 资源包络（全有界）

- registry：1024 连接条目 / idle TTL 1800s
- capability 缓存：1024 / TTL 300s
- result cache：256 条 / 64MiB / TTL 300s
- feedback：进程 512 keys×16 obs；DB 20k 行 prune
- 探测：每 profile ≤3 请求/轮；采样 ≤2 页
- 迁移：2 新表，均 append-only + 上限 prune

## 7. 共享文件纪律

- config.py：`DATA_FABRIC_V7_*` 6 个新 setting（同步 .env.example —— 模板奇偶闸）
- migration：1 个（2 表），链 0033；rebase 时单 head 复核
- ADR：**0119-federated-data-fabric-v7**（0118 已被占用 5 主题，取新号）
- CHANGELOG：最小独立小节追加
- 不动 generated artifacts 权威输入（OpenAPI/registry 快照不变 —— 无 REST 契约变更）

## 8. 架构挑战修订（Subagent-A Round 0，2026-09-09）

### R-C1（原 D6）safe aggregate pushdown 证明收紧
- 原设计"NDV==行数估计"被否：估计值不能当证明；且缺 inner-join 存活论证。
- **最终条件（全部满足才下推）**：
  1. `edge.kind=="aggregate_join"` 且 `join_field_left/right` 均存在（纯属性等值；spatial aggregate join 显式排除）；
  2. `group_by_right` **包含** `join_field_right`（组内匹配全有或全无的根基）；
  3. 左 join 键唯一性是 **measured 级证明**：PostGIS `pg_index` indisunique 探测（新 collector）或用户 `stats_hints` 显式 `unique_key: true` 声明；估计 NDV 不作数；
  4. 聚合函数 ⊆ {count, sum, min, max, avg}（可合并内核）；
  5. 右源 `caps.aggregation=True`。
- 等价性论证（写入 ADR）：左键唯一 ⇒ 每右行至多匹配一左行；组含 join 字段 ⇒ 组内右行要么全匹配要么全不匹配；不匹配组被最终 join 丢弃 ⇒ 源侧 GROUP BY ≡ join-后聚合。
- 无证明 → 本地路径 + EXPLAIN rejected `fan_out_unproven`。

### R-C2（原 D5）server CRS placement 语义重定义
- **发现 V6 潜在缺陷**（本 Epic 修复主体）：PostGIS `_geojson_expr`（postgis_adapter.py:1104）默认 `out_srid=4326` —— 联邦扫描交付行恒为 4326，而 enumerator 用 descriptor 原生 srid 做本地对齐决策 ⇒ 原生≠4326 的 PostGIS 源进混合 CRS 链会被双重变换（静默错坐标）。
- **修复 = server placement 本体**：声明 srid 已知 ∧ `caps.output_crs_pushdown=True`（新 additive 字段；postgis=True / arcgis=True / 其余 False）时，扫描 extras 传 `output_crs=EPSG:<declared>`（legacy QuerySpec extra 通道已被 normalize 支持：normalize.py:293）⇒ **delivered==declared 不变量恢复**。
- join 对齐：enumerated server placement ⇒ 目标侧扫描 output_crs=对齐目标 srid，physical 跳过该侧本地变换；**out_srid 账本消费 delivered srid**。
- 验证分级：PostGIS=SQL 内省（确定性，记录事实）；ArcGIS=响应 `spatialReference` 解析，不匹配/报错 ⇒ **去 output_crs 一次性重试** + 本地 pyproj 回退 + warning（覆盖"硬报错≠忽略"缺口）；WFS/OGC API 轴序/协商风险 ⇒ `output_crs_pushdown=False`（维持本地，诚实披露）。
- 双通道分离：过滤 bbox 语义不变（WHERE 作用于原生列）；`output_crs` 只影响交付几何编码。`SourceFacts.crs` 定义为 **declared/native**，delivered 走 plan 账本。
- `output_crs` 值白名单：`^EPSG:\d{4,5}$` / `OGC:CRS84`（防注入进 SQL/srsName/outSR）。

### R-C3（原 D8）feedback 回写守卫
- `observe_row_count` 既有契约（statistics.py:148"只对无过滤请求"）**不破坏**：仅当扫描无 where/bbox/spatial pushdown（capture 点已知 extras）才回写行数事实；过滤后观测只进 correction 因子。
- feedback 一律不带 secret；scope 进键。

### R-M1（原 D1）revision = 内容寻址
- 不给 data_sources 加列（降低共享表面冲突）。ConnectionRecord.revision = **redacted profile 内容 sha256 前 16 hex**（content-addressed revision）——跨进程天然一致，进程内 CAS 用 RLock 比较 hash。registry 读穿 DB（REST 真相）+ 工具路径进程内注册。
- capability/SourceFacts 缓存键同用 profile 内容 hash（解 D2 的 MINOR）。

### R-M2（原 D9）cache 纪律收紧
- `owner=None` 全局域连接**禁止**进结果缓存（跨会话串结果风险归零）。
- 键披露 `key_basis`；fingerprint 校验基于 catalog（连接时同步）—— TTL 内远端变化不可见的残余陈旧在 `result_cache.basis="ttl+fingerprint"` 显式披露。
- counters 明确 **per-execution**（execute_chain_v6 每次新建，与 executor/controller 同生命周期）。

### R-M3（原 D7）确定性口径
- bushy 重排是"**计划可复述、不可逐位复现**"（观测驱动）；ADR-0119 显式决策；差分 oracle 用**序不敏感**比较（多重集合 + 确定性排序后对照）；explain 披露 pinned 证据与 replan 决策。

### R-minor 收口
- fail-open 层：首次失败 logger.warning（非 debug）+ 失败计数进 explain fabric 段。
- caps 来源披露：explain 每个 source 标注 `caps_basis: default|probed|stale`。
