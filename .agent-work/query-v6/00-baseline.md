# Federated Spatial Query Optimizer V6 — Baseline（Phase A 审计）

- 日期：2026-09-08
- 分支：`feat/query-v6-federated-spatial-optimizer`
- Baseline commit：`445ad30`（origin/master，fetch 后）
- Worktree：`/home/kevin/projects/webgis/query-v6`

## 1. V5 事实图（file:line 均为本 worktree 验证）

### 模块布局 `app/services/data_fabric/query/`（~6.3k 行）

| 文件 | 行数 | 职责 | 现状 |
|---|---|---|---|
| models.py | 400 | QuerySpecV2 / QueryPlan / AdapterCapabilitiesV2 / QueryEvidence | 单源平面计划；V3 cost/alternatives、V4 pushdown_classes、V5 filter_split 均为 additive 字段 |
| predicates.py | 589 | 谓词 AST（filter/spatial/temporal）+ 本地求值 | 已实现 |
| normalize.py | 447 | legacy QuerySpec → QuerySpecV2 | 已实现 |
| planner.py | 574 | `plan_query` 单源确定性计划 | 规则式；CRS 只做校验 + 输出重投影 placement（planner.py:174-191） |
| optimizer.py | 257 | PlanCost 相对成本 + 有界备选（≤8） | 无 join/CRS 维度；权重公开常数（optimizer.py:17-22） |
| statistics.py | 518 | DatasetStatistics + TTL 缓存 + observe_row_count | rows/extent/columns/row_groups/total_bytes；**无空间直方图/CRS/复杂度** |
| selectivity.py | 182 | 属性谓词选择性（统计优先/常数兜底） | **空间选择性仅有 bbox 面积比**（planner.py:319-324） |
| capabilities.py | 250 | 8 类源 truthful 默认矩阵 | 已含 streaming/server_side_spatial_join/max_page_size |
| pushdown.py | 341 | V5 Wave9 AND 边逐子句拆分 + pushdown_classes | 已实现 |
| compilers.py | 486 | PostGIS SQL / CQL2 JSON / FES / ArcGIS where | 已实现 |
| federation.py | 1440 | 两源 + N 源左深链（≤4 源） | 见 §2 |
| execution.py | 174 | StreamingBudget + compute_aggregates | 本地聚合 |
| feedback.py | 133 | 有界 planner 反馈（winsorized 中位数） | 只修正行数估计，无中途 replan |
| accumulators.py | 342 | 聚合累加器 | 已实现 |
| evidence.py | 96 | QueryEvidence 组装 | 已实现 |

### 生产路径（真实接入，非 test-only）

- `app/tools/data_fabric_tools.py:758` `query_federated_data`（两源）；`:854` `query_federated_chain`（N 源，AGENT 工具表面）
- `app/services/geocompute/ops.py:286-373`（feedback_store、spatial_join_local、attribute_join_local、StreamingBudget）
- `app/services/data_fabric/manager.py`（describe/query 主链路，plan_query 逐源消费）

### N 源联邦现状（federation.py）

- `MAX_FEDERATED_SOURCES = 4`（federation.py:630）；左深链 **only**，无 bushy
- 序枚举：`cost_stats` 时 ≤24 排列有界枚举 + 连通性过滤（federation.py:794-847）；成本 = Σ传输行 + 跳基数（federation.py:761-791）
- **CRS 混用 = 计划期 typed fail-fast，无在线变换**（federation.py:975-981）
- 半连接：`_semi_join_reduce_right` 属性键集（≤1000 键，超限诚实放弃）（federation.py:1121-1154）；**无 Bloom、无空间半连接、无盈利阈值**
- 执行：逐源**一次有界全量拉取**到 list → 逐跳本地 join（STRtree/hash）；无流式批处理/backpressure
- 聚合 join：链上先连接后聚合（本地），**无聚合下推**

### Adapter 事实

- GeoParquet：row-group bbox 剪枝（covering/列统计）+ `iter_batches` 流式已实现（geoparquet_adapter.py:8-14, 575-624）
- STAC：bbox/datetime POST /search 下推（stac_adapter.py:393-420）
- PostGIS：bbox/属性/聚合/重投影/MVT 全下推；streaming=False（fetchall 有界）
- OGC API：bbox 下推；CQL2 需 conformance 探测升级

### 测试现状

- `tests/unit/test_data_fabric_federation.py`（13 用例，1.3s 全绿）
- `tests/unit/test_data_fabric_federation_v4.py`（V4 序枚举/半连接/投影派生）
- `tests/unit/test_planner.py`、`tests/unit/test_capability_registry_parity.py`
- `tests/benchmarks/test_planner_runtime_perf.py`（perf 门）

### 缓存 key

- `query_fingerprint(spec, dataset_fingerprint)`（models.py:182-187）：查询语义 + 数据集指纹；capability 变化/ provider 版本**不在 key 内**（capability 探测升级只影响计划，不影响结果缓存 —— 语义等价）；owner 隔离在 connection_manager 层（profile per owner）。

## 2. V6 缺口判定（14 目标能力）

| # | 能力 | 现状 | 缺口 |
|---|---|---|---|
| 1 | Typed logical plan | **缺失** | 无计划树（flat dict/list）；需 scan/filter/project/join/aggregate/sort/limit/reproject 节点 + canonical hash |
| 2 | Capability model | **已实现** | 增量：max_request_complexity/spatial op 细分已有；够用 |
| 3 | Statistics catalog | **部分** | 缺空间网格直方图、geometry 复杂度、CRS、freshness 展示 |
| 4 | Spatial selectivity | **缺失** | 只有 bbox 面积比；缺 intersects/within/dwithin 估计、复杂度、index 感知 |
| 5 | CRS-aware costing | **缺失** | 链内混 CRS 直接失败；无 transform placement/单侧变换/每行重复变换意识 |
| 6 | Join enumeration | **部分** | 左深 only + ≤24 枚举；缺 bushy、join graph 通用枚举、cost pruning |
| 7 | Pushdown V6 | **部分** | 单源逐子句已好；链上无 limit/order/聚合下推；无"为何未下推"结构化解释 |
| 8 | Semi-join/Bloom | **部分** | 键集 semi-join 有；缺 Bloom、盈利阈值、网络成本比较 |
| 9 | GeoParquet/FGB pruning | **已实现（adapter 内）** | 缺把剪枝事实暴露进统计/计划/成本（剪枝后行数未反馈给 join 枚举） |
| 10 | Streaming Arrow 物理 plan | **缺失** | 链执行全量 list 物化；需批算子/有界内存/backpressure/cancellation |
| 11 | Adaptive execution | **部分** | 只有 planning-time 反馈修正；无执行期基数偏差观察与安全切换 |
| 12 | Explain V6 | **部分** | summary/chain_explain 有；缺 est vs actual、计划树、物化点、CRS 变换点 |
| 13 | Differential correctness | **缺失** | 无 optimized vs reference 随机语料对比 |
| 14 | Benchmark | **部分** | 只有 planner runtime perf 门；缺 N 源/选择率/延迟模拟/字节传输基准 |

## 3. Scope 冻结

**纳入（P0/P1，本 Epic ownership 内）**：上表 #1、#3-#14 的纵向深化，全部落在 `app/services/data_fabric/query/**` + `data_fabric_tools.py` 的 federated 工具接线 + 对应 tests/benchmarks。

**不纳入（边界）**：
- object/chunk durability → Data Lakehouse；
- worker 集群调度 → GeoCompute；
- 科学算法语义 → Spatial Science；
- 单源 `plan_query` 行为逐位不变（V6 是联邦层的纵向深化，不动单源语义）。

**兼容红线**：V5 公共 API（`plan_federated_chain` / `execute_chain` / `plan_query`）签名与行为默认逐位不变；V6 通过显式 `engine="v6"` 入口接入生产工具，V5 路径保留为回退。

## 4. Subagent 审计增补（context-sweeper，独立验证）

主 agent 独立审计后，Subagent A 的全仓扫描确认了上述事实，并补充：

- **生产路径第三层**：REST `POST /data-fabric/catalog/{id}/query`（routes/data_fabric.py:867）→ manager → **adapter 内部**调 normalize+plan_query（postgis_adapter.py:663-717），plan/evidence 附 QueryResult.metadata。联邦仅工具面暴露（无 REST）。
- **statistics 已有**：PostGIS pg_stats collector（statistics.py:244-272）、GeoParquet footer collector（275-332，仅本地路径）、进程 TTL + DurableStatisticsStore（351-501）、`statistics_for_request` fail-open（162-187）。
- **pushdown 分级**：`PushdownClass = exact|equivalent|coarse`（pushdown.py:23-28）；`limit` 族恒 EXACT（:83）；`join` 族恒 UNSUPPORTED（:84）。**CQL2-JSON 是 ADR-0094 显式 Deferred** —— V6 不做，沿用 CQL2-text conformance 探测路径。
- **streaming.py 已有**：iter_batches/stream_filter/stream_aggregate + governor 批大小折半（batch_size_for:43-69）+ 批边界取消钩子（28-41）；arrow_ops 谓词不可等价执行时 typed 拒绝。**V6 物理层复用这些原语**。
- **accumulators.py AggregateDriver**：dict lane 与 Arrow lane 单一语义真相（test_data_plane_arrow_v5.py 差分对齐）—— V6 BatchAggregate 必须委托它。
- **ADR-0101 D7 明示 deferred**："no bushy join-tree search"、"multi-hop in-database federation"、"Arrow batches between seams" —— 本 Epic 即这些 deferred 的纵向落地。
- **查询结果缓存不存在**（Deferred），query_fingerprint 只进审计行；capability/provider 版本不在 key 内（审计项记录为 known limitation，不在本 Epic 引入结果缓存）。
- **安全网测试**：test_data_fabric_optimizer_v5.py 的位级兼容 + TestSplitExecutionParity 差分是 V6 不可破坏的红线。
- **query/ 目录 TODO/FIXME 几乎为零**（仅 feedback.py:132 跨进程持久化 Deferred）。

结论：Scope 冻结（§3）不变；W2 统计扩展改为 **additive dict 字段**（沿用 QueryPlan.cost 的"dict 形态避免反向依赖"先例）；W6 物理执行器复用 streaming.py governor + accumulators.py AggregateDriver；W7 不做 CQL2-JSON（Deferred 尊重），下推解释复用 pushdown_classes 分级。
