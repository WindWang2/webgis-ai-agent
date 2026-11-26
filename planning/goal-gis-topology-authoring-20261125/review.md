# Review — goal-gis-topology-authoring-20261125

## R1 冷审（独立 agent，未参与实现；对抗探针 8 组自行设计执行）
- 结论：**fix-then-approve**。架构判定 sound（additive 词表、单一写路径、
  真实 CAS/journal 复用、诚实预算、只读约束面）；基线缺陷修复两项判定
  正确且安全（journal 逆序、pyproj 全数组）。
- 验收判定：1 PASS（F3 例外）、2 PARTIAL（F4/F5/F6/F7）、3 PASS（journeys
  PARTIAL：缺线网 REST 旅程）、4 PASS（F1 回归）、5 PASS（安全/鉴权）。
- 发现：P1×5（F1 rename REST 回归、F2 索引跨 op 陈旧、F3 基线增长被身份
  去重掩盖、F4 MultiLineString connect 逐 part 瞬移、F5 部分拒绝静默
  不对称）+ P2×2（F6 op 级容差/层级未达索引、F7 跨层级共编默认）+
  P3×5（F8 注入面、F9 截断披露、F10 共享 plan 需 targets、F11 鉴权负例
  依赖覆盖模式、F12 findings 24 条既有封顶）。
- 全部 P1/P2 与 P3 可行项已在修复轮 `43e857477` 关闭：
  - F1 validator 移除原键 + model_dump_op 还原（wire 契约回归测试钉住）
  - F2 任何 delta 后索引全缓存失效；陈旧坐标引用 typed 拒绝（语义钉）
  - F3 split_topology_findings 严重度感知（>5% rel 且 >0.5m² → 新增阻断）
  - F4 端点解析到 path 级，只动解析 part
  - F5 mutate 返回应用与否；任一拒绝 → typed finding + 零 delta
  - F6 op 参数按 (tolerance, level) 缓存键达索引；F7 信封级 level_field 默认
  - F8 注入限共享 op；F9 截断披露
  - 补线网 REST 旅程（connect_endpoint 真实事务链 5 步）
- 修复轮验证：tests/unit/feature_edit **196 passed**；ruff 干净；新增性质
  钉（stale 坐标 typed 拒绝、growth 阻断、MultiLineString part 不变、
  all-or-nothing、op 级容差全链、信封层级守卫）。

## R2 复审（另一独立 reviewer，从 diff 与自有探针验证修复）
- 结论：**fix-then-approve**。R1 五项 P1 + 两项 P2 全部 FIXED-VERIFIED
  （reviewer 自有探针逐项确认：rename wire 契约、索引失效、严重度感知
  分离、MultiLineString part 隔离、all-or-nothing、op 级参数达索引、
  信封 level_field、注入面限定、截断披露）；两项基线缺陷修复判定正确；
  单一写路径/plan.safe/鉴权/journal 闸全部 VERIFIED；suites+lint 干净、
  无修复轮回归。
- 新发现：N1(P1，修复轮引入) commit_plan 重求值缺 shared_level_field →
  信封层级数据集预览/提交分歧；N2(P2，既有门) 提交期拓扑门未用严重度
  感知分离 → 存量披露在 approve 被 400；N3–N6(P3，既有)；N7(账本卫生)。
- 修复轮 `54ab9a2bc`：N1 提交一致性（+批准链路回归）、N2 提交门与预览
  同语义（共享 op 基线/新增分离；非共享 op 语义不变）、N3 闭环删除陈旧
  重复、N4 Point 成员崩溃（视图归一 + typed 拒绝）、N5 impact 索引参数。
- 修复轮验证：feature_edit 202 passed；feature_proposals 45 passed；
  field_contracts + tools 112 passed；registry rationalization 208 passed
  （组合）；ruff 干净。

## R3 聚焦复审（仅验证 54ab9a2bc 增量）
- 结论：**fix**。N1–N5 全部 FIXED-VERIFIED（reviewer 自有 REST 探针
  对 parent commit 作判别：N1 父提交 changed_wfids=2 → 修复后=1；N2 父
  提交 approve=400 → 修复后=200；N3/N4 父提交陈旧重复/TypeError → 修复后
  正确；N5 容差邻接如实回报）；202 passed、ruff 干净，非共享 op 语义
  保持不变。
- 新发现：P1（修复轮引入）R2-N4 编辑时 mutate/环同步块被错误移出
  内层 per-path 循环 → 多 path 要素（MultiLineString 共端点、MultiPolygon
  触点、Polygon 壳+洞共点）只动最后一个 path，零 findings 静默撕裂几何。
- 修复（待提交）：将 mutate/Point 归一/环同步完整放回 per-path 循环，
  保留 R1-F5 all-or-nothing；新增 MultiLineString 双 part 共端点回归
  （两 part 同步移动 + undo 原字节恢复）。
- 修复后验证：feature_edit **203 passed**；ruff 干净。
- 其余 P3 follow-up：feature_proposals.candidate 重放缺 shared_level_field
  （当前无共享 op/level_field 提案入口，未达路径）；提交期存量违规不重复
  投影（preview.topology_context 已是披露面）。
