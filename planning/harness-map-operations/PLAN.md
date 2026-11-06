# PLAN — Track A：情境化地图操作事务与可验证终验

分支：zcode/gis-harness-map-operations-vnext（基线 origin/master 4b3629c90）

## 最终用户价值（验收锚点）

「隐藏热力图、学校点放大一点、缩放到武侯区、图例别动」→ 一次受控操作（预览→提交→单回执）→ MapSpec revision 前进 → 真实渲染观察 → `GIS_FINAL_DISPLAY_CONFIRM=required` 时用户显式确认才算 display_confirmed → 多轮会话/刷新后操作事实不丢、用户手动决策永不被 agent 静默覆盖。

## 架构决策

- **D1 复用而非平行**：不新建 MapStore/第二状态仓库。MapOperation 服务是对既有 mutation facade（`apply_gis_mutation`/`apply_gis_mutation_batch`）的组合层：presentation 组走 batch（单事务单 revision），其余 intent 顺序走 facade，每步 CAS 链（后一步用前一步返回的 revision）。不虚构原子性——receipt 如实披露 per-effect 状态。
- **D2 幂等**：operation_id（agent/用户侧 UUID）为操作级幂等键；receipt 持久于 map_state `_map_operation_receipts`（有界环 16）。重放同 operation_id 返回存证 receipt。底层 mutation_id = `op:<operation_id>:<effect_idx>`（引擎级 dedup 兜底）。
- **D3 局部成功策略**：失败/被抢占不自动补偿（表现层效果彼此独立、补偿写会与 user-wins 竞争）；receipt 记 applied/skipped/failed/superseded + reason_code，交由下一轮（agent 或用户）决策。GISAction executor 补偿链保持未接线（非本任务范围）。
- **D4 敏感操作走人环**：低风险效果（显隐/透明度/样式/排序/视口/组件位置）agent 工具可直接 commit；敏感效果（remove_layer、layout 整体替换）工具只产 preview + confirmation_required，由 owner 经 `POST /map-operations/{operation_id}/commit` 提交（镜像 visual-repairs plan→approve 先例）。
- **D5 display-ack 新鲜度门**：POST ack 的 render_seq 必须 == 当前最新 observation sequence 且 ≥ stored verdict 的 render_observation_seq；旧观察 ack 拒绝（typed stale）。ack 后 force finalizer 重跑，task_complete 披露刷新。
- **D6 前端零冲突布线**：receipt 经 `commands:[{command:'map_operation_receipt'}]`（catalogue 新条目+新 slice 文件）；显示确认卡自水合（GET on mount + session-cursor revision 订阅 debounce），不触碰 use-sse-stream/adapters/message-reducer（#1873 占用）。挂载点 map-panel 一行加法。
- **D7 指代解析（M3）**：resolver 输入 = MapSpec 层清单 + IR 投影（selection/user_overrides/recent_interactions）+ situation focus_layer_id；支持 target_kind: layer_ref（alias/id/name）、focused_layer、last_result、viewport 区域复用（zoom_to 最近 AOI/bounds）；「保留其余」= 仅列出的 target 被改，receipt 披露 untouched 层数。歧义（多候选且无 focus）→ clarification 候选列表，不写任何状态。

## 交付物（文件级）

后端（新文件为主）：
1. `app/services/gis_harness/map_operations/{__init__,contracts,resolver,service}.py`
2. `app/tools/map_operations_tools.py` — `webgis_map_operation`（mode: preview|commit）
3. `app/api/routes/{map_operations,display_confirmation}.py` + `app/schemas/{map_operation_schema,display_confirmation_schema}.py`
4. 改动（加法）：`app/main.py`×2 行、`app/tools/__init__.py`×1、`app/tools/categories.py` 名字覆盖、`app/services/gis_harness/completion/pipeline.py` display_confirmation 披露块
5. 测试：`tests/unit/gis_harness/map_operations/`（contracts/resolver/preview/commit/routes/tools）+ `tests/unit/gis_harness/test_display_confirmation_routes.py` + 旗舰 e2e `tests/unit/gis_harness/map_operations/test_flagship_fa1_fa5.py`

前端：
6. `frontend/lib/map-operations/{receipt-bus.ts,api.ts}` + `frontend/lib/map-commands/mapOperationsCommands.ts`
7. `frontend/components/map/map-harness-cards.tsx`（receipt 卡 + display 确认卡复合宿主）
8. 改动（加法）：`catalogue.ts`×1、`map-panel.tsx`×1 行、`messages/{zh-CN,en-US}/map.json` 新键
9. 测试：`receipt-bus.test.ts`、`mapOperationsCommands.test.ts`、`map-harness-cards.test.tsx`

文档：`docs/adr/0278-map-operation-transactions.md`、endpoint-scope-matrix 再生、openapi 快照再生（若脚本可用）。

## 里程碑对照

M0=BASELINE ✓ → M1=contracts → M2/M3=resolver+tool → M4=service+route → M5=双向同步验证（既有 M5 机制只做测试加固，不重写）→ M6=前端卡 → M7=display-ack 路由 → M8=typed pending 披露 → M9=receipt 环恢复测试 → M10=安全负例 → M11=性能快照 → M12=兼容回归 → M13=测试全量 → M14=质量门 → M15=独立评审+PR。

## 退出条件

ruff/compileall/pytest（新测试全绿+受影响旧测试不红）/typecheck/lint/vitest 全过；F-A1..F-A5 有自动化证据；INDEPENDENT_REVIEW P0/P1 清零；PR 已创建（不 merge）。
