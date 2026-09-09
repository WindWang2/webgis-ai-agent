# V6 架构决策（Phase B）

## 1. Current-state → Target-state

### 现状依赖图（V5）

```
tools/data_fabric_tools.py (query_federated_data / query_federated_chain)
        │
        ▼
federation.py ── plan_federated / plan_federated_chain（左深序 ≤24 枚举）
        │                │ 复用：capabilities / statistics / selectivity / pushdown
        ▼                ▼
FederatedExecutor.execute_* ── adapter.query(QuerySpec) 逐源一次有界拉取（list 物化）
        │
        ├── spatial_join_local / attribute_join_local / aggregate_join_rows（本地原语）
        ├── _semi_join_reduce_right（键集 ≤1000）
        └── feedback_store（planning-time 行数修正）
```

### 目标组件图（V6 新层，全部在 `app/services/data_fabric/query/federated/`）

```
data_fabric_tools.query_federated_chain(engine="v6" 默认)
        │
        ▼
federation.execute_chain(req)  ── engine 分派（默认 "v5" 逐位不变）
        │ engine="v6"
        ▼
federated/planner.py::plan_federation_v6(req, ctx)
        │  ① chain_request → logical.py 计划树（typed IR + canonical hash）
        │  ② enumerator.py：join graph 上 left-deep + bushy DP（n≤4 全子集，结构性有界）
        │     每个 partial plan 由 costing.py 估价：
        │       - 传输字节（投影感知 per-feature bytes）
        │       - join 基数（属性 NDV 模型 / 空间直方图模型）
        │       - CRS 变换成本（placement：server / local / 拒绝；单侧变换；地理距离守卫）
        │       - semi-join/Bloom 盈利阈值
        │       - 聚合下推收益（caps.aggregation 时按组行传输）
        │  ③ 产出 PhysicalChainPlan（含 rejected alternatives ≤8，EXPLAIN 证据）
        ▼
federated/executor.py::execute_physical(plan, ctx)
        │  - PhysicalScan：adapter 分页批拉取（generator，绝不全量物化）
        │  - BoundedBatchChannel：有界队列 = backpressure
        │  - cancellation：threading.Event + deadline，逐批检查
        │  - BloomSemiJoin（bloom.py，确定性 sha256 双哈希，阈值盈利才启用）
        │  - HashJoin / SpatialIndexJoin（build 侧受 budget.max_rows 硬界）
        │  - BatchAggregate（复用 accumulators.py）
        │  - adaptive.py：逐跳观测 est vs actual，偏差越阈 → 一次性受护栏 replan
        ▼
federated/explain.py → result["explain_v6"]（计划树 + est/actual + 下推边界 + 物化点 + CRS 变换点）
```

## 2. 权威数据/状态所有权（无第二事实源）

| 真相 | Owner | V6 关系 |
|---|---|---|
| 查询语义（谓词 AST） | query/predicates.py + QuerySpecV2 | V6 IR 节点**引用**同一 Predicate 模型，不复制 |
| 源能力 | AdapterCapabilitiesV2（capabilities.py） | V6 costing 只读消费 |
| 统计 | DatasetStatistics（statistics.py） | **就地扩展**：spatial_histogram / avg_vertices / crs 字段（additive, Optional） |
| 预算 | ExecutionBudget / StreamingBudget | V6 执行器逐批强制，同一 typed error |
| Adapter 访问 | FederatedExecutor._adapter_factory 注入 | V6 PhysicalScan 委托同一 adapter.query 契约 |
| 执行反馈 | feedback_store（feedback.py） | adaptive 复用 record()，不建新 store |
| 结果契约 | execute_chain 返回 dict 形状 | V6 输出同形状 + additive 字段（engine/explain_v6/replan） |

**明确不做**：不建新 registry/store/manifest；不改单源 `plan_query` 语义；不重复 compilers/pushdown（逐子句拆分直接调用既有函数）。

## 3. 公共 API / typed contract

- `FederatedChainRequest` **additive** 字段：`engine: str = "v5"`（dataclass 默认逐位兼容）。
- `federation.execute_chain(req)`：`engine="v6"` 时走 V6；V6 内部非 typed 异常 → 一次性地回退 V5 执行并在 warnings 披露（typed DataFabricError 原样上抛，与 V5 语义一致）。
- `data_fabric_tools.query_federated_chain` 新参数 `engine: str = "v6"`（生产路径默认 V6；差分测试背书）。
- V6 新公共面：`query/federated/{logical,costing,enumerator,physical,bloom,adaptive,explain}.py`，经 `federated/__init__.py` 导出最小集合。

## 4. 关键语义决策

1. **Join 枚举**：n≤4 → 全子集 DP（16 subsets），join graph 允许树形（chain 是特例）；cost pruning 只保留每 subset 最优 k=3 个计划（有界、确定性）。位置寻址 joins 无法重排 → given 序（V5 parity 路径）。
2. **CRS**：V5 链内混 CRS = 计划期 typed 失败（federation.py:975）；V6 中 CRS 变换成为**可估价算子**：优先 server_reprojection（免费相对项）→ 否则本地批量变换**较小一侧一次**（进入 build 缓存，绝不逐行重复变换）；dwithin 在 EPSG:4326 上标记 geodesic 正确性约束（本地先投影再距离，成本含投影）。
3. **Bloom semi-join**：只做**键过滤**（语义安全）；盈利 = 估计约减字节 > 构建成本 × 系数；键集/位图大小有界（≤1M bits）；超界或键全 None → 回退 V5 键集 semi-join（诚实放弃）。
4. **聚合下推**：aggregate_join 跳右源 caps.aggregation=True → group_by+aggs 编译进该源 QuerySpec（拉组行而非全行）；无能力 → 本地（V5 parity）。下推决策解释写入 explain。
5. **Adaptive**：每跳结束 actual/estimated 偏差 > 4× 且剩余跳 > 0 → 用观测行数重估剩余子图成本；最多 **1 次 replan**；新序必须更优才切换，否则保留原计划（确定性 fallback）；`order_strategy="given"` 时禁用。
6. **流式**：批 = adapter 分页返回的一页 features；build 侧（右/维表）仍有硬上限（MAX_JOIN_CANDIDATES，V5 parity）；probe 侧分页流过，backpressure 由有界 channel 实现；绝无全结果拼接（输出行受 limit + budget 界）。
7. **确定性**：同一输入（spec/stats/caps/seed）→ 同一计划 hash、同一 rejected 序、同一结果序（稳定排序 + tie-break by source_id）。

## 5. 失败模式与回退

| 失败 | 处理 |
|---|---|
| V6 planner 非 typed 异常 | 记 warning，回退 V5 executor（一次性） |
| 预算超限（rows/bytes/deadline） | QueryBudgetExceededError 原样上抛（两引擎一致） |
| Bloom 误判 | 只可能**漏**（假阳性保留，假阴性不存在）→ 不影响正确性 |
| replan 后计划不可行 | 保留原计划 + warning |
| shapely 缺失 | STRtree 退化线性扫描（V5 原语已有），候选积守卫不变 |

## 6. 性能预算（结构性，不依赖整机瞬时 wall-clock）

- 基准断言维度：**transferred rows/bytes（pushdown on vs off 比值）**、build 侧行数上界、批大小、replan 次数 ≤1、枚举候选 ≤ 界。
- differential benchmark 记录 baseline 表（见 04-test-matrix.md），wall-clock 仅作参考不作门。

## 7. 与并行 Epic 的边界

- 不触碰 `geocompute-v6`（cluster runtime）、`harness-v5`（autonomous runtime）worktree 的所有权区域。
- 共享文件仅 additive 最小修改：`data_fabric_tools.py`（工具参数 + 结果字段）、`statistics.py`（Optional 字段）、`federation.py`（engine 分派）。无 migration、无 OpenAPI/前端契约变更（工具是 agent 内部表面，参数 additive）。

## 8. 为何不能复用现有实现（新组件辩护）

- **logical.py**：V5 无计划树（flat dict）；FederatedPlan 是两源跳描述，无节点语义/哈希/规范化 —— 无法承载 bushy 枚举与 est/actual 对照。
- **enumerator.py**：`_chain_plan_order` 只枚举线性排列且成本模型无 CRS/空间/下推维度；DP 子集枚举是不同算法域。
- **costing.py**：optimizer.PlanCost 是单源排序分数；CRS 变换/空间直方图/Bloom 盈利是新成本维度，且 PlanCost 被既有 EXPLAIN 逐位锁定 —— 新模型独立并引用其权重常数。
- **physical.py**：FederatedExecutor 逐跳全量 list 物化（federation.py:1208-1225 的 `_fetch` 是一次性拉取）；批处理/backpressure/cancellation 是新的执行语义，不能原地改（会破坏 V5 逐位兼容红线）。
- **bloom.py**：`_semi_join_reduce_right` 键集 O(keys) 内存且无概率压缩；网络字节敏感场景需要 Bloom。
- **adaptive.py**：feedback.py 只在 planning-time 修正估计；执行期偏差驱动的受控重排是新的控制环。
