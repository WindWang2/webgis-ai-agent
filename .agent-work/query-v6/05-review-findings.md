# Review Round 1 发现与修复（独立 reviewer：架构/正确性/回归）

Reviewer 全新上下文，对 445ad30..HEAD 全量 diff 走读 + 运行时探针验证。

| 级别 | 发现 | 修复 |
|---|---|---|
| CRITICAL C1 | 混合 CRS 多跳链：join 的 CRS 决策用**边声明源**的 CRS 代表整个左子树 → hop2 误判 aligned，静默错位比较（V5 原为 typed fail-fast） | `_Candidate.out_srid` 传播子树输出 CRS；DP 与 fixed-chain 两条路径的变换决策均以子树输出 srid 为输入；回归测试 `test_mixed_crs_multi_hop_propagates_subtree_output_crs` |
| CRITICAL+（C1 探针推广） | 空间跳 build 侧为 join 子树时，累积行几何存于 `__right_geometry__`，`spatial_join_local` 读 "geometry" → 静默空结果 | 枚举器守卫：空间跳右孩子必须是原始 scan（或其 Reproject 包装） |
| MAJOR M1 | V6 绕过 V5 planner 校验：limit 超预算静默钳制、缺 join 字段/spatial_op 静默 0 行/默认 within | 从 `plan_federated_chain` 提取 `validate_chain_shape`（单一真相）；`execute_chain_v6` 以 `check_crs_mix=False` 调用（混 CRS 是 V6 有意差异） |
| MAJOR M2 | `derive_projection` 在 v6 引擎被静默忽略 | 接线：V6 计划序 == given 序时复用 V5 `derive_chain_fields` 并把派生投影写回 scan 节点；重排序时不派生（多取全列 + warning 披露） |
| MAJOR M3 | 自适应尾重排结构性不可达（tail_edges 只含链邻接边）+ replan 分支双执行 bug | tail_edges 从完整 edge_specs 构建（入口边 = 任意已消费源 → 尾源，`_chain_row_key` 可穿透取键）；重建失败退还预算；采纳后 `i += 1` 消除双执行；entry_sources 门槛 + 采纳集成测试 |
| MAJOR M4 | Bloom 键归一化与 `_hashable_key` 相等类不完全一致（list/dict vs 同文本 str；Decimal 类）→ 理论假阴性 | `canonical_variants`：list/dict → "s:"+str（与 str 同槽）；未知标量产出数值/对象多变体，add 全部置位、contains 任一命中即保留（假阳性方向冗余） |
| MAJOR M5 | 空间统计/caps 在生产为 façade（无探测注入），EXPLAIN 文案夸大 | EXPLAIN 网络窗口改从计划树 scan 渲染真实 fetch_window；pushdown_boundary 保留 "not probed" 诚实文案；ADR 明确"提示驱动、探测注入 follow-up" |
| MAJOR M6 | F3 hop 位置 1-based/0-based 双路径不一致（首跳误触发几何检查） | 静态路径 `_left_depth - 1`（0-based），与自适应路径及 V5 `src_pos` 对齐 |
| MINOR m2 | 聚合 join 基数 = left×right 高估一量级 | 改为 `min(left.card, groups)`（每左行归属恰一组） |
| MINOR m3 | PhysicalExecutor 复用时 trace 累计 | execute() 起始重置 trace |
| MINOR m5 | `node.kind != "spatial_join"` 恒真死分支 | 改为 `join_kind` |
| MINOR m9 | 同源 server 委托判定不含 adapter 实例同一性 | 与 V5 同款 `adapter0 is adapter1` 判定 |
| NIT | explain where 文案夸大等 | 随 M5 修复 |

验证：修复后 V6 targeted 341 passed；**完整 tests/unit 8527 passed, 105 skipped**（8m10s）。

未修复（记录为 follow-up）：m1（extract_hop_estimates bushy 次序）、m6 部分常量镜像、m7（hop 内取消检查点，V5 parity）、m8（bushy plans 展示序）、m10（offset 分页窗口）、NIT 组。
