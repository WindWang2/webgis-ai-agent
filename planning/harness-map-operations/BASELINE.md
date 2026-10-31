# BASELINE — 情境化地图操作事务与可验证终验（Track A）

基线：origin/master `4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（2026-10-31 勘察）
勘察方式：3 个只读 explorer 全量调用图 + 主 agent 亲读关键文件（display_confirmation.py 全文、mutation.py facade、finalize_display、pipeline 披露面、map-action-ack 路由、IR projection、catalogue 契约测试、widget-host、map_finalization 流）。

## A. 生产动作现状矩阵（M0，24 项）

| # | 用户动作 | 现状 | 证据（file:line，均相对仓库根） |
|---|---|---|---|
| 1 | 缩放定位（agent） | true | `app/tools/map_view.py:89-327` fly_to_location/zoom_to_bbox/zoom_to_layer/reset_map_view/set_map_view；SetViewIntent registry `app/services/mapspec/mutation_registry.py:587-591` |
| 2 | 历史视图（undo/redo） | true | `frontend/lib/workbench/undo.ts:30-43`（命令模式反向重放 CAS；有界 50） |
| 3 | 图层 show/hide | true | 用户：`frontend/lib/mapspec/user-mutation.ts:391-422`→patch_layer_presentation；agent：`app/tools/layer_manager.py:425-463`；批量 CAS：`app/services/gis_world_state/mutation.py:551`（仅 presentation） |
| 4 | 图层 isolate | partial | finalize_display（show 集 + 隐藏其余）`app/tools/layer_manager.py:97-305`；无通用 isolate 单动作 |
| 5 | 图层排序 | true | 用户拖拽 `user-mutation.ts:746`；agent `layer_manager.py:524-573` REORDER_LAYER + 前端 durable 回写 `layerCommands.ts:991-1015` |
| 6 | 透明度 | true | `user-mutation.ts:821`；agent `layer_manager.py:425-463`（同 LAYER_VISIBILITY_UPDATE） |
| 7 | 字段过滤 | partial | agent `layer_manager.py:612-651` APPLY_LAYER_FILTER（MapLibre 表达式）；MapSpec 编译器不输出 filter（issue #1809 已立案，不在本任务修） |
| 8 | 图层样式 | true | patch_layer_style `user-mutation.ts:301`；agent `update_layer_appearance` `layer_manager.py:465-522` |
| 9 | 标签开关 | absent | 无专属 intent/入口（label 是层族变体 `gis_world_state/reconciliation.py:34-42`） |
| 10 | legend/annotation 放置 | partial | SetLayoutIntent `intents.py:211-224` 后端产线有写入；前端无直发；组件 patch_component `component-mutation.ts:73` true |
| 11 | 图表面板状态 | true | chartCommands ×7 全走 patch_component CAS `frontend/lib/map-commands/chartCommands.ts:3-14,86-237` |
| 12 | 用户手势优先级 | true | 相机仲裁 `frontend/lib/map-commands/camera-arbitration.ts`；viewport_settled 仅真实手势 `map-panel.tsx:1458-1474`；user-pinned `visibility-transaction.ts:240-257` |
| 13 | 切换会话 | true | IR per-session ledger + resetClientSeq `use-workspace-session.ts:222,419`；undo 清栈 `undo.ts:187-192` |
| 14 | 前端重载恢复 | partial | MapSpec durable；IR durable（persist_ledger_state）；渲染观察重新采集；**agent 操作回执无持久面**（本任务补） |
| 15 | 重新打开项目 | partial | mapspec/session durable；operation 历史无 |
| 16 | 观测超时 | true | waitForRenderSettle 400ms 超时 `render-observation.ts:45,246-272`；observation ladder 缺字段按弱态 |
| 17 | RenderWork 落后 | true | stale 语义三道门（client_generation/fingerprint/revision 盖章 `chat.py:1810-1864`、`render_apply_ack.py:171-173`） |
| 18 | 离线返回 | true | outbox 水印裁剪 + keepalive flush `frontend/lib/interaction/outbox.ts:276-293,430-444` |
| 19 | 用户手势并发抢占 agent 相机 | true | superseded_by_user `viewCommands.ts:86-97,131-139` |
| 20 | agent 覆盖用户显隐决策 | true（被拒） | user-presentation guard ring `mutation.py:417-445` + W15 锁守卫 `lifecycle_engine.py:447-458` |
| 21 | 多动作一次提交（表现层原子包） | partial | apply_gis_mutation_batch 仅 PatchLayerPresentationIntent（`mutation.py:578-583` TypeError） |
| 22 | 操作前预览（before/after diff） | partial | GISActionIR dry-run（`app/lib/gis/action_ir.py`，工具 webgis_action_plan 仅 dry-run）；visual-repairs plan 卡有 ops diff；**常规地图操作无 before/after 预览** |
| 23 | 最终显示确认（human seam） | partial | 读面 is_display_confirmed 3 消费者（`completion/pipeline.py:1490-1500` 等）；**record_display_ack 零路由零前端**（`display_confirmation.py:15-17` 明言 seam 待接） |
| 24 | required 模式 pending → 用户行动 | absent | 无 typed pending 披露/无确认卡（F-A3 不可达） |

## B. 核心断缝（本任务增量面）

1. **S1（M7）**：`GIS_FINAL_DISPLAY_CONFIRM=required` 永远无法 confirmed——ack 写半边无路由；`render_observation_seq` 已在 stored map_product（代次钥匙在场）。
2. **S2（M1/M4）**：多效果表现层修改无统一 Request/Preview/Receipt——agent 只能逐工具调用（N 次独立 dispatch、N 个 step_result、无统一回执/幂等键）；混合 intent 无原子包（batch 仅 presentation）。
3. **S3（M3）**：指代解析无正式通道——resolve_layer_ref 只做 alias/id/active/name 子串（`layer_manager.py:39-91`），无「这个图层/上一个结果/刚才放大的区域/保留其余」的情境绑定与歧义候选产出。
4. **S4（M8）**：required-pending 无 typed needs_user_action 披露（display_confirmed 只是个布尔）。
5. **S5（M9）**：跨刷新的 agent 操作事实持久面缺失（receipt 环）。

## C. 已明确不重造（前置拒重确认）

- #1868 data_preparation、#1869 analysis_workflows、#1870 design_candidates/semantic_checks、#1871 atlas_batch、#1872 intent_semantic.py、#1873 feature_edit/* —— 本任务零触碰其核心目录。
- GISActionIR/executor（补偿链未接线——本任务不接，receipt 采用「局部成功披露」策略，见 PLAN 决策 D3）。
- InteractionIR/GIS Situation/MapSpec lifecycle/RenderWork/observation/finalizer/continuation/runtime_state_machine 全部复用只读或经公共 API 调用。

## D. 关键机制备忘（设计输入）

- 命令契约：后端 `"command": "x"` 字面量必须在前端 COMMAND_CATALOGUE（`catalogue-contract.test.ts` 正则扫描 `../app`）。
- step_result 双通道：`data.mapspec ?? data.result.mapspec` → commitMapSpecDocument（use-sse-stream.ts:543-558）+ `command`/`commands` → dispatchAction（useMapBridge.ts:462-535）。finalize_display 返回形态（command+params+mapspec_fingerprint+mutation_revision+mapspec）是先例。
- 用户 mutation 必带 expected_revision（`lifecycle_engine.py:364-372`）；agent 可缺省。mutation_id 幂等在 CAS 之前（`lifecycle_engine.py:342-363`）。
- finalizer 重跑入口：`maybe_finalize_map_product(session_id, reason=, force=, final_gate=)`（`completion/pipeline.py:1040`）。
- 会话归属：`require_owned_session`（`auth_history_bridge.py:57-73`）= 登录 user_id 或匿名 owner_token 匹配，不属主 404。org/project 维度 absent（记忆面高危 issue #1841-1843 属他人在修，本任务新路由一律 owned-session，不信任客户端身份字段）。
- 渲染观察代次：stored map_product 的 `render_observation_seq`；当前最新 = `observation_sequence()`（`render_observation.py:291`）。
