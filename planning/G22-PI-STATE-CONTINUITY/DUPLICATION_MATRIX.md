# DUPLICATION_MATRIX — G22-PI-STATE-CONTINUITY

> 能力 / 现状证据 / owner / 本任务剩余增量 / 共享接口 / 明确不做。

| 能力 | 现状证据（master @ b8f2a6ecc） | owner/PR/branch | G22 剩余增量 | 共享接口 | 明确不做 |
|---|---|---|---|---|---|
| Pi vendor pin / 兼容矩阵 | v1.5.1（gitlink dd30101c）；C1–C16 裁决；v1.5.2 初评"零 diff 不升" | G12 #2081（G22 接管独占） | 按 §3 清单实跑后升级 v1.5.2 + 矩阵 §4/§5 更新 + 构建产物 SHA 核验 + 回退演练 | docs/PI_UPSTREAM_COMPAT_MATRIX.md、build-vendor-pi.sh、pi_compat | 追 main、为升级而升级 |
| Pi 原生发现/嵌套/clear_queue/停机 | PI_RUNTIME_CONTRACT §12（ADR-0288） | G12 #2081 | 仅回归证明（A02–A06） | index.mjs、pi_native_surface | 重新实现 discovery、放松 scope 断言 |
| turn 相位状态机 | pi_turn_state.py TurnPhaseTracker（18 用例） | G01 #2012（ADR-0286） | P1 在 turn 转换边界补序列化：迟到 clear_queue/abort 不误伤后继 turn（G12 P2-1 残留） | bridge 收尾/准入 seam | 新状态机、turn-scoped vendor API 发明 |
| SSE resume / event ring | Last-Event-ID ring 重放（test_sse_resume.py） | G01 #2012 | P4 只补"重连不重执行 turn/不重复 mutation"的对账证据与缺口 | 路由层 ring buffer | 第二事件存储 |
| mutation receipts / CAS / undo | pi_surface_receipt、agent_undo #2035、MapSpec CAS | #2035/#2012 | P4 复用收据对账；receipt 失效（数据删除/权限变化）检查 | receipt 指纹 | 平行幂等键体系 |
| 工具失败恢复决策 | recovery_policy/*（GISRecoveryDiagnostic.v1） | GOAL 05（planning/goal-harness-recovery） | 无——正交方向（工具失败处置 vs 会话状态连续性） | 无直接共享 | 不重建恢复策略/诊断词表 |
| GisStateRebuildProvider | context_assembly/providers.py（MapSpec/SessionPlan 少量投影；≤24 层/1600 cap） | G12（W09） | P2 扩展：revision/用户隐藏/锁定/标注 pin/active frame/selection/任务引用/未完成目标/数据版本；紧凑目录+只读详情 | typed assembly scope/budget/receipt | 平行 summary、cap 大幅放大 |
| G14 矢量编辑状态 | #2075（增删/顶点/分割/合并事务、稳定身份、草图升格、拓扑闸） | G14 | 只读消费 typed 状态源 | 待子代理 A 回填 | 重建编辑器 |
| G15 多画幅 frame_product.v1 | #2076 | G15 | active frame/sync group 只读投影 | 待回填 | — |
| G16 标注人工控制（schema v2.1） | #2077（覆写/固定/移位/文字/恢复自动） | G16 | pin/offset/leader 恢复投影 | 待回填 | — |
| G17 导入工作台 | #2079 | G17 | 无（导入不在 G22 验收面） | — | — |
| G20 栅格表达 | #2078 | G20 | 无直接增量 | — | — |
| 数据版本/刷新 | #2022（G10 版本感知/增量更新/采纳回退） | G10 | 数据 ref 有效性投影（A16 幽灵图层） | 待回填 | — |
| ChatEngine 聊天 fallback | #2036 已下线（Pi-only） | #2036 | 禁止恢复 | — | 任何 chat fallback |
| job runner / 完成裁决 / 全局预算 | G23/G28/R1 | 其他方向 | 不接管 | — | — |
| #2061 /config/llm 静默无效 | G12 P1-2/P1-3 修复（turn 准入 recycle_if_config_stale + spawn key 覆盖） | #2081（issue 仍开） | 按最新代码复验 A10/A11 并记录证据；不重复实现 | chat.py turn 准入 | 擅自关 issue |
| J6b 选择器 turn-2 抖动 | master@v1.5.0 干净基线 10 跑 4 败（G12 P2-2 记录） | 存量（根因未明） | P5 查真实根因；若属 R1 owner 提供精确复现+消费侧测试 | J6b/J8 旅程 | 偷偷放松断言 |
| N4 J7 llm_calls 恒 0 | G12 N4（json.dumps 空格 vs node 紧凑输出） | 存量 | P5 修复观测（reviewer 点名） | J7 旅程 | — |
| 会话 fork | PI_RUNTIME_CONTRACT §3 明确不支持（边界文档化） | G01 | 维持不支持；不另建 session 产品 | — | fork 产品入口 |

## 待回填（已回填 2026-11-12）

子代理 A 报告核实的状态源（P2 消费的真实路径）：
- 地图 revision/frames/label authoring/workbench 锁：`mapspec_store_instance.
  get_mapspec(session)`（committed spec dict，含 `frame_product`/`layout.frames`
  /`workbench.lockedLayerIds`）；`app/contracts/workbench_locks.py` 读取契约。
- G16 标注：`layer.label.authoring.overrides`（≤256 条/层，schema v2.1）。
- G15 画幅：`mapspec.frame_product`（active_frame/frames/product_kind/enabled）；
  `frame_product_status(session)` 服务面存在但 provider 走纯 dict 读（无服务调用）。
- undo：`app/services/mapspec/agent_undo.py::undo_points_of`（`_agent_undo_points`
  键，FIFO 有界）。
- 任务：`AnalysisTask` 行 + `ACTIVE_STATUSES`（`app/services/jobs/lifecycle`），
  `AsyncSessionLocal` 查询（facts 装配并行 ≤8）。
- 数据 ref 有效性：`session_data_manager.has(session, ref)`（refId/ref_id 型指针；
  url 型不判定）。
- selection：**无持久权威源**（env/map_state 临时面）——P2 投影不发明（测试钉死）。
- 恢复入口：`--no-session`（vendor 无持久 transcript）+ DB `Conversation/Message`
  transcript + mapspec_store 磁盘原子写（temp+fsync+replace，盘先于 Redis）。
- 前端恢复：`map-state-restore.ts`（图层/相机恢复 + fetch 失败披露）、
  `chat-announcer.tsx`（已恢复历史会话合成行）、`use-sse-stream.ts`（重连
  2×/500ms → Last-Event-ID 重放）。

