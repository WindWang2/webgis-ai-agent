# SOURCE_INVENTORY — situated-agent-workbench-vnext

> 状态标注：implemented / partial / missing / in_other_PR / issue_assigned。
> 证据：file:line @ origin/master `40cda057a`。

## A. 后端（主 agent 亲读 + 后端探索 agent 报告合并）

### 补充关键事实（后端探索报告）

- **`ContextSnapshot` 类不存在**（grep 零命中）；等价物 GISSituation。任务书「ContextSnapshot 兼容投影」= situation 现有投影 + 缺失维（selection scope 验证/分析阶段/结果证据）。
- **`queries.py` 结构化查询零生产调用方**（唯一消费者是测试）——situation 只以 4KiB 文本进 prompt。
- **selection→scope 仅两处消费**：feature_edit（load_selection_view→目标 ids）与 scene3d 相机守卫。**分析工具无 selection 注入**（active_aoi 投影零 app/ 消费者）；分析工具接受显式 ref_id+参数级 bbox/aoi_geometry 由 LLM 填参。
- **IR ↔ turn_journal 无衔接**（唯一共同锚 mutation id）；两套账本并存。
- **agent suggestion 无 IR 通道**（ingest 只放行 actor=user，防伪造；agent 行为进 turn_journal）——四身份缺 agent suggestion 的事件面（由 map_operation receipts 承载 preview 态）。
- **表现层无后端 undo 原语**（map_operations service 头注明示「回退=再提交一次 mutation」）；全栈唯一 undo 原语在 feature_edit 数据层。
- **AOI 几何**：IR 只存 digest；`canvas_affordance.py` 环（16）存**有界几何**但零分析消费者；`[画布意图]` 文本投影。
- **多轮指代现有痕迹**：map_operations resolver（图层名/focused_layer/last_result + clarification ≤6）、followup REF_REUSE_KEYWORDS（legacy 链，Pi 不走）、gis_memory resolved_place 兜底、intent 层回填、悬空指代 consistency 检查。**缺失**：历史多选集无 turn 键控句柄（投影只保留当前唯一 selection）；「上一张图」无 per-generation 可引用实体；「这个区域」→分析 scope 转换不存在。
- **7 条地图状态进后端通道**：turn 起点 map_state、POST map-state（viewport seq CAS）、canvas-actions、cartographic-observation（fingerprint+generation 双门）、map-action-ack、WS 感知、interaction-events。
- **SSE 断线恢复**：Last-Event-ID → TurnResumeRegistry 环重放（进程内）。
- **ownership 模式**：REST `require_owned_session`（404 不泄存在性）；chat body `_guard_body_session`；WS token+归属双检；写共享态「降级锁 fail-closed + 锁丢失复检」。
- **gis_memory 不是 situation 编译事实源**（docstring 预留挂点未接线；关系是 turn_context 尾部追加 `[GIS_MEMORY_PROACTIVE]`）。
- **temporal**：IR 无 kind；situation `active_time_slice` 恒 unknown。
- 断链总图见探索报告 §「数据流总链与断点」七条断点。

| 模块 | 状态 | 关键接口/事实 |
|---|---|---|
| `gis_situation/contract.py` | implemented | 11 分区 GISSituation v1；SitFact{value,status,source,revision,ref}；SituationRevision 四分量（mutation/observation/frontend/interaction）字典序单调 |
| `gis_situation/compiler.py` | implemented | `compile_situation`；`compile_interaction:435`——selected_feature 读 `_cartographic_context_observation`（pre-turn 前端通道）而非 IR ledger；recent_interactions 优先 IR ledger 投影（:497-508）；feature_edits 回执投影（:520-546） |
| `gis_situation/consistency.py` | implemented | 死选中（宿主层移除→stale 降级 reconcile_fact_views）、user_hidden_layer_conflict（user_wins）、observed_behind_desired、verdict_stale_fingerprint 等 7 检查 |
| `gis_situation/queries.py` | implemented | get_selected_feature / resolve_geographic_scope（viewport+region_name+crs+scale）/ query_user_locks / get_pending_mutations / get_active_analysis |
| `gis_situation/turn_context.py` | implemented | build_situation_turn_context（compile→diff→advance→投影 + 记忆切片 + 空间事件块，全 fail-open） |
| `gis_situation/observation.py` | implemented | 轮间交互观察摄入（封闭 kind、512B、client_generation 单调、32 环） |
| `interaction_ir/contracts.py` | implemented | 13 kind 封闭词表；feature_selected 已带 selected_ids(≤8)+dataset_ref；MUTATION_LINKED_KINDS；normalize_payload 白名单 |
| `interaction_ir/ingest.py` | implemented | 纯函数状态机 + verdict 词表（accepted/accepted_with_gap/duplicate/stale_seq/rejected/overflow_dropped） |
| `interaction_ir/projection.py` | implemented | project_interaction_snapshot（viewport/selection/active_aoi/user_overrides/attempted_not_applied/recent_interactions） |
| `interaction_ir/replay.py` | implemented | 同路径回放 |
| `map_operations/`（ADR-0278） | implemented | 组合操作/operation_id 幂等回执环/resolve_effect_target（focused_layer/last_result/clarification 候选 ≤6）/display-ack 新鲜度门/remove_layer 人环 |
| `turn_journal/`（ADR-0268） | implemented | durable turn_events 账本（append-only 投影、advisory resume、compaction） |
| recovery（ADR-0281） | implemented | GISRecoveryReason 27 码 + RecoveryDisposition 6 值 + GoalVerification 对账 |
| feature editing（ADR-0277） | implemented | preview/commit 两相 + PatchSet journal + _wfid + EditProposalCard 人环 |
| **selection→受控分析 scope** | **missing** | 无服务端「选择 → 可执行 analysis scope（ref 所有权+content_revision+要素存活）」验证投影；queries.get_selected_feature 只回快照不验证 |
| **多轮指代解析（selection/AOI/chart 面）** | **partial** | map_operations resolver D7 只解图层名（focused_layer/last_result）；「刚才那组学校/这个区域/图表里最大的区」无证据面 |
| **turn → intent delta → map op delta → observed state 事件链投影** | **partial** | turn_journal 有 map_mutated 行；无面向 agent/用户的一体化可重建链投影 |

## B. 前端（探索 agent 全量报告，2026-11-06）

| 模块 | 状态 | 关键接口/事实 |
|---|---|---|
| `lib/interaction/contract.ts` | implemented | 13 kind 镜像 + ack 契约 + watermarks；`render_observation_seq` reserved 未接线（contract.ts:63-68） |
| `lib/interaction/bus.ts` | implemented | emitViewportSettled/emitFeatureSelected(+selectedIds)/emitFeatureDeselected/emitAoiChanged/emitMeasurementCreated/emitMutationLinked/emitComparisonModeChanged；无会话静默丢弃；隐私最小化 |
| `lib/interaction/outbox.ts` | implemented | POST `/chat/sessions/{sid}/interaction-events`；500ms debounce/批 50/重试 3/队列 256；**队列内存态——刷新丢未发事件**（:34-36）；client_instance_id 复制标签页防串号；会话切换 resetClientSeq（use-workspace-session.ts:221,418） |
| `lib/selection/selection-store.ts` | implemented | 四源（map/chart/table/legend）单例 store（非 Zustand）；publishSelection/clearSelection/getSelectionFilterExpression；MAX_SELECTED_IDS=50；epoch 守卫；**明确不进 MapSpec/不进 LLM context（ADR-0059）** |
| `lib/selection/brush-select.ts` | implemented | projectBrushHits（≤50 ids + truncated→bbox predicate 兜底）；BRUSH_ID_KEYS 链 |
| `lib/selection/viewport-context.ts` | implemented | 视口非选择；指纹去重 + epoch 取消 |
| `components/map/map-panel.tsx` | implemented | 点选/框选（brush_select 工具）→ publishSelection + emitFeatureSelected；框选命中多数图层族（:1390-1402）；程序性相机不伪造 viewport 事件（:1509-1518）；PR #1965 占用中——只读 |
| 图表类别选择 | **partial（无上行）** | chart-panel.tsx:252-271 publishSelection('select',source:'chart')——**不 emit IR**；chart-core 仅 onClick 无拖刷 |
| 表格行选择 | **partial（无上行）** | table-panel.tsx:194-207 publishSelection('select',source:'table')——**不 emit IR**；map→table 滚动定位（无 flyTo） |
| 图例分级过滤 | **partial（无上行）** | map-panel 本地 activeFilters（:309,413-415）纯前端 |
| temporal 时间轴 | implemented（无 IR） | commitUserTemporal → set_temporal_view intent（user_pinned 后 Agent 不可覆盖）；**无 IR kind** |
| 刷新/重连选择恢复 | **missing** | selection-store/viewport-context 内存态；HUD persist 刻意不含 layers/selectedFeature（useHudStore.ts:126-161）；map-state-restore.ts:385 只还原图层/相机/workbench doc |
| `lib/workbench/undo.ts` + `use-undo.ts` | implemented | 命令模型 undo/redo（50 条，user/agent，反向重放 CAS 通道）；会话切换清栈；Ctrl/Cmd+Z |
| `components/workbench/undo-history-panel.tsx` | implemented | timeline/layers 双 tab + opsLog journal + 「回退到此处」+ 焦点围栏 |
| `components/agent/agent-run-panel.tsx` | implemented | aiStatus 阶段 + 当前工具 + analysis-graph 投影 + 产物 + opsLog 近 8 条 |
| MapHarnessCards | implemented | map_operation_receipt 回执卡（committed/preview/partial/superseded/rejected）+ 显示确认卡 |
| render-apply-ack | implemented | render_apply_ack.v1（applied/partial/failed；user-wins 弃权） |
| 双写面 | partial | selectedFeature（HUD）与 selection-store 并存——点选链路两个都写（use-feature-selection.ts:60-121） |
| e2e journeys | implemented×9 | j1–j9；**零选择/框选/图表联动覆盖**（grep brush|selection 零命中）——新 journey 空白区 |
| V4 tokens/主题/a11y | implemented | globals.css V4 语义 token + darkMode class 契约锁；chat-announcer aria-live 范例；use-dialog-focus 范例；no-raw-cjk i18n 门 |

## C. 事件去向矩阵（12 组操作追踪的核心事实）

| 交互 | 进 IR？ | 进后端？ | 通道 |
|---|---|---|---|
| 地图点选/框选 | ✅ feature_selected（含 selectedIds） | ✅ | IR outbox + 每轮 map_state.selected_feature 快照（use-sse-stream.ts:1173-1175） |
| 图层显隐/透明度/排序/删除/样式 | ✅ mutation-linked kinds | ✅ | mapspec/mutations CAS + IR 事件（结局穿透） |
| 视口 settle（仅用户手势） | ✅ viewport_settled | ✅ | IR + 每轮 POST map-state |
| 图表类别选择 | ❌ | ❌ | 前端 selection-store only |
| 表格行选择 | ❌ | 仅随下轮聊天 | 前端 selection-store only |
| 图例分级过滤 | ❌ | ❌ | map-panel 本地 state |
| temporal 区间 | ❌（有 set_temporal_view intent） | ✅（spec 面） | commitUserTemporal |
| 撤销/重做 | ❌（走 mutation 通道本身） | ✅ | 反向重放 CAS |
