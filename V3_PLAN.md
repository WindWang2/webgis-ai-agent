# Pi GIS Runtime V3 — 工作计划（内部工作文件，不入库）

分支: feat/pi-gis-runtime-v3  基线: origin/master 222a994

## 审计结论（已完成，3 个并行只读 agent + 本地验证）

- live tools = 231（`ToolRegistry.all_metadata()`）；algorithm registry `tool_to_capability()` 覆盖 119/231。
- descriptor 机制完备（descriptor.py ADR-0101）但几乎未富化：capabilities/side_effect/tags 声明为 0（tags 仅 8）。
- model_runtime（descriptors/health/provider/roles/routing, ADR-0102）完整但**未接入任何 engine**。
  集成点: execution_engine._llm_config/_planner_llm_config (1016/1022), llm_client 结果→observe(), subagent.model_role 未消费, Pi models.json (pi_rpc_client:234)。
- Pi: 7 frozen native + webgis_execute proxy；spawn 时 native_tools_for_pi dump；extension index.mjs 全部经 postToBridge→/pi-tools/execute→ToolRegistry（单一执行真相已成立）。
- **vendor/pi 支持 `pi.setActiveTools(names)`（agent-session.ts:924 setActiveToolsByName，下一 turn 生效）+ `before_agent_start` 事件** → 动态工具面无需改 vendor：extension 注册超集，per-turn 由 prompt 内 marker 激活子集。
- context_budget.py 只有规划/度量（12 类）；无 GIS 联合预算、无 artifact offload 决策。
- trace.py 闭式事件表（256 ring）；replay.py 3 模式；evaluation/ 有 case/runner/golden_cases；无 retrieval/routing/context 指标。
- no_progress.py CallPatternTracker 已有 exact_repeat/alias_oscillation/repeated_read/repeated_mutation_no_state_change；缺 map/workflow epoch 联动。

## 实施序（10 commits）

1. `refactor(tools): introduce tool descriptor v3` — descriptor.py 扩展字段（input/output_artifacts, required_context, map_mutations, data_mutations, idempotent, latency/memory/scale_class, crs_semantics, unit_semantics, security_tier, required_permission, examples, anti_examples, failure_taxonomy, fallback_tool, capability_source 溯源）；registry.register 接受新 kwargs；**algorithm-registry 派生回填**（capabilities_source=derived）；validate 扩展。
2. `feat(tools): enrich live tool metadata` — 231 tools 按 module 批量富化（side_effect/tags/output_semantic_type/result_size_policy/latency_class/…），真实读取代码，未知留默认；`scripts/check_tool_descriptor_coverage.py` + 覆盖率 gate 测试。
3. `feat(runtime): add dynamic tool surface projection` — tool_surface_v3.py：ToolSelectionContext(intent/task/stage/data_profile/map_state) → capability 检索（lexical baseline + 可插拔语义）→ contract 过滤 → rank → 选 10-30 → 压缩 → 投影；deterministic + 可解释 reasons。
4. `feat(pi): wire dynamic tool surface into pi sessions` — spawn dump 全量 model-visible 超集；extension setActiveTools 默认面 + before_agent_start 扫 `[WEBGIS_ACTIVE_TOOLS:...]` marker；resolve_pi_tool_call 增 registered_dynamic 直呼分类（仍走 dispatch）；env gate PI_DYNAMIC_TOOL_SURFACE。
5. `feat(models): wire model runtime into live engine routing` — engine _llm_config/_planner_llm_config 经 ModelRouter.resolve_config（failure→legacy fallback）；call sites observe()；subagent role→router；Pi models.json/set_model；roles.py 新增 corpus_worker/code_worker/doc_crosscheck/descriptor_enrichment/cartography_reviewer/architecture_reviewer/debugger；routing reason 留痕。
6. `feat(context): add gis-aware context budgeting v2` — 新增类别 DATA_PROFILE/ALGORITHM_METADATA/CARTOGRAPHY_METADATA/TOOL_RESULTS；GisContextBudgeter.advise()（keep/compress/offload/drop + per-section tokens/dropped/reserve/overflow reason 可观察）；大 tool result → ref 不入 prompt 的策略接线。
7. `feat(runtime): harden cancellation retry and no-progress handling` — CallPatternTracker + map/workflow epoch（unchanged_map/unchanged_workflow reason codes）；ToolDispatchService pure/cacheable singleflight；subagent 递归深度预算。
8. `feat(trace): implement end-to-end gis trace and replay v3` — gis_trace.py 18 段证据链（timestamp/ids/fingerprints/decisions/cost/tokens）；trace.py 词汇表扩展；replay.py A/B（planner/route/retrieval/workflow regression）。
9. `test(runtime): expand runtime evaluation corpus` — retrieval recall@k/precision@k/irrelevant rate；routing fallback/failure/latency/cost；execution 完成率/重复调用/超时/no-progress；GIS correctness（算法族/数据资格/final map state）；context overflow/truncation/比例。
10. `docs(runtime): document pi gis runtime v3` — ADR-0103 + docs/agent-runtime/*。

## 资源约束

- 本地测试: `python -m pytest <module> --no-cov -q`（addopts 有 --cov，务必 --no-cov）；-m "not heavy and not real_services"。
- 不跑线上 CI；不等待远端。
- 前端不动 → 无需 frontend build；后端 build smoke = import + 既有单测子集。

## 红线

- ToolRegistry 单一执行真相；Pi surface 只是投影。
- 不伪造元数据；未知 → 显式默认（unclassified/unknown），溯源 capability_source。
- vendor/pi 子模块零修改。
- fingerprint 稳定性测试必须仍成立（同输入同指纹）。
