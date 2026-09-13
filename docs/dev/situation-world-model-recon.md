# GIS Situation / World Model — Recon（Phase 0）

- 日期：2026-09-13
- 基线：`origin/master @ 580b33e9`（PR #1272 ads-v1 合并后）
- 分支：`harness/gis-situation-world-model-v1`（独立 worktree `webgis-situation-wt`）
- 方法：全量 fetch + PR/issues 对账 + 生产代码深读（非 grep 标题）。

## 1. 执行时仓库事实

| 项 | 事实 |
| --- | --- |
| origin/master | `580b33e9` Merge PR #1272 (adaptive-data-supply/v1-master) |
| 最近 merged | #1272 ads-v1 M1-M5 (ADR-0170~0179)、#1271 ac-v11 (ADR-0160~0169)、#1269 quality baseline (ADR-0159)、#1262-1268 AC 波次 |
| Open PR | 仅 #1270 `fix/ci-adaptive-hygiene`（CI/hygiene：`mapspec/coordinator.py`、`frontend/lib/mapspec-compiler/compiler.ts`、quality generated artifacts、`tests/conftest.py`）— 与本任务文件面**零交集**；本任务不吞入 |
| ADR 编号占用 | 已用至 **0179**；本任务使用 **0180**（已验证 0180 未被任何分支/文档占用） |
| Subagent A 勘察 | 因账户速率限制失败（1/2 配额已耗）；全部勘察由主 Agent 完成 |

## 2. 生产调用链 before（Pi 默认路径，USE_NEW_AGENT=True）

```
POST /api/chat {message, map_state(前端内嵌快照)}
  chat.py
  ├─ _use_pi_bridge() ──false──▶ Legacy: ChatContextAssembler.assemble（见下）
  ├─ _record_frontend_cartographic_observation(sid, req.map_state)
  │     写 map_state["_cartographic_context_observation"]
  │     {sequence↑, layers(bounded), viewport, base_layer, selected_feature,
  │      user_location, focus_layer_id, is_3d}                      [pre-turn 快照]
  ├─ environment_context = _build_environment_turn_context(req.map_state)
  │     [环境感知] 文本块：viewport/bounds/底图/用户位置/选中要素/聚焦图层/活跃图层(前8)
  │     ← 纯前端快照，零 store IO，无 source/freshness/revision 语义
  ├─ cartography_context = _build_cartography_turn_context(sid)
  │     [CARTOGRAPHY_VERDICT](should_inject_verdict 指纹门) + [CARTOGRAPHY_MEMORY](ADR-0069)
  └─ bridge.stream_prompt(message, cartography_context, env_block=environment_context)
        agent_pi_bridge.prompt/stream_prompt
        └─ pi_turn_context.bind_turn_prompt：
            message + cartography + plan(SessionPlan projection + [GIS Plan Progress])
            + env_block + surface_block + active_tools + evicted_refs
            + v6_blocks(node_local + workflow_global + map_situation)
            + [WEBGIS_TURN_CONTEXT:token]  ← marker 必须最后
        Pi subprocess LLM loop → webgis_execute → /pi-tools/execute → ToolRegistry
```

Legacy 路径（回退）`ChatContextAssembler.assemble`：
system+`[环境感知]`(build_map_state_summary: store map_state+list_refs+event_log)
→ `[执行计划]` → `[CARTOGRAPHY_VERDICT]` → `[CARTOGRAPHY_MEMORY]` → V6 三块
→ `[最近对话上下文]` → 预算截断历史。**本任务不改 legacy 组装（回退路径保持原样）**。

## 3. 事实生产者/消费者 inventory（S0）

### 写方（事实源）

| # | 事实 | 权威写方 | 存储位置 | revision/新鲜度 |
|---|---|---|---|---|
| W1 | viewport(用户拖拽) | WS `viewport_change`→`ws_service.handle_viewport_change`；REST `POST /sessions/{id}/map-state`(+seq CAS) | `map_state["viewport"]` | 无 observed_at |
| W2 | base_layer | WS `base_layer_changed`；REST map-state | `map_state["base_layer"]` | 无 |
| W3 | is_3d / layer_order | WS `mode_changed`/`layers_reordered` | `map_state["is_3d"|"layer_order"]` | 无 |
| W4 | layer visible/opacity(用户) | WS `layer_toggled/opacity` → `update_layer_in_state` | `map_state["layers"]`(前端活跃层镜像) | 无 |
| W5 | pre-turn 快照（选中要素/位置/聚焦/3D/layers） | chat 请求内嵌 → `_record_frontend_cartographic_observation` | `map_state["_cartographic_context_observation"]` | sequence 单调↑ |
| W6 | runtime 渲染观察 | `POST /sessions/{id}/cartographic-observation`（事件驱动） | `map_state["_cartographic_observation"]` | sequence↑ + fingerprint 门 + 服务端 revision 盖章 + client_generation 单调 |
| W7 | desired MapSpec（layers/sources/layout/view） | `MapSpecLifecycleEngine`（agent mutation 唯一权威）经 `apply_gis_mutation` 门面 | 磁盘 mapspec.json + revisions/ + Redis | `_cartographic_mutation_revision` CAS（#1073 磁盘 sidecar 复活） |
| W8 | mutation 决策环 | `apply_gis_mutation` → provenance | `map_state["_gis_provenance"]`（环 64） | seq 单调，origin=user/agent/system |
| W9 | verdict/review | `evaluate_cartographic_session`（观察/变异后） | `map_state["_cartographic_review"]` | mapspec_fingerprint 门 |
| W10 | SessionPlan 信封 | `session_plan.py`（user_goal/gis_chapter/progress） | session store alias `session-plan` | updated_at/envelope_id，supersede 归档 |
| W11 | 数据 ref 清单+descriptor | `session_data_manager.store/list_refs/get_ref_descriptor` | ref store（LRU+spill） | ref_id；descriptor 惰性 |
| W12 | 工具事件/用户操作 | `append_event`（chat engine/WS） | `event_log` | 顺序 |
| W13 | 采集事实 | ads-v1 `data_fabric/facts.py` AcquisitionFact | 进程内+alembic 0071 | wave 聚合 |
| W14 | 项目先验 | ADR-0069 project memory | DB（Postgres） | active/stale/conflicted |

### 读方（消费者）

| 消费者 | 读什么 |
|---|---|
| Pi turn env block `_build_environment_turn_context` | **req.map_state 内嵌快照**（非 store） |
| legacy `build_map_state_summary` | store `map_state`（viewport/base_layer/selected_feature/focus_layer_id/user_location/layers）+ list_refs + event_log |
| V6 `build_map_situation_block` | map_state revision/_cartographic_review + mapspec + chapter |
| `gis_world_state.build_world_state` | map_state（单次全量）+ mapspec fallback + provenance + observation —— **pull 型工具 `webgis_world_state`，不进 per-turn context** |
| `observation_states.build_observation_summary` | `_cartographic_observation`（阶梯 unknown→…→semantically_correct） |
| SessionPlan 投影/进度 | plan 信封 + mapspec |
| verdict 注入门 | `_cartographic_review` + `cartographic_fingerprint(mapspec)` |

## 4. 重复/失联 source of truth（关键发现）

| # | 事实 | 副本位置 | 问题 |
|---|---|---|---|
| D1 | **viewport** | `map_state["viewport"]`(WS/REST 写) vs req.map_state.viewport(chat 快照) vs `_cartographic_context_observation.viewport` vs `_cartographic_observation.viewport` vs `mapspec.view`(agent framed) | 5 处；env block 用 req 快照，GISWorldState 用 mapspec.view+fallback map_state.current_view；无新鲜度/来源标注 |
| D2 | **选中要素/聚焦/位置/3D** | 顶层 `map_state["selected_feature"|"focus_layer_id"|"user_location"]` | **#811 后无写方**（WS 白名单已剔除）——legacy `build_map_state_summary` 读的是**死键**；活通道只有 `_cartographic_context_observation`（Pi 路径消费）。legacy 路径选中要素行恒空 = 陈旧读失联 |
| D3 | **layers** | mapspec.layers(desired 权威) vs map_state.layers(前端活跃镜像) vs `_cartographic_context_observation.layers` vs `_cartographic_observation.layers`(证据) | 4 处，消费方各取所需；地图语义（desired）与渲染语义（observed）无统一对账出口 |
| D4 | **revision** | `_cartographic_mutation_revision`(权威 CAS) vs mapspec 内 revision 字段(回退投影) vs observation.mapspec_revision(盖章投影) vs client_generation | 投影无单调性保证；situation 必须以 mutation_revision 为准 |
| D5 | **数据集事实** | list_refs(ref→alias) vs mapspec.sources(ref/profile) vs ref descriptor vs data_fabric AcquisitionFact | “active datasets by role” 无单一查询口；env block 的 format_layer_lines 双源兜底（inventory 优先，前端 layers 兜底） |

## 5. 与最近 PR 的重叠矩阵

| 能力 | #1271/#1262-1269 (AC-V11) | #1272 (ads-v1) | #1270 (open) | 本任务处置 |
|---|---|---|---|---|
| 自适应意图/语义槽位 (ADR-0150) | ✅ 已建 | — | — | **复用**（UserGoalContext 引用语义槽位事实，不重做） |
| recipe adjudication/fallback (ADR-0151/0157) | ✅ | ✅ DS4 | — | 复用，Situation 只投影 verdict/fallback 状态 |
| 数据源注册/语义检索 (ADR-0171/0172) | — | ✅ | — | **复用** DataContext 的 descriptor-first 事实源 |
| 采集事实/观测 (ADR-0178) | — | ✅ facts.py | — | 复用为 freshness 事实（不重做 fact store） |
| 渲染观察阶梯 (ADR-0119 D9) | — | — | — | **复用** `observation_states`，Situation 的 map/render 事实从阶梯派生 |
| verdict 注入门 | 既有 quality_loop | — | — | 复用 `should_inject_verdict`，Situation 不重建 verdict 语义 |
| `build_map_situation_block`（V6 W13） | 已合并 | — | — | **扩展关系**：3 行摘要块保留；Situation 投影是底层结构化事实层，不并列第二文本通道（见决策 DC-3） |
| `build_world_state` 快照 (C2/ADR-0072) | 已合并 | — | — | **复用**：pull 型读模型保留；Situation 是 per-turn push 编译（互补不重叠） |
| MapSpec CLI hygiene | — | — | #1270 改 compiler.ts/coordinator.py | 无交集；若 quality generated artifacts 冲突按 #1270 归因 |

## 6. 复用 / 扩展 / 不做 清单

**复用（直接调用，不改语义）**：`apply_gis_mutation`/provenance、`build_world_state`、`observation_states` 阶梯、`should_inject_verdict`/`render_verdict_for_llm`、`cartographic_fingerprint`、`load_session_plan`/`format_session_plan_projection`、`session_data_manager`（get_map_state 单次读 + get_state_field 定向读）、`_cartographic_context_observation`/`_cartographic_observation` 通道、`_cap_text`/字节预算纪律、`_xml_fence` 转义纪律、`viewport_naming`。

**扩展**：`bind_turn_prompt` 的 env_block 注入位（chat.py 两处构造点）→ 由 Situation 投影接管（flag+fallback）；`_cartographic_context_observation` 通道 → 增加交互环存储 `_situation_interactions`（S4）；`ChatContextAssembler`/legacy 路径不改。

**新建**：`app/services/gis_situation/`（facts/contract/compiler/diff/observation/projection/queries/consistency/inspector），ADR-0180，S0-S9 测试。

**不做**：持久化 Spatial Memory（方向 9）、mutation 完整事务引擎（方向 8）、Data Fabric descriptor 重做、Pi history memory 重写、通用 agent loop、legacy ChatEngine 路径接管、#1270 hygiene。

## 7. 任务书假设 → 实际代码 → 调整

| 任务书假设 | 实际代码 | 调整 |
|---|---|---|
| "map_state 是字典" | 分散多键 + `_cartographic_*` 内嵌对象 + CAS revision | compiler 单次 `get_map_state` + 定向 `get_state_field`，绝不多次全量读（遵循 #1068(E-5)/v2(audit P1) 纪律） |
| "前端 observation 需新建通道" | pre-turn 快照 + runtime observation 两通道已存在且有门 | S4 只补**轮间交互环**（debounce/dedupe/单调），不动既有两通道 |
| "ChatContextAssembler 为 Pi 提供上下文" | assembler 是 legacy 专属；Pi 走 bind_turn_prompt | 接线点改为 chat.py 的 env_block 构造（Pi 两分支） |
| `pi-host-seams.md` 说 SessionPlan "Missing" | `session_plan.py` 已落地（ADR-0076） | 以代码为准；recon 据此更新（文档过时项不再引用） |
