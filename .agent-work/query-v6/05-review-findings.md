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

# Review Round 2 发现与修复（独立 reviewer：性能/安全/可维护性/故障披露）

| 级别 | 发现 | 修复 |
|---|---|---|
| CRITICAL C-1 | R1 的 M2 修复缺陷：`_apply_scan_fields(chain_to_logical(req))` 重建 given 序树 → **丢弃 LogicalReproject 节点**，混 CRS 链 + 默认 derive_projection 静默 0 行且 EXPLAIN 谎报变换（reviewer 实测复现） | 改为在实际 `plan.tree` 上写投影（保留全部节点）；新增生产入口回归测试 `test_production_entry_mixed_crs_with_default_derive_projection` |
| MAJOR M-2 | 差分语料绕过生产入口 `execute_chain_v6`（R1 的 M2 分支因此漏网） | 新增生产分派差分：混 CRS 默认配置 + 语料子集（属性/空间/三跳）经 `execute_chain(engine="v6")` 双引擎对齐 |
| MAJOR M-1 | 工具文案宣称"自适应重排"但生产 n-1 边约束下采纳路径不可达 | 工具 engine 描述改为诚实表述（"仅在 join graph 存在替代有向链时触发"）；执行器保留 edge_specs 超集能力（有测试锁定） |
| MINOR m-1 | 无统计的 id 寻址链收到误导性 "positional joins" warning | `_enumerate_fixed_chain(positional=)` 区分两种回退语义 |
| MINOR m-2 | EXPLAIN 宣称 MAX_JOIN_CANDIDATES 硬界但执行器不消费 | 文案改为真实口径（fetch_window + budget.max_rows 逐跳 fail-fast） |
| MINOR m-3 | `_chain_row_key` 逐行函数级 import（20 万行 ~20ms） | 提升到模块级 |
| MINOR m-4 | Bloom 生效后 keyset semi-join 无条件二跑 | Bloom 已生效时跳过（键超集过滤，精确键集为重复遍历） |
| MINOR m-5 | 镜像常量/函数无对账 | 新增 `test_federated_mirror_parity.py`（8 项：源数/占位行数/收缩因子/CRS 解析同域等价/页大小/自适应阈值/键上限） |
| MINOR m-6 | 回退 warning 内嵌原始异常文本 | 截断 200 字符 |
| MINOR m-7 | engine 非法值静默按 v5 | 工具层 strict 校验（非法值 INVALID_QUERY） |
| NIT | 死 edge_index、physical 未用 logger、_scans 重复导入 | 清理 |

安全核查（未上报 = 干净）：注入面（where 经 normalize/参数化编译）、多租户 owner 作用域、DoS/预算边界、日志泄露、warnings/notes 界、engine 可区分性、FederatedChainRequest 关键字构造兼容（全仓 50+ 调用点）。

验证：R2 修复后完整 tests/unit **8537 passed, 105 skipped**（7m36s）；`ruff check app tests` 全绿。
