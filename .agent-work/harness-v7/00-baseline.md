# Harness V7 Baseline（只读审计 @ worktree feat/harness-v7-agentic-runtime，HEAD 2aabdc43）

日期：2026-09-10。方法：全部断言带 file:line 证据；测试/命令均为本机（Windows Git Bash + Anaconda Python 3.13.9）实测。
V6 权威事实来源：`.agent-work/harness-v6/01-architecture.md`、`06-pr-summary.md`（ADR-0119 已落地 D1-D10）。

---

## Section 1: 执行链路图（生产 Pi 路径为主链）

**主链（Pi bridge 路径，`USE_NEW_AGENT=true` 为默认，app/core/config.py:202-203）：**

1. `POST /chat/completions`（非流式）→ `chat_completions`（app/api/routes/chat.py:760）；流式 `POST /chat/stream` → `chat_stream`（chat.py:903）。
2. 会话守卫 `_guard_body_session`（chat.py:692）→ `_ensure_pi_bridge_available`（chat.py:535）→ 池化 bridge 选择 `_pi_turn_bridge`（chat.py:491；`get_bridge_pool().bridge_for_session`）。
3. 回合开场观察写入 `_record_frontend_cartographic_observation`（chat.py:272，map_state 键 `_cartographic_context_observation`）；制图上下文块 `_build_cartography_turn_context`（chat.py:416）。
4. `bridge.prompt(...)`（app/agent_pi_bridge.py:1680）——turn_id 租约 `_acquire_turn_lease`、`bind_turn_prompt/issue_turn_token`（app/services/chat/pi_turn_context.py）、事件 drain。
5. Pi 子进程（vendor/pi, rpc-mode）回调 `POST /pi/execute`（app/api/routes/pi_tools.py:51）→ turn token 校验 + live-turn 检查（pi_tools.py:63-89）→ `dispatch_tool`（agent_pi_bridge.py:428）→ 共享 `ToolDispatchService`（agent_pi_bridge.py:216/229-235）。
6. **动态工具面**：prompt 组装时 `_active_tools_block_for`（app/services/chat/pi_turn_context.py:317-348）→ `compute_turn_active_tools`（app/services/chat/pi_native_surface.py:414）→ `DynamicToolSurface.select`（app/services/chat/tool_surface_v3.py:301，select 体 :461 起；V6 hybrid 段 :591 起；置信度 :717-747）→ hybrid 信号 `hybrid_signals`（app/services/chat/semantic_retrieval.py:679）+ `compute_confidence`（semantic_retrieval.py:743）。SessionPlan→ToolSurface 投影 `_compile_surface`（pi_turn_context.py:288-301）。
7. **调度**：`ToolDispatchService.dispatch`（app/services/tool_dispatch_service.py:329）：
   - 链事件 `emit_chain_once(Stage.TOOL_CALLS/ARGUMENTS)`（tool_dispatch_service.py:356-368）；
   - 去重 `_dedup_lock` + `executed_tools` + `_completed_keys`（:375-402）；
   - 分析复用 `GIS_ANALYSIS_REUSE` 门（:413）→ `app/lib/gis/analysis_reuse.compute_analysis_key/find_reusable_artifact`（:415-449）；
   - wave 并发闸 `_session_wave_gate`/`_wave_semaphore`（:489-497）→ `registry.dispatch`（:495）；
   - 失败归一 `is_error_like_result`（:531-544）→ **V5 typed 诊断** `classify_and_remediate`（:558-569）→ append_event `tool_failed`（:573-577）；
   - 成功回写 durable 账本 `get_recovery_ledger().record_success`（:918-922）；
   - 事件回写 `session_data_manager.append_event(session_id,"tool_executed",...)`（:1444-1474）。
8. **规划面（host-plan）**：SessionPlan 载体 `SessionPlan`（app/services/session_plan.py:67；`gis_chapter`:73；`progress: list[CapabilityProgress]`:74，状态词表 :56）+ `goal_key`（:96）。LLM 规划意图解析 → `resolve_map_request_intent`（app/services/gis_harness/intent.py:660）→ 确定性规划器 `MapProductPlanner.plan_from_intent`（app/services/gis_harness/planner.py:405/516，纯函数零 LLM，memo :427-429，runtime 单例 `get_planner_runtime` app/services/gis_harness/planner_runtime.py:39）。Pi 路径的 plan 块/合成由 `AgentPlanOrchestrator`（app/services/chat/plan_orchestrator.py:347；`orchestrate_plan`:833；harness 合成 `_synth_plan_from_harness`:534；V4 证据编译 `_compile_v4_evidence`:653）。
9. **观察→终验→修复闭环**：前端观察 `POST /chat/sessions/{sid}/cartographic-observation`（chat.py:1498）→ `maybe_finalize_map_product(reason="render_observation")`（chat.py:1690 → completion/pipeline.py:508）→ 必要时 `run_runtime_repair`（chat.py:1754 → app/services/gis_harness/runtime_repair.py:393，V6 continuation 裁决挂接 `_attach_continuation` runtime_repair.py:358-390）。
10. **回合收口终验**：非流式 settle 后 `maybe_finalize_map_product(final_gate=True)`（chat.py:810-817）；流式在 `agent_settled` 同钩子（chat.py:1017 注释/同函数）。终验本体 `run_map_finalization`（completion/pipeline.py:115-344：有界 passes 循环 :169-197、渲染级校验 :208-224、V3 final map :251-267、状态定格 :272-309、裁决聚合 :311-334、链事件 :338）。
11. **响应/持久化**：`map_product` 块在 session 锁内写回 `gis_chapter`（pipeline.py:631-701）；SSE `task_complete` 载荷 `finalization_sse_payload`（pipeline.py:792）；transcript 持久化 `AsyncHistoryService.save_message`（chat.py:826-836）；项目记忆收割 `harvest_project_memory`（chat.py:851 → app/services/cartography/memory_harvest.py:127）。
12. **Legacy 回退链**（`_use_pi_bridge()` False，chat.py:510-532）：`ChatEngine.chat`（app/services/chat/execution_engine.py:1257）→ `orchestrate_plan`（execution_engine.py:1107 → plan_orchestrator.py:833）→ LLM 轮次循环 → `ToolDispatchService`（execution_engine.py:55 import）→ `SubagentDispatcher` 子代理路径（execution_engine.py:356-361；app/tools/subagent.py:162/175 为 LLM 工具入口）。

---

## Section 2: 状态与持久化清单（谁拥有什么）

| 状态 | 位置 | 写者 | 读者 |
|---|---|---|---|
| SessionPlan（envelope + gis_chapter + progress） | session store（内存/Redis 二实现，`session_data_manager`；`store(prefix="sessionplan")`，app/services/session_plan.py:318/341/353） | plan_orchestrator、finalizer（仅 `gis_chapter["map_product"]` 单键，pipeline.py:531-533+688） | pi_turn_context、completion、resume_anchor、`/sessions/{sid}/plan` 路由 |
| MapSpec（期望地图态）+ revision | `mapspec_store`（app/services/mapspec_store.py:93；`_cartographic_mutation_revision` 读于 pipeline.py:556） | mapspec lifecycle / 前端 ack | finalizer、runtime_repair、composer |
| RenderObservation（渲染证据，观察非真相） | map_state 键 + 观察账本；`load_render_observation`（app/services/gis_harness/render_observation.py:264）、代次 `observation_sequence`:291、`observation_revision`:301 | 观察端点（chat.py:1498） | finalizer（pipeline.py:559/669）、runtime_repair、observation_states |
| `_recovery_state`（live durable，≤2KB） | map_state 键（app/services/gis_harness/durable_context.py:34/92/108/143） | `update_recovery_state`（turn 边界/回路事件；runtime_repair.py:376-382） | continuation、resume_anchor.build_anchor（resume_anchor.py:87-95） |
| WorkflowResumeAnchor（跨 session 恢复指针） | DB 表 `workflow_resume_anchors`（app/models/project.py:203-226；anchor JSON） | `save_anchor`（resume_anchor.py:175）+ 路由 `POST /chat/sessions/{sid}/workflow-resume-anchor`（app/api/routes/workflow_resume.py:28） | `resume_from_anchor`（resume_anchor.py:219）+ `POST /chat/workflow-resume/{anchor_id}`（workflow_resume.py:62）；verify 由 `verify_resume`（resume_verify.py:388） |
| Trace（V4/V5/V6 只读证据面） | `<DATA_DIR>/.webgis-agent/<sid>/trace_chains.jsonl`（legacy 只读）+ `trace_v6/manifest.json` + `seg_<n>.jsonl[.gz]`（trace_store.py:13-16/77-101；SEGMENT_SIZE=16 :49；目录 = `MAPSPEC_STORAGE_DIR` or `settings.DATA_DIR` :84-85） | trace_store（flock + 单调 seq + settle 幂等） | `read_chains_since`/`last_seq`/`iter_session_chains`、resume_anchor（:104 `trace_last_seq`） |
| Durable recovery ledger | `.webgis-agent/<sid>/recovery_ledger.json` + flock（app/services/gis_harness/recovery_ledger.py:54-76/115） | dispatch 失败 +1 / 成功清零（tool_dispatch_service.py:918-922） | `classify_and_remediate` 预算、continuation |
| 进程级 RemediationLedger | 内存全局 `_global_ledger`（app/services/gis_harness/failure_taxonomy.py:292；类 :252） | dispatch 失败路径 | `remediation_for`:295（无 session 上下文时降级口径） |
| Workflow runtime 运行态块 | `gis_chapter["workflow_runtime_v6"]`（`WORKFLOW_RUNTIME_KEY`，app/services/gis_harness/runtime_bridge.py:40） | `maybe_update_runtime_projection`（runtime_bridge.py:592）、workflow_instance 派生 | contracts（stale 维 hard input，contracts.py:208-218）、resume、`/sessions/{sid}/plan` |
| Ref/artifact 载荷 + 溢出 | session store + RefSpillStore（app/services/session_data.py:49-213，24h 会话面） | tools/registry | dispatch ref 解析、resume 重水合 |
| 会话事件账 | `append_event("tool_executed"/"tool_failed"/...)`（session_data；tool_dispatch_service.py:573/1474） | dispatch | SSE 回放、event_resume |

**结论（Phase A 现状）**：没有单一 runtime 状态机对象。状态分布在 ①SessionPlan.gis_chapter（事实面）②workflow_runtime_v6 块（派生运行态）③mapspec revision（期望态）④render observation（观察面）⑤recovery_state/ledger（预算面）⑥trace（证据面）。但派生方向单一：`derive_workflow_instance`（workflow_instance.py:478，纯函数）与 `derive_runtime_block`（runtime_bridge.py:113）都从 gis_chapter 行派生，`StageState`（workflow_instance.py:84-106）+ `StateTransition`（:234）+ `WorkflowEventKind→RECOMPUTE_DIMENSIONS`（:57-81）已是显式转移词表 —— V7 的"显式状态机"应是**统一这些投影的转移登记/恢复面**，不是新状态源。

---

## Section 3: Phase A-G 缺口普查

状态图例：EXISTS=生产可用；PARTIAL=有机制但有明确缺口；MISSING=无。

### Phase A — 显式运行时状态机

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| 阶段状态词表 | EXISTS | `StageState` 8 态 workflow_instance.py:84-94；`PlanNodeStatus` plan_graph.py:51；`GoalNodeStatus` 7 态 goal_graph.py:63-70 | 统一映射表已有（_NODE_TO_STAGE :98-106）；V7 加"运行中回合归属/最终验收态"需 additive 扩词表 |
| 转移事件+维度 | EXISTS | `WorkflowEventKind.dimensions`（workflow_instance.py:57-81）；`StateTransition`:234；预算 MAX_TRANSITIONS=32 :122 | 把"事件→转移→验证"登记为可查询序列（现只留最近 32 条快照） |
| 确定性派生（无第二真相） | EXISTS | `derive_workflow_instance`（workflow_instance.py:478，O(nodes) 纯函数）；`derive_runtime_block` runtime_bridge.py:113 | 无需新状态源；V7 挂事件日志即可 |
| 全转移可追溯 | PARTIAL | 转移历史被钳 32 条（workflow_instance.py:122）；trace 有 STAGE_FINALIZATION 等（pipeline.py:707-728） | 转移账本持久化（session-plane 文件，仿 trace_v6 布局） |
| 可恢复/可测 | PARTIAL | resume 靠 anchor 指纹比对（resume_anchor.py:145-172 + resume_verify.py:248/309） | 状态机断点序列化 + "从任意态恢复"的确定性测试钩子 |

### Phase B — 长程计划/重规划

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| 计划版本化/指纹 | EXISTS | `rows_fingerprint` V2（workflow_instance.py:149-176，capability:status:bound_ref:algorithm:params_hash）；`package_fingerprint`（resume_anchor.py:166-168）；SessionPlan envelope_id + registry 指纹 staleness（session_plan.py:157-160） | 版本号递增 + diff 可读化（workflow_v4/diff.py 已有基础） |
| 步依赖/执行状态 | EXISTS | `PlanGraph`（plan_graph.py:103，`build_plan_graph`:254，`infer_dependency_edges`:173，`recommended_next`:477）；goal graph（goal_graph.py:227） | — |
| 观察记录 | EXISTS | StageEvidence（workflow_instance.py:181）+ RenderObservation | — |
| 失败分类 | EXISTS | 11 类 `HarnessFailureClass`（failure_taxonomy.py:44）+ planning/geocompute 适配器 :187/204 | — |
| 重试/修复 | EXISTS | `classify_and_remediate`（failure_taxonomy.py:321）+ durable 账本（recovery_ledger.py:115）+ runtime 修复按 fingerprint 分代预算（runtime_repair.py:412-477）+ completion 内联修复（pipeline.py:184-197） | — |
| 重规划 | PARTIAL | `decide_continuation` 有 `replan` 裁决词（continuation.py:44-47）；但 **replan 回路无生产驱动点**——`LOOP_BUDGETS` 明示不含 replan（durable_context.py:56-58）；V6 follow-up 已登记（06-pr-summary.md:151） | 把 replan 裁决接到生产回路（finalizer 或 turn settle）+ 预算入账 |
| 计划分支/回滚点 | PARTIAL | 候选计划 `generate_plan_candidates`（plan_candidates.py:391）+ `validate_candidate_graph`（goal_graph.py:405）；fallback 链（AlgorithmDescriptor.fallback_algorithms algorithm_registry.py:271；gis_ontology.FallbackTier :66） | 分支/回滚点无持久化锚（现只有 supersede 语义 session_plan.py:75-77） |
| 依赖感知部分重算 | EXISTS | `compute_affected_subgraph`（workflow_v4/recompute.py:111-126，WorkflowChange/RecomputePlan :40/56；正向闭包+端口归一 :95-108）+ `format_recompute_line`（runtime_bridge.py:770） | 生产执行器消费 recompute 清单的"最小重跑"闭环（现为投影/披露） |
| 幂等/防重复执行 | EXISTS | dispatch dedup（tool_dispatch_service.py:375-402）+ analysis reuse（:413-468）+ finalizer 三重门（revision/rows/observation，pipeline.py:569-573/645-678） | — |
| abort/resume/超时/上下文丢失重建 | EXISTS | anchor（resume_anchor.py:56/96-111 含 recovery_state+reasoning_digest V6 键）+ verify_resume（resume_verify.py:388，_mark_stale_and_plan :458）+ `HonestTurnFailure`（chat.py:882-893） | — |
| 续行裁决 | EXISTS | `decide_continuation`（continuation.py:75-176，纯函数）；挂接点 runtime_repair.py:358-390 | finalization 路径直接消费 decide_continuation（V6 follow-up，06-pr-summary.md:152） |
| 最终验收裁决 | EXISTS | `derive_product_verdict`（contracts.py:305，READY/READY_WITH_WARNINGS/NEEDS_REPAIR/BLOCKED_BY_DATA/BLOCKED_BY_METHOD）+ 七维契约 `evaluate_completion_contract`（contracts.py:166-296） | — |

### Phase C — 分层持久上下文

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| durable/rebuildable/forbidden 三分层 | EXISTS | `classify_context_key` + 封闭词表（durable_context.py:37-51/67-78） | 词表扩容（turn/session/project/workspace 级键现只有 workflow 位置级） |
| checkpoint + crash recovery | PARTIAL | recovery_state turn 边界写 map_state（durable_context.py:108-148）；写入"非原子，靠调用方 session lock"自认（:122-124） | checkpoint 原子性（二段写/版本号），跨 worker 锁丢失路径 |
| compact/summarize + 预算管理 | PARTIAL（chat 侧） | `plan_budget/GisBudgetAdvisor`（app/services/chat/context_budget.py:142/306）+ `run_history_ops/condense/offload`（context_policy.py:208/266/286）+ V6 块 v6_context_blocks.py | harness 侧上下文预算与 chat 预算无统一账本 |
| 选择性检索 | PARTIAL | hybrid 工具检索（semantic_retrieval.py:679）；上下文块选择 `select_active_node`（v6_context_blocks.py:132） | 面向 context-assembler 的按层检索 API（现按预算截断为主） |
| 新鲜度/冲突/溯源 | EXISTS | ref content_hash/data_fingerprint 证据（resume_anchor.py:114-142）；`goal_key` supersede（session_plan.py:96）；observation 代次门（pipeline.py:671-678） | — |
| 持久化/重载 | EXISTS | anchor additive JSON 键（模型 app/models/project.py:225）；resume 重注入（resume_anchor.py:219 起；ref_map/missing_refs） | — |
| 项目级记忆 | PARTIAL | `harvest_project_memory`（memory_harvest.py:127，turn 末收割 chat.py:851）；"memory lags evidence by one step"（chat.py:848-850） | project/workspace/map/data 分层上下文对象（现只有收割-注入两点的松散记忆） |

### Phase D — 超越字符串匹配的能力检索

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| 结构化算子描述符 | EXISTS | `AlgorithmDescriptor`（app/lib/gis/algorithm_registry.py:242-302）：input/output_artifact_types、geometry_requirements、crs_requirements/crs_class、cpu/memory/io_cost、complexity、fallback_algorithms/fallback_semantics、resource_envelope/tolerance/cancellation_profile（ADR-0117） | 后置条件（postcondition）/可靠性评分/延迟实测轮廓（现只有 cost 档位与 conformance_tests 静态声明） |
| 混合检索（词法+双语扩展+capability graph+方法论+embedding） | EXISTS | `hybrid_signals`（semantic_retrieval.py:679）+ `expand_query_terms`:323 + `negation_anti_terms`:375 + `methodology_signal_tools`:571 + `embedding_retriever`:826（可选，`TOOL_RETRIEVAL_SEMANTIC` tool_surface_v3.py:60）；kill switch `GIS_TOOL_RETRIEVAL_V6=0`（semantic_retrieval.py:44；tool_retrieval.py:82） | — |
| 置信度/弃权 | EXISTS | `compute_confidence`（semantic_retrieval.py:743）；`SurfaceSelection.confidence/abstained/abstain_reason`（tool_surface_v3.py:263-265）；模型面披露（pi_turn_context.py:340-345） | — |
| 非 tool 能力联合检索（template/component/model/workflow） | PARTIAL | 组件槽位 `ComponentResolver`（component_resolver.py:48）；模板兼容 `TemplateCatalog`（template_catalog.py:68）；方法论 12 族/44 候选仅作检索信号（semantic_retrieval.py:571；V6 doc D1）；模型 descriptor 覆盖 `MODEL_DESCRIPTOR_OVERRIDES` env | 统一 capability 索引跨 tool/algorithm/template/component/model/workflow 六类（现各自 registry，检索只汇于 tool 面） |
| 历史成功反馈 | PARTIAL | durable ledger 记 (session,tool,failure_class) 预算（recovery_ledger.py:115）；跨会话可靠性分无 | 全局（匿名化）可靠性/延迟反馈环，回写 descriptor |
| fallback 链 | EXISTS | `fallback_algorithms`+`fallback_semantics`（algorithm_registry.py:271/292）；`fallback_v3.py`；ontology FallbackTier（gis_ontology.py:66） | — |
| 金标语料 | PARTIAL | `build_retrieval_eval_corpus`（app/evaluation/retrieval_eval_corpus.py:69）**实测 598 条**（本机运行：direct 209 / paraphrase 240 / near_duplicate 52 / hard_negative 52 / ambiguous 31 / out_of_scope 14；`RetrievalEvalCase` dataclass :39，构造器 `_c`:58）——注意已比 V6 PR 的 358 增长；**但仍是硬编码 `_c(...)` 行**（:84-718 + `_build_v6_additions`:719） | V7 目标 2k-5k 场景需**可演进的生成式/分域结构**（domain packs + 生成纪律），不能继续堆行 |

### Phase E — 地图感知观察与批评

| 可观察项 | 状态 | 证据 |
|---|---|---|
| 可见/隐藏图层 | EXISTS | `_layer_declared_visible`（completion/validators/layers.py:19）、F_LAYER_HIDDEN/F_LAYER_MISSING/F_NO_RESULT_LAYER（pipeline.py:276-278） |
| extent/视野 | EXISTS | `_check_extent`（completion/map_verification.py:168）；viewport `F_VIEWPORT_NO_BBOX`（pipeline.py:236-243） |
| 投影/CRS | EXISTS | `validate_semantics` CRS 契约（validators/semantics.py:160-187，WGS84 等价归一 :29-40） |
| 图层顺序 | EXISTS | `_check_layer_order`（map_verification.py:132） |
| 陈旧覆盖层 | EXISTS | `_check_stale_overlays`（map_verification.py:202） |
| 图例/标题/色带 | EXISTS | legend 三型映射 `_LEGEND_KIND_TO_COMPONENT`（semantics.py:21-26）+ F_SEMANTIC_LEGEND_MISSING/MISMATCH（:99-141）+ F_TITLE_MISSING_REPORT（:151-158） |
| 必需组件（槽位） | EXISTS | `validate_components`（validators/components.py:35，required_slots）+ `ComponentComposer`（component_composer.py:51） |
| chart 渲染/数据点 | EXISTS | `chart_observation_state`（observation_states.py:85）+ V5 render 校验（render_observation.py:349） |
| 组件重叠 | EXISTS | `derive_component_layout_findings`（render_observation.py:101，矩形交叠面积 :71-93） |
| 导出完整性 | EXISTS | `assess_export_parity`（pipeline.py:228 调用；validators/viewport_export.py） |
| 观察状态阶梯 | EXISTS | mounted→loaded→rendered→data_present→semantically_correct（observation_states.py:52-139，`to_workflow_health`:139） |
| scale（比例尺）/north-arrow 组件 | PARTIAL | 依赖产品 facet 槽位表（product_facets.py LEGEND_FAMILY 等；semantics.py:88 注明"不建第三词表"）；无 scale 专项校验 |
| symbol（符号等级/视觉变量） | PARTIAL | 渲染侧 legend_spec 有类型；完成期无视觉变量专项校验 |
| label collision / blank map | PARTIAL | 前端 HUD 遥测阈值（CARTO_LABEL_WARN/FAIL_RATIO 等 conftest 钉扎 tests/conftest.py:73-77）+ `visual_evaluator.py` LLM 评估通道（触发白名单 :48-54）——但 **`GIS_VISUAL_EVALUATOR` 默认空=关闭（visual_evaluator.py:39-40）**，无像素级 blank-map 校验 |
| 渲染观察语义升级 | EXISTS | `validate_render_observation`（render_observation.py:349）→ finalizer render_status（pipeline.py:208-224） |

### Phase F — 终结钩子

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| 终验入口（幂等/有界） | EXISTS | `maybe_finalize_map_product`（pipeline.py:508：dedup 门 :569、final_gate :571、锁内写 :631-701）；非流式 settle（chat.py:814）+ 流式 settled + 观察驱动（chat.py:1690） | — |
| 用户意图满足检查 | PARTIAL | `intent_verified=(result.status=="complete")`（pipeline.py:699）+ workflow_contract 义务（contracts.py:179-201）；"结果 vs 结论文本一致性"无（无 LLM-free 或 LLM 的 answer↔map 对账） | 结果-结论一致性校验（可先确定性：结论引用的 ref/数值 ↔ artifact 现值） |
| 必需图层可见 | EXISTS | validate_layers + layer_status（pipeline.py:276-279） | — |
| chart/legend/scalebar/north-arrow | PARTIAL | 组件槽位校验（components.py:35）覆盖 chart/legend 族；scalebar/north-arrow 走 facet 槽位，无专项语义校验 | 补 facet 槽位语义映射（仿 semantics.py 模式） |
| 失败回路由（局部修复/重规划） | PARTIAL | 修复闭环 `_apply_repairs`（completion/repairs.py:21）+ `plan_repairs_for_chapter`（repair_planner.py:327）+ `evaluate_repair_loop`:235；runtime 侧 `run_runtime_repair`（runtime_repair.py:393）；**但 finalizer 不直接消费 `decide_continuation`（经 runtime_repair 间接，V6 follow-up 已登记）**；replan 无驱动点 | finalizer 出口接 `decide_continuation`；needs_repair→replan 路由 |
| 提交上下文/工件状态 | EXISTS | `map_product` 块 + repair_memory 合并 + observation_health（pipeline.py:688-700）+ 链事件 VERIFICATION/REPAIR/FINAL_VERDICT（:338，`_emit_finalization_chain`:48） | — |
| 最终显示确认钩子 | PARTIAL | SSE `task_complete`（pipeline.py:792）+ observation 序列门防陈旧盖章（:669-678）；"用户端最终显示确认"（前端确认渲染后回执）无独立钩子——现靠 observation POST 驱动重验 | 增量式 final-display 确认（可复用 cartographic-observation 端点语义） |

### Phase G — 多代理委派

| 能力 | 状态 | 证据 | V7 需补 |
|---|---|---|---|
| 专用角色词表 | EXISTS | 12 roles：`gis_inspector/scientific_reviewer/cartography_reviewer/algorithm_reviewer/map_observer/result_verifier/data_researcher/tool_result_verifier/planner/corpus_worker/spatial_scientist/cheap_summarizer`（app/services/subagent_roles.py:111-282） | — |
| 预算档 class | EXISTS | `BUDGET_CLASSES` light/standard/heavy/research（subagent_roles.py:369-379）+ `SubagentBudget`:388 + token 闸 `wrap_dispatch_with_budget`:486 | — |
| 递归上限 | EXISTS | depth≤2 硬限（app/services/subagent.py:281-286，contextvar `_subagent_depth`:129） | — |
| lineage/父记账 | EXISTS | SubagentResult.lineage（subagent.py:62-81）+ 父 evidence roll-up（:437-448） | — |
| 并行委派 | EXISTS | `run_parallel`（subagent.py:630） | — |
| **harness→子代理程序化委派** | MISSING | 唯一生产派发点是 LLM 工具 `app/tools/subagent.py:162/175`（模型自行 spawn）；**gis_harness/ 目录 0 处调用 SubagentDispatcher**（grep 证据）；planner（MapProductPlanner）不委派 | planner/runtime 的显式 handoff schema + 子代理结果回写为章节证据（附着点见 Section 4） |
| 子代理失败回收 | PARTIAL | 预算超限上抛（app/services/chat/tool_pipeline.py:229）+ BudgetExceeded（subagent_roles.py:35） | 结构化"子代理失败→父重规划"回路 |

---

## Section 4: V7 应附着的接缝（精确签名）

1. `decide_continuation(*, recovery_state: dict, observation=None, qualification=None, failure=None, ledger_attempts: int = 0) -> ContinuationDecision` — continuation.py:75。**终验/收口回路的裁决点**。
2. `maybe_finalize_map_product(session_id: str, *, reason: str = "tool_result", force: bool = False, final_gate: bool = False) -> Optional[MapCompletionResult]` — completion/pipeline.py:508。终结钩子入口。
3. `run_map_finalization(session_id, *, chapter=None, max_passes=MAX_FINALIZATION_PASSES, reason="manual", prior_repairs=None) -> Optional[MapCompletionResult]` — pipeline.py:115。
4. `derive_workflow_instance(...)` — workflow_instance.py:478（纯派生；V7 状态机投影从这里扩展）；`rows_fingerprint(chapter)` :167。
5. `compute_affected_subgraph(dag: dict, changes: Sequence[WorkflowChange]) -> RecomputePlan` — workflow_v4/recompute.py:111。部分重算清单。
6. `build_anchor(session_id) -> Optional[dict]` / `resume_from_anchor(db, *, anchor_id=..., user_id=...)` — resume_anchor.py:56/219；`verify_resume(...)` — resume_verify.py:388。长程恢复面。
7. `update_recovery_state(session_id, *, position=None, loop=None, detail="", reset_loop=None) -> dict` — durable_context.py:108（LOOP_BUDGETS :58 是 V7 新回路的记账点）。
8. `classify_and_remediate(status, code, error_type, message, tool_name, session_id)` — failure_taxonomy.py:321；`get_recovery_ledger()` — recovery_ledger.py:338。
9. `DynamicToolSurface.select(ctx: ToolSelectionContext) -> SurfaceSelection` — tool_surface_v3.py:301/461；`hybrid_signals(registry, query)` — semantic_retrieval.py:679；`compute_confidence` :743。**检索扩展接缝**。
10. `compute_turn_active_tools(...)` — pi_native_surface.py:414（生产检索消费点）。
11. `AgentPlanOrchestrator._synth_plan_from_harness`（plan_orchestrator.py:534）/ `MapProductPlanner.plan_from_intent`（planner.py:516）/ `generate_plan_candidates`（plan_candidates.py:391）——计划生成/分支接缝。
12. `build_goal_graph(chapter)`（goal_graph.py:227）/ `build_plan_graph(...)`（plan_graph.py:254）/ `recommended_next(graph)`（plan_graph.py:477）。
13. `SubagentDispatcher(registry, parent_session_id).run(...)`（subagent.py:270）/ `.run_parallel`（:630）/ `select_tools_for_subagent`（:145）+ `SubagentRole` 注册表（subagent_roles.py:45）。
14. `validate_render_observation(...)` — render_observation.py:349；`aggregate_observation_state`/`to_workflow_health` — observation_states.py:97/139。
15. `evaluate_completion_contract(result, methodology_warnings, chapter)` — contracts.py:166；`derive_product_verdict` :305。
16. `run_runtime_repair(...)` — runtime_repair.py:393（含 `_attach_continuation` :358 的裁决挂接范例）。
17. 观察端点 `POST /chat/sessions/{sid}/cartographic-observation`（chat.py:1498）——runtime 闭环的 HTTP 面。

---

## Section 5: 测试与验证事实（本机实测）

**pytest 约定**（pytest.ini）：`testpaths=tests`、`asyncio_mode=auto`、`timeout=60`（thread）、`addopts=--cov=app --cov-report=term-missing`（**需要 pytest-cov；本机未装 → 必须 `-o addopts=""` 或安装**）。markers：`heavy`/`perf`/`cartography`/`real_services`；perf 无 `-m perf` 时被 conftest 自动 skip（tests/conftest.py:199-217）。

**conftest 事实**（tests/conftest.py）：~200 键 `_ENV_BASELINE` setdefault（:26-163），关键钉扎：`USE_NEW_AGENT=false`（:130 注释——测试不拉 Pi 子进程）、`USE_REDIS=false`、CELERY memory://；autouse `_pin_auth_bypass_off`（:172）与 `_offline_embedding_model`（:216，Windows 无 fcntl 时守卫降级 no-op :226-235）。**无 DB/Redis 硬依赖的纯单元面**：tests/unit/gis_harness/ 76 文件绝大多数为纯函数测试（如 test_goal_graph.py 直接构造 planner 章节，零 I/O；test_durable_context_continuation_v6.py 用 tmp_path+monkeypatch MAPSPEC_STORAGE_DIR）。`test_chaos_invariants_v6.py` spawn 真实子进程 kill -9（:36-62）——Windows 可跑（实测通过）。

**本机命令（Windows Git Bash，全部实测）**：
```bash
# 单文件（10 passed, 10.55s）
OVERPASS_API_URL="https://140.82.121.4/interpreter" NOMINATIM_URL="https://140.82.121.4/search" \
  python -m pytest tests/unit/gis_harness/test_goal_graph.py -o addopts="" -q

# V6 核心（18 passed, 2 skipped, 1.07s）
... python -m pytest tests/unit/gis_harness/test_durable_context_continuation_v6.py \
  tests/unit/gis_harness/test_chaos_invariants_v6.py -o addopts="" -q

# gis_harness 全量：9 failed / 1103 passed / 3 skipped，246s（~4 分钟）
... python -m pytest tests/unit/gis_harness/ -o addopts="" -q

# lint（全绿）
python -m ruff check app/services/gis_harness/ tests/unit/gis_harness/
```
**必须带 OVERPASS/NOMINATIM 覆盖的原因**：本机 DNS 被 VPN fake-IP 拦截（overpass-api.de → 198.18.0.85 实测），Settings 的 SSRF 校验在构造期做 DNS 解析并拒绝私有 IP（app/core/config.py:428-470），任何 Settings 构建即炸：`ValueError: ... resolves to private/reserved IP 198.18.0.85. Blocked (SSRF)`。IP 字面量 URL 绕过（实测有效）。

**剩余 9 个失败均为环境缺失非代码回归**：`test_analysis_graph`（`fcntl` Unix-only，app/services/rag/faiss_store.py:5 顶层 import）；`test_kriging_vertical_slice`×3（`No module named 'h3'`）；`test_conformance_corpus`×3（`capabilities unresolved: ['mcda_evaluation']`，同 h3 类依赖缺口）；`test_benchmark_harness`、`test_component_lifecycle[trio]`（同 import 链）。→ V7 验收口径建议钉：`tests/unit/gis_harness/ -o addopts=""` 下 1103+ passed / 9 failed 为本机已知环境红。

**CI 口径**（scripts/quality_runner.py:90-96）：backend lane = `pytest -m "not perf and not cartography and not real_services" --cov-fail-under=75 --timeout=120 -q`；science lane = `pytest tests/science_oracles/ tests/unit/lib/ --no-cov -q`。

---

## Section 6: 风险登记簿（Top 10）

1. **四投影同步风险**：gis_chapter 行 → goal_graph / plan_graph / workflow_instance / runtime_bridge 四个派生面 + SessionPlan.progress 第五面。全为纯派生（无第二真相）但 V7 新增状态若绕过 `derive_workflow_instance` 即成第二源。证据：workflow_instance.py:11/478、runtime_bridge.py:113、session_plan.py:67。
2. **两个 remediation 账本并存**：进程级 `_global_ledger`（failure_taxonomy.py:292）与 durable `RecoveryLedger`（recovery_ledger.py:115）。authority 关系已文档化（durable 优先），V7 若再加预算面（如 replan loop）必须入 `LOOP_BUDGETS`（durable_context.py:58）而非造第三本账。
3. **replan 有裁决无驱动点**：continuation.py:44 列出 replan verdict，但 LOOP_BUDGETS 不含 replan（durable_context.py:56-58 自认"入而无驱动=预算耗尽永不可达"）。V7 接驱动点时若先加预算后加驱动（或反之）会出现不可达/无界回路。
4. **finalizer 只信 `map_product` 单键 + 三重门**：revision/rows/observation 任一漂移即拒绝盖章（pipeline.py:645-678）。V7 改验证输入（如新增 display 确认）必须同步进 `_dedup_gate_blocks`（:358）门钥匙，否则门失效→每触发点重跑或陈旧块永久保护。
5. **fcntl/h3 等 Windows/依赖缺口**：faiss_store.py:5 顶层 `import fcntl` 使 Windows 上任何触达 rag 链的测试红；`h3` 缺失红 kriging/conformance。V7 新代码顶层 import Unix-only/可选依赖会复制此模式。
6. **Settings 构造期 DNS/SSRF 假阳性**：config.py:461-470 构造期解析 DNS；VPN/内网环境直接炸 Settings（本机已复现）。V7 新增 URL 型 env 会放大该雷。
7. **map_state 下划线键影子状态通道**：`_recovery_state`（durable_context.py:34）、`_cartographic_context_observation`（chat.py:325）、`REPAIR_STATE_KEY`（runtime_repair.py）、`_cartographic_mutation_revision`（pipeline.py:556）都是 map_state 键值袋里的非 SessionPlan 状态。V7 加键需守 durable_context 分层词表纪律（白名单外=forbidden）。
8. **dual agent 路径语义差**：Pi 路径在 agent_settled 跑 finalizer（chat.py:805-822 注释承认非流式曾是缺口）；legacy ChatEngine 不跑同样 settle。V7 的收口钩子必须两路径同验（现 final_gate 幂等已缓解）。
9. **子代理只存在于 LLM 工具面**：harness 无法程序化委派（Section 3G）。V7 做 planner→worker 委派时若绕过 `SubagentDispatcher`（预算/lineage/depth 上限都在里面，subagent.py:129/281/437）会失去预算与审计保障。
10. **检索语料硬编码行数已近 600**：retrieval_eval_corpus.py 598 条 `_c(...)` 字面量行（V6 PR 时 358 → master 已增长）。按此增速到 2k-5k 场景的行式维护不可持续（V6 follow-up :153 已预告）。

---

## Section 7: V7 实施顺序建议（waves，additive-first）

- **W1 状态机登记面（Phase A）**：新增 session-plane 转移账本（仿 trace_v6 文件布局）记录 StageState/WorkflowEventKind 转移序列；`derive_workflow_instance` 与 `maybe_update_runtime_projection`（runtime_bridge.py:592）为写挂点。零新表。
- **W2 长程回路闭环（Phase B）**：①finalizer 出口直连 `decide_continuation`（接缝 2+1，V6 follow-up 兑现）；②replan 回路：LOOP_BUDGETS 增 `replan` + 驱动点（needs_repair/BLOCKED_BY_METHOD 时经 `generate_plan_candidates` 出分支候选，`validate_candidate_graph` 收敛）+ `compute_affected_subgraph` 清单驱动最小重跑消费器。
- **W3 上下文分层扩容（Phase C）**：DURABLE_FACT_KEYS 词表扩 turn/session/project 键；checkpoint 原子化（recovery_state 版本号+二段写）；context_budget（chat）与 harness 回路面共用预算口径。
- **W4 能力检索统一索引（Phase D）**：AlgorithmDescriptor 增 postcondition/可靠性/延迟轮廓 additive 字段（有 validator 先例 algorithm_registry.py:304-362）；`hybrid_signals` 增 template/component/model 信号通道；跨会话可靠性反馈回写（durable ledger 聚合投影）。
- **W5 语料结构化（Phase D）**：retrieval_eval_corpus 改"域 pack + 生成纪律"结构（recipe_packs/ 27 域为分域骨架），目标 2k-5k 场景；指标门沿用 retrieval_eval_report（:1311）。
- **W6 地图观察补全（Phase E）**：scalebar/north-arrow facet 语义映射（仿 semantics.py `_LEGEND_KIND_TO_COMPONENT` 模式）；visual_evaluator 通道默认化评估（`GIS_VISUAL_EVALUATOR` 现默认关）；label/blank 走 render observation 投影扩展。
- **W7 终结钩子增强（Phase F）**：结果-结论一致性校验器（validators/ 新文件，确定性先行）；final-display 确认（复用 cartographic-observation 语义）；验收失败路由 `decide_continuation`。
- **W8 程序化委派（Phase G）**：handoff schema + `SubagentDispatcher.run` 包装的 harness 侧委派（数据审计→gis_inspector、算法选择→algorithm_reviewer、制图 QA→cartography_reviewer 角色已存在），parent provenance/失败回收沿 lineage 既有通道。

每 wave 均不改 typed DAG 编译器、不动 GeoCompute scheduler、零新表（session-plane 文件 + additive JSON 键），与 V6 纪律（01-architecture.md:5）一致。

---

## quick facts

- **Python**: 3.13.9（Anaconda 全局 `C:\ProgramData\anaconda3`；**repo/worktree 内无 .venv/venv**；依赖按 requirements.txt + requirements-dev.txt 全局安装）
- **pytest**: 8.4.2 + pytest-asyncio 1.3.0 + pytest-timeout 2.4.0；**pytest-cov 未装** → 本地跑必须 `-o addopts=""`（或 `pip install pytest-cov`）
- **ruff**: 0.12.0，`python -m ruff check app tests`（选 E4/E7/E9/F，pyproject.toml:65-68）；harness 代码当前全绿
- **fast subset（Windows 实测命令）**：
  `OVERPASS_API_URL="https://140.82.121.4/interpreter" NOMINATIM_URL="https://140.82.121.4/search" python -m pytest tests/unit/gis_harness/ -o addopts="" -q`（~4 min；单文件秒级）
  环境覆盖原因：VPN fake-IP DNS（198.18.0.0/15）触发 Settings 构造期 SSRF 校验拒绝（config.py:428-470）
- **已知本机红**：fcntl（Unix-only import）、h3 缺失、DNS/SSRF 假阳性 —— 9/1113 失败均为环境非回归
- **CI lanes**：backend `-m "not perf and not cartography and not real_services"` + cov 75；perf/cartography/real_services 独立 lane；science lane `--no-cov`
- **gis_harness 规模**：~50 顶层模块 + completion/(8) + workflow_v4/(11) + recipe_packs/(27) + validators/(9)；tests/unit/gis_harness 76 文件
