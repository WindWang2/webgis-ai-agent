# Review 记录（Rounds 0-2）

## Round 0 — Subagent-A 架构挑战（Phase B）
3 CRITICAL（D6 证明/D5 delivered_crs/D8 契约）+ 4 MAJOR → 全部修订进
01-architecture.md §8（R-C1/C2/C3、R-M1/M2/M3），实现前闭环。

## Round 1 — Subagent-A（2026-09-09，实现后只读 diff 审查）

### 必修（全部已修）
| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| 1 | CRITICAL | Arrow lane 丢 bbox 行级过滤（行组剪枝≠行过滤）→ 超集错结果 | arrow_lane `_bbox_passes`（与 dict lane `_row_passes` 包围盒相交语义逐位一致）；回归 test_arrow_lane_bbox_* |
| 2 | CRITICAL | R-C3 回写守卫失真：截断行数/组数冒充 observed 总量 | executor trace 新增 `per_source_scan_complete_unfiltered`（无过滤 ∧ 取回 < 窗口）；aggregate 分支恒不算；feedback 消费 trace 事实，启发式恒 False 兜底 |
| 3 | CRITICAL | R-C1 证明被嵌套左子树绕过 + seen 去重静默丢重复组行 | 改写限定 `node.left` 为 LogicalScan；回归 test_aggregate_pushdown_not_applied_for_nested_left_subtree |
| 4 | MAJOR | _matching_edge 按 source_id 猜边（同右源多边选错） | 逐项相等（source_id×2/join 字段/group_by 排序/aggregates 规范 JSON） |
| 5 | MAJOR | 聚合字段左优先解析 vs 下推纯右值不等价 | 证明新条件：聚合字段 ∈ 左投影已知字段集 → 拒绝；左未投影（字段未知）→ 拒绝；测试数据去碰撞 + 回归 |
| 6 | MAJOR | 下推组值类型偏离 finalize 口径（Decimal/int） | `_project_pushed_groups` 对 sum/avg 做 float 归一（累加器 0.0 起步口径） |
| 7 | MAJOR | bushy replan 跨基准比较 + 重执行失败丢首结果 | previous_cost = 原序在 **pinned 估计下**的链形重估（_enumerate_fixed_chain）；重执行 try/except 回退首次结果并披露 |
| 8 | MAJOR | 负缓存 dead code；错误面反馈缺失 | execute_chain_v6 typed 错误路径接 put_negative（仅 unreachable/auth）+ `_v7_record_feedback(outcome="error", error_code=...)`（均 fail-open 后原样上抛） |

### MINOR（本轮已收口）
- 9 attach 并发：回滚仅删**本次 revision** 条目（不误删并发方）
- 10 secret dedupe：命中刷新 TTL 时刻（长活连接不自愈失效）
- 12 arcgis delivered_crs 与 outSR 自相矛盾 → `_arcgis_out_sr` 单一真相共用
- 13 counters：`crs_server_placements` 接线（server placement 成功计数）；
  删除恒 1.0 的 `pushdown_ratio` 假口径

### 未采纳/带入 Round 2
- 11（全局域连接 scope 校验 vs session 侧）：缓存双防线语义已覆盖实际风险；
  registry 连接 scope 与 session scope 是不同抽象，PR 中披露口径
- NIT（arrow 末页超收/probe cost 计入/self-join delivered 键）：披露为已知限制

### Round 1 后回归
- 联邦+fabric 全量：**289 passed**（+R1 回归测试 5 个后 294 passed）
- ruff（app/ + tests/）：0 错误
