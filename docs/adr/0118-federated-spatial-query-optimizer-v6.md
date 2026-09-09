# ADR-0118: Federated Spatial Query Optimizer V6

**Date:** 2026-09-09
**Status:** Accepted
**Extends:** ADR-0101 (GeoCompute & Data Fabric V4, D7/D8 deferred 项的落地), ADR-0096 (Data Plane V3), ADR-0094 (Data Fabric V2)
**Branch:** `feat/query-v6-federated-spatial-optimizer`
**Audits:** `.agent-work/query-v6/00-baseline.md`（主 agent + context-sweeper 双审计）

## 背景与问题

V5 链式联邦（ADR-0096 D3 / ADR-0101 D7）交付了受控左深链：≤4 源、≤24 排列有界
序枚举、键集 semi-join、逐跳预算 fail-fast。但三类能力缺口把 N 源联邦限制在
"基本 pushdown"层面：

1. **无计划搜索**。`QueryPlan` 是扁平能力决策记录（无 operator IR）；join 序枚举
   仅线性排列（无 bushy）；成本模型无空间选择性、无 CRS 变换维度 —— ADR-0101 D7
   明示 "no bushy join-tree search — that stays deferred"。
2. **空间不可估**。空间谓词选择性只有 bbox 面积比；无直方图、无几何复杂度；
   CRS 混用在链内直接 typed 失败（无变换放置决策）。
3. **执行全量物化**。链上每源一次有界全量拉取到 list；无逐页取消点、无 Bloom
   预滤、聚合逐跳物化全部 join 行。

## 决策

新增 `app/services/data_fabric/query/federated/` 优化层，**经 `engine` 分派接入
生产路径**（V5 路径默认位级不变）：

```
FederatedChainRequest(engine="v5"|"v6")
  └─ execute_chain ─┬─ v5: execute_federated_chain（位级不变）
                    └─ v6: plan_federation_v6（typed IR → 子集 DP → 成本）
                          └─ PhysicalExecutor（分页批执行 + 取消 + Bloom + 自适应）
```

单一事实源约束：谓词 AST（`query/predicates.py`）、capability 矩阵
（`AdapterCapabilitiesV2`）、统计（`DatasetStatistics`）、预算
（`ExecutionBudget`/`StreamingBudget`）、adapter 契约、反馈环
（`feedback_store`）全部复用；不建第二 registry/store/manifest。

### 核心组件

| 组件 | 职责 | 关键决策 |
|---|---|---|
| `logical.py` | typed 计划树（scan/filter/project/join/aggregate/sort/limit/reproject）+ canonical `plan_hash` | 成本提示不进哈希（提示更新不漂移计划指纹） |
| `spatial_stats.py` | 确定性网格直方图 + 几何复杂度 + CRS；`DatasetStatistics` additive dict 字段 | 无 extent 不产直方图；basis 恒随行（measured/assumption） |
| `costing.py` | 空间选择性（直方图比 × op 收缩因子）+ CRS 变换决策（两侧估价取最小） | dwithin 地理 CRS 恒标注度近似；`allow_server=False` 默认 |
| `enumerator.py` | join graph 子集 DP（n≤4 全子集、每子集 top-3、披露 ≤8），left-deep + bushy | 方向语义守卫：边方位决定 `__right__` 归属，绝不换位；无提示时保持 given 序（V5 parity） |
| `physical.py` | 分页探针（逐页取消/超时检查）、Bloom 预滤（无假阴性）、pyproj 一次性变换 | build 侧仍受 `MAX_JOIN_CANDIDATES` 硬界（V5 红线） |
| `executor.py` | 计划树求值；行形状/预算行为镜像 V5（差分锁定） | 自适应仅对纯链形计划；bushy 中途重排是 follow-up |
| `adaptive.py` | est vs actual 偏差观测（阈 4×）+ 一次性受护栏尾重排 | 更优才切换；`order_strategy="given"` 禁用；拒绝也消耗预算 |
| `explain.py` | 计划树 + est/actual + 下推边界 + 物化点 + CRS 变换 + 替代披露 | 确定性文本 |

### 语义红线（差分锁定）

- **优化器不改变结果语义**：W11 差分语料（10 种子 × 属性/数值键/空间/聚合/bbox/
  空集/limit/where/全 NULL 组）断言 V6 ≡ V5 行集逐位一致。
- **V5 公共 API 位级不变**：`FederatedChainRequest.engine` 默认 `"v5"`；既有
  143 项 V5 测试全绿。
- **计划即执行**：只产本地 CRS 变换决策（server placement 需跨 adapter 的
  output.crs 管道 —— 显式 follow-up）；fetch 窗口保持 V5 奇偶（差分语料证明
  build 侧窗口放宽会改变结果，列为 follow-up）。

## 行为变更（engine="v6" 的完整 delta 清单，评审 R1 后修订）

`query_federated_chain` 工具 `engine` 参数默认 `"v6"`。V6 相对 V5 的**全部**
行为差异（结构校验经 `validate_chain_shape` 单一真相共享，除下列有意差异外
逐字一致）：

1. **树形 join graph 可执行**：星形图（两跳共享父源）不再 typed 失败 —— V5
   左深链契约以 `engine="v5"` 显式测试锁定；V6 行为改进由对照测试锁定。
2. **链内混 CRS 可执行**：V5 计划期 typed 失败；V6 在计划内插入本地
   `LogicalReproject`（一次性变换较小总成本侧，子树输出 CRS 逐跳传播）。
3. **`derive_projection` 仅在 V6 选择 given 序时生效**：派生投影的"下一跳
   左键"集合依赖跳序；重排序下宁可多取全列（如实 warning 披露），绝不缺
   字段静默失真。
4. **自适应尾重排**：观测偏差 ×4 触发、一次性、更优才切换；重排连通性来自
   完整 join graph（edge_specs），入口边从任意已消费源出发。

V6 内部非 typed 异常自动回退 V5 并在 warnings 披露；typed 错误（预算/构造）
与 V5 同契约原样上抛。统计/能力提示（`stats_hints`）直接驱动成本；
**adapter 能力探测（caps）注入是 follow-up** —— 未探测时 EXPLAIN 如实标注
"capabilities not probed"。

## Consequences

- 正面：N≤4 源真实 cost-based 树形规划；空间选择性/CRS 进入成本与解释；
  流式批执行（逐页取消/超时）；Bloom 预滤（盈利阈值下）；自适应重排
  （受护栏）；EXPLAIN 可解释 pushdown 边界与 est/actual。
- 代价：`query/` 增加 ~3.5k 行优化层；两个 planner 并存直到 V6 差分覆盖成熟
  （engine 分派是刻意的过渡形态，非第二事实源 —— 同一基底、同一输出契约）。

## Known Limitations / Follow-ups

1. **CQL2-JSON 下推**：沿 ADR-0094 Deferred（CQL2-text conformance 探测路径已够）。
2. **server-side CRS 变换放置**：需 legacy QuerySpec 增加 output.crs 下推管道
   （跨 adapter 契约变更）；当前一律本地 pyproj 一次性变换。
3. **build 侧 fetch 窗口放宽**：可提升 join 覆盖（找回 V5 欠取漏配），但属
   结果语义契约变更 —— 差分语料已证明，需独立决策。
4. **bushy 计划的自适应重排**：当前自适应仅链形计划；树形中途重排需要
   子树级 estimate 传播。空间跳的 build 侧限定为原始 scan（join 子树的
   累积行几何存于 `__right_geometry__`，`spatial_join_local` 读取不到 ——
   枚举器已加方向/形状守卫）。
5. **跨进程 feedback / 查询结果缓存**：沿 ADR-0101 Deferred 不变。
6. **聚合下推到源**：aggregate_join 语义是"先连接后聚合（左优先取值）"，
   源侧 GROUP BY 会统计未命中行 —— 语义不安全，已在 EXPLAIN 中作为
   rejected alternative 披露。
