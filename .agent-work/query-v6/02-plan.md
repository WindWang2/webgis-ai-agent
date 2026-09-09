# V6 实施计划（12 waves）

每个 wave：契约/测试先行 → 最小闭环实现 → targeted pytest → ruff → 小步 commit。

| Wave | 内容 | 主要文件 | 验收 |
|---|---|---|---|
| W1 | Typed logical plan IR：节点（scan/filter/project/join/spatial_join/aggregate/sort/limit/reproject）、canonical dict、确定性 plan_hash、chain_request → tree 构建器 | `federated/logical.py` + `tests/unit/test_federated_logical_plan.py` | hash 确定性/规范化/可序列化 |
| W2 | 空间统计：SpatialGridHistogram（确定性网格）、geometry 复杂度、CRS、freshness；DatasetStatistics additive 字段 + 收割函数 | `federated/spatial_stats.py`、`statistics.py` | 直方图确定性、诚实 unknown |
| W3 | 空间选择性 + CRS 成本：bbox/intersects/within/dwithin 估计（直方图驱动）、geometry 复杂度因子、index 感知、transform placement/单侧变换/地理距离守卫 | `federated/costing.py` | 估计有 basis 标注；CRS 进 cost |
| W4 | Join 枚举：join graph、left-deep + bushy DP（n≤4 全子集）、每 subset top-k 剪枝、rejected alternatives ≤8 | `federated/enumerator.py` | 确定性、有界、bushy 可达 |
| W5 | Bloom semi-join：sha256 双哈希、位图有界、盈利阈值、V5 键集回退 | `federated/bloom.py` | 无假阴性；超界回退 |
| W6 | 流式物理执行：PhysicalScan 分页批、BoundedBatchChannel backpressure、cancellation/deadline、HashJoin/SpatialIndexJoin/BatchAggregate/BatchLimit | `federated/executor.py` + `federated/physical.py` | 有界内存；取消响应 |
| W7 | 下推 V6：聚合下推（caps.aggregation）、每源 fetch 窗口（基数驱动 limit）、下推边界解释 | `federated/planner.py`（V6 入口） | 组行传输 < 全行；解释 why |
| W8 | Adaptive：逐跳 est vs actual、偏差越阈一次性 replan、guardrails、feedback 接线 | `federated/adaptive.py` | replan ≤1；given 序禁用 |
| W9 | Explain V6：计划树渲染、est vs actual、下推边界、物化点、CRS 变换点 | `federated/explain.py` | 行文本确定性 |
| W10 | 生产接线：engine 分派 + V6 planner/executor 入口 + 工具参数（默认 v6）+ 回退 + 契约测试 | `federation.py`、`data_fabric_tools.py` | V5 路径逐位不变；V6 真实接入 |
| W11 | 差分正确性：种子化随机语料（几何/NULL/CRS 边界）、V6 vs V5 参考执行逐行一致 | `tests/unit/test_federated_v6_differential.py` | ≥50 语料全绿 |
| W12 | 基准 + 文档：N 源/选择率/延迟模拟/字节传输结构断言；ADR-0118；progress 汇总 | `tests/benchmarks/test_federated_v6_perf.py`、`docs/adr/0118-*.md` | 结构性断言非 wall-clock 门 |

## 提交序列（Conventional Commits）

每个 wave 1 个 commit：`feat(query-v6): W<n> ...` / `test(query-v6): ...`。W10 可能拆 `feat` + `test` 两个。

## 风险与缓解

- **V5 逐位兼容**：所有 V5 公共函数默认路径不动；V6 全部新代码路径；W10 前后各跑一次 federation/planner 既有全量测试。
- **上下文/资源**：targeted tests 优先；全量回归仅在 W10/W12/rebase 后跑（-x -q，timeout=60s 自带）。
- **subagent 预算**：审计已由主 agent + 1 个 context-sweeper 承担；review 阶段启用 1 个 reviewer subagent（共 2 个角色）。
