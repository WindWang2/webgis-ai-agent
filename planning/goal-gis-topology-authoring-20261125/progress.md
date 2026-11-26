# Progress — goal-gis-topology-authoring-20261125

## 提交记录（本分支）
1. `a8f8834cb` 共享拓扑结构索引与跨要素约束编辑算子（WP1–WP3 服务层）
2. `5ead353a0` 共享拓扑约束面 REST/Pi 工具与真实事务验证（WP4–WP5 后端）
3. `614d560c4` 前端共享顶点编辑工作台（WP5 UI）
4. `587be3977` WP6 质量/性能/可复现审查
5. `0e8078bc4` Playwright 旅程 j50
6. `c41ce7608` 补交 WP4/WP5 接线文件（stash 往返遗漏）
7. `8f348e47f` progress/acceptance 账本
8. `43e857477` R1 冷审修复轮（P1×5 + P2×2）
9. `54ab9a2bc` R2 复审修复轮（N1–N5）
10. `7fb7ace33` R3 多 path 同步回归修复
11. `1e9373c80` 提案候选重放层级守卫对齐
12. R4 最终聚焦复审：approve，未发现新 P0–P3（249 passed / ruff 干净）
13. `docs(planning)` 最终验收/PR 文档；PR [#2279](https://github.com/WindWang2/webgis-ai-agent/pull/2279)

## WP 完成状态
- WP1 共享空间结构建模：✅ shared_topology.py（exact/容差/level_field 三种匹配、
  500k 顶点预算、成员寻址表、边索引）；typed impact plan（plan.impact +
  summarize.impact_summary）。ADR-0304。
- WP2 面状共享边编辑：✅ move/insert/delete/reshape 四 op 双侧精确同步；
  基线/新增违规分离（身份键 + 重叠面积严重度：相对 >5% 且绝对 >0.5m²
  实质扩大视为新增；新增阻断、存量披露）；提交门同语义；多 path 同步；
  环闭合同步；测地面积变化披露。
- WP3 线网约束：✅ connect_endpoint（容差 fail-closed + level 守卫拒绝桥隧
  误连）；悬挂端点披露（inspect 工具）；线网自触经 no_self_intersection
  阻断的实测用例。
- WP4 原子提交/回滚：✅ 真实事务链（preview→approve→commit 两面同步→undo
  字节级恢复→redo）；信封 level_field 在预览/提交/提案重放同源；提交期
  拓扑门与预览同语义；幂等重放零二次写；stale CAS 409；reject 零写入；
  跨数据集 typed 披露（blocked，不尝试半提交）。
- WP5 用户与 Pi 同链：✅ 三工具（inspect_edit_constraints/get_impact/
  revalidate，tier2 只读）+ 既有 preview/commit 工具承载共享 op；REST
  三端点（constraints/inspect、impact、revalidate，require_owned_session）；
  前端共享顶点拖拽（客户端 provisional 高亮 + 服务端权威传播）、提案卡
  impact 区块、i18n 独立命名空间。
- WP6 质量/性能：✅ 五 op 逆元恒等、同种子重放一致、交换序恒等、共享结构
  不变量；1k/10k 网格 perf 实测；顶点预算诚实拒绝；journal 指纹链不变量。
- Pi live 旅程：⛔ blocked（master 的 vendor/pi 为指向已删除路径的符号链接
  blob dd645f50；entry_built=False；既有 Pi journey 测试按设计显式 skip；
  Pi 打包归 G12/方向 01 所有）。替代证据：真实 ToolRegistry.dispatch +
  真实 session store 的工具链测试 + REST 事务链测试。
- Playwright e2e：✅ j50（mock 档，2 例通过；registry 注册；锁测试 27/27）。
  real 档 journey（j24 同款直驱 REST）未新增 —— 本地无 Redis/Celery 服务，
  不以线上 CI 为门禁；mock 档 + 组件/事务测试覆盖同等断言面。

## 修复的既有域内缺陷（随本分支）
1. journal invert_deltas 正序逆回放 → 同要素多几何 op undo 停在中间态（P1）
   —— 复现（replace_geometry+move_by）后修复为逆序回放。
2. pyproj≥3.8 数组广播破坏 move_by 面/线位移（本机 3.8.0 实证 GeodError）
   —— 显式全数组调用修复。

## 已知既有问题（非本分支引入，已核实基线复现）
- feature_proposals 套件先跑时污染 event loop，G31 repair 两个测试
  （test_repair_ownership_choice_required / test_repair_candidates_no_deterministic_op）
  在组合运行下失败（asyncio.get_event_loop 弃用 + Python 3.12）；单独运行通过。
- 前端全套件 3 个既有失败（i18n key-completeness 占位符 + geoai-panel 2 例）
  —— stash 基线复核确认。
- ruff 0.17.6 对 lakehouse/raster_tile 的 9 处告警为版本差异噪声（未触碰文件）。
