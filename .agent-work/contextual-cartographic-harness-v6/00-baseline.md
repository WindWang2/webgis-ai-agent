# 00 — Baseline 审计（Phase 0）

- 日期：2026-09-09
- 分支：`feat/contextual-cartographic-harness-v6`（worktree：`/home/kevin/projects/webgis/feat-contextual-cartographic-harness-v6`）
- HEAD：`8a33e3a5`（= 最新 origin/master；`git fetch` 后 ff 确认无新提交）
- 审计方式：2 个只读 subagent（后端 runtime/workflow、制图/渲染/前端观测）+ 主 agent 对关键论断逐一二次验证（✅ 标记处）
- 开放 PR / issue：均无（`gh pr list --state open`、`gh issue list --state open` 为空）
- Migration head：`0033_geocompute_v6_cluster`（`alembic heads` 实测 ✅）
- 基线测试：`pytest -m cartography -q --no-cov` → **708 passed, 7 skipped**（36.5s，release-blocking 门全绿）

最近合并的相关 PR（master 顶部）：#1165 Harness V5（ADR-0118）、#1168 Workflow V4、#1169 Workbench V5、#1170 Cartography V5、#1171 Extensions V2、#1172 Quality V2、#1163 GeoCompute V6、#1164 Lakehouse V6、#1166 Science V4、#1167 Query V6。

---

## A. Workflow Compiler V4（PR #1168）

模块根：`app/services/gis_harness/workflow_v4/`（红线自述：零 LLM/零 I/O、不建第二事实源）。

| 概念 | 文件 | 核心定义（file:line） |
|---|---|---|
| Methodology Family / Candidate Method | `workflow_v4/methodology.py` | `METHODOLOGY_FAMILIES`(:45，12 族)、`METHOD_QUALIFICATION_STATUSES`(:61)、`MethodCandidate`(:74)、`MethodologyFamily`(:115)、`MethodQualification`(:149)、`MethodologyRegistry`(:535)、`qualify_method_candidates`(:808) |
| Typed Workflow DAG / Typed Ports | `workflow_v4/typed_dag.py` | `TYPED_NODE_KINDS`(:23)、`TypedPort`(:46)、`TypedWorkflowNode`(:66)、`TypedWorkflowEdge`(:103)、`TypedWorkflowGraph`(:117)、`ports_compatible`(:151)、`validate_typed_dag`(:179)、`build_typed_dag`(:298) |
| Obligations | `workflow_v4/obligations.py` | `ObligationProvenance`(:42)、`InheritedObligation`(:60)、`ObligationChain`(:85)、`inherit_obligations`(:114) |
| WorkflowPackage | `workflow_v4/package.py` | `WorkflowPackage`(:48)、`CompatibilityVerdict`(:74)、`emit_workflow_package`(:116)、`check_compatibility`(:166)、`WORKFLOW_PACKAGE_SCHEMA_VERSION="1.0.0"`(:33) |
| Semantic Diff | `workflow_v4/diff.py` | `DIFF_KINDS`(:27，7 种)、`DiffEntry`(:38)、`WorkflowDiff`(:57)、`diff_workflow_packages`(:112) |
| Affected Subgraph / RecomputePlan | `workflow_v4/recompute.py` | `CHANGE_TARGETS`(:28，6 种)、`WorkflowChange`(:38)、`RecomputePlan`(:54)、`compute_affected_subgraph`(:93)；维度词表复用 `workflow_schema.RECOMPUTE_DIMENSIONS`(:25) |
| compile 入口 | `workflow_v4/compiler_v4.py` | `WORKFLOW_COMPILER_VERSION="4.0.0"`(:35)、`WORKFLOW_V4_STAGES`(:38，8 阶段)、`WorkflowCompilationV4`(:52)、`compile_workflow_v4`(:94)；底层 15 阶段 `COMPILER_STAGES` 在 `workflow_compiler.py:35` |

**生产接线状态【仅证据面，非执行面】**：
1. `app/services/chat/plan_orchestrator.py:643-710` `_compile_v4_evidence` → `compile_workflow_v4`（`asyncio.to_thread`），摘要存 `Plan.workflow_v4`(:196-199)。注意不对称：LLM `make_plan` 路径不附 V4 证据，仅合成路径有。
2. `app/tools/semantic_tools.py:300-368` tier-2 工具 `compile_workflow_semantics`（advisory 只读）。

**✅ 二次验证**：`compute_affected_subgraph` / `diff_workflow_packages` 全仓库 `app/` 内仅出现在 `workflow_v4/` 自身（定义 + docstring），**零生产调用**——局部重算未闭环。执行侧（`SessionPlan._mark_progress`、`planner_runtime`、`plan_graph`、`maybe_update_workflow_instance`）不 import V4 任何符号。**编译器是"算给人看"，runtime 跑另一套。这是 V6 最核心的缝。**

## B. GIS Harness Runtime V5（PR #1165，ADR-0118）

运行时无单一 loop 进程，由以下组合构成（均生产已接线）：
- 规划：`planner_runtime.py:39` `get_planner_runtime()`
- 行状态唯一写者：`SessionPlan._mark_progress`（`app/services/session_plan.py`，bound_ref 回填 :459-489）
- 终验：`maybe_finalize_map_product`（`completion/pipeline.py:484`）
- 实例事件：`maybe_update_workflow_instance`（`workflow_instance.py:868`）
- 修复：`run_runtime_repair`（`runtime_repair.py:284`，`MAX_RUNTIME_REPAIR_PASSES=2` :57）
- resume 路由：`app/api/routes/workflow_resume.py:27,56`

**节点状态词汇（逐字）**：
- ✅ `PlanNodeStatus`（`plan_graph.py:51-58`）：`pending, ready, running, complete, skipped, unavailable, failed`（7 值）
- `StageState`（`workflow_instance.py:84-94`）：`pending, ready, active, satisfied, blocked, stale, skipped, failed`（8 值），映射 `_NODE_TO_STAGE`(:98-106)
- `DEPENDENCY_STATES`（`workflow_instance.py:109`）：`satisfied, blocked, stale, unknown`
- `WorkflowEventKind`（`workflow_instance.py:57-67`）：`data_arrived, artifact_produced, artifact_stale, algorithm_change, parameter_change, style_mutation, observation, tool_failure`
- `BLOCK_*`（:113-117）：`BLOCKED_BY_DATA, BLOCKED_BY_METHOD, BLOCKED_BY_DEPENDENCY, BLOCKED_BY_EXECUTION, BLOCKED_BY_RENDER`

**execution/dependency 与 Compiler DAG 完全双轨**：`build_plan_graph`（`plan_graph.py:254`）只读 `data_requirements/analysis_steps` 扁平行 + registry 推断（`infer_dependency_edges` :173），不读 typed DAG。

**Resume / Trace**：`resume_anchor.py`（`RESTORABLE_CHAPTER_KEYS` :33-39、`MAX_ANCHOR_REFS=128` :31、ref 重水合 + `missing_refs/dangling_refs` 披露）；`trace.py` + `trace_store.py`（跨进程 flock、单调 seq、FINAL_VERDICT 永不丢）。

**失败/修复词汇（逐字）**：
- `HarnessFailureClass`（`failure_taxonomy.py:44-57`，11 类）：`tool_error, data_error, crs_error, empty_result, stale_ref, renderer_failure, timeout, partial_completion, cancelled, budget_exhausted, unknown`
- `RemediationAction`（:32-41，8 个）：`retry, retry_with_backoff, fallback_tool, substitute_operator, requery_profile, replan, reobserve, abort_with_disclosure`；`REMEDIATION_POLICY`(:61-73，max≤3)
- ⚠️ `classify_and_remediate`(:321) 仅被测试/评估引用，生产 dispatch 接入待核实
- runtime 修复动作（`runtime_repair.py:63-65`）：`reassert_spec_layer, restore_expected_visibility, reassert_component`

## C. 计划形态：名义一套、实际两套半

- `SessionPlan.gis_chapter`（`session_plan.py:73`）= 运行时唯一真相；`format_session_plan_projection`(:177) 统一投影
- `WorkflowInstanceState`（`workflow_instance.py:278`）持久化于 `gis_chapter["workflow_instance"]` 单键（:49），additive 纯派生
- `geocompute/plan.py` `ExecutionPlan`(:217)/`ExecutionNode`(:150)/`ExecutionRun`(:292) = 作业层第三套计划形态
- Compiler V4 DAG/WorkflowPackage = 只读咨询投影（唯一回写是 `derive_unblock_contract` :332 单调解除）
- 三者 node_id 命名各自为政（`cap:<capability>` vs `data:<role>` vs ExecutionNode id），无统一互引

## D. Artifact lineage

- `product_lineage.py` `build_facet_lineage`(:160)、`LineageRef`(:80)、`FacetLineageEntry`(:98，含 `recompute_capabilities/depends_on_facets`)；`artifacts_for/reusable_inputs/dead_outputs`(:118-149)
- 行 `bound_ref`（`session_plan.py:64`）；liveness 直投 `ArtifactRecord.status`（`product_lineage.py:55-76`）
- **缺口**：facet→artifact 正查有；artifact→node 反向索引无；artifact 与 typed DAG node 之间无持久双向指针，`producer_capability` 仅内存派生
- `ArtifactContract`（`app/lib/data/artifact_contract.py:173`）：`SourceInfo/TemporalExtent/ProducedBy/LineageInfo/Cacheability/Reproducibility/ContractDiagnostic`

## E. Completion verdict（生产已接线）

`completion/contracts.py`：
- `VERDICT_*`(:123-127)：`READY, READY_WITH_WARNINGS, NEEDS_REPAIR, BLOCKED_BY_DATA, BLOCKED_BY_METHOD`；推导 `derive_product_verdict`(:270)；七维 `evaluate_completion_contract`(:149)，维度词表与 `workflow_schema.COMPLETION_DIMENSIONS` 同源（parity 测试锁定）
- 完成态(:30-33)：`pending, needs_repair, complete, failed`；最终地图态(:115-118)：`verified, verified_with_degradation, failed, unknown`；render 态(:105-109)：`verified, issues, stale, unknown, not_applicable`
- finding codes（:36-95 逐字见 04-unified-findings.md 附录）
- 消费链：`run_map_finalization`（`pipeline.py:111`）→ `map_product_block`(:401) → SSE `task_complete`（`_is_task_complete` :721 要求 READY* + verified*）；去重门 `_dedup_gate_blocks`(:353) + 行指纹 `rows_fingerprint`（`workflow_instance.py:167`）

## F. Tool Retrieval

- ✅ `TOOL_RETRIEVAL_SEMANTIC` hook（`tool_surface_v3.py:37-40`）：`os.getenv(..., "").strip()` 默认空串 → **语义检索从不加载，生产等价 lexical-only**【仅扩展点】
- 生产接线：词法 `rank_tools`（`tool_surface_v3.py:506`，V4 门控 :467）；kill switch `GIS_TOOL_RETRIEVAL_V4=0`（`tool_retrieval.py:62`）
- 语料：`app/evaluation/retrieval_corpus.py` 确定性生成器（Layer A intent-306 + Layer B 口语 ×3 + Layer C，全量 ≥2000，`MIN_CORPUS_SIZE` :40）；**66 条人工金标在另一文件** `retrieval_eval_corpus.py`（`build_retrieval_eval_corpus` :65，开环 direct/near_duplicate/hard_negative/ambiguous，zh+en）
- 指标：`retrieval_eval_report`（`retrieval_eval_corpus.py:244`）`p@1/r@5/r@10/invalid_selection_rate`；钉门 0.65/0.81/0.87/0.33（诚实基线）；`tier3_leak`（`runtime_metrics.py:119`）期望恒 0

## G. Context Policy / Assembly

- `ChatContextAssembler.assemble`（`context_assembler.py:206`）：map_state 块 + plan 块 + verdict 块 + memory 块 + 历史（fold→truncate）
- 无 LLM session summary；`context_policy.summarize_dropped_turns`(:168) 指纹化确定性摘要；`history_compression` 预算 6000 tokens；KEEP pin（`SAFETY_PIN_MARKERS` :142-147）
- **node-local context 无**：`gis_harness/` 内零命中；最近似物是 `pi_turn_context.py:152` `bind_turn_prompt`

## H. Cartographic Rendering V5（PR #1170，ADR-0118 D1-D9）

- 诊断词表唯一真相：`app/lib/cartography/render_diagnostics.py` —— 18 码封闭词表 `RENDER_DIAGNOSTICS`(:43-139)，severity ∈ info/warning/error(:21)，`MAX_DIAGNOSTICS_PER_EXPORT=64`(:24)；未知码 `diagnostic()` 返回 None(:168-189)、外部载荷 `normalize_render_diagnostics`(:202-249) 拒绝未知码。逐字 18 码见 04-unified-findings.md 附录
- 发射器：后端 `mapspec_to_svg.py`（可见性过滤 :559、`features_truncated` :656、`export_timeout_partial` :567/664、`label_truncated` :805-812）；前端 `vector-svg-export.ts`/`frame-composer.ts`/`export-chrome.ts`（parity 测试锁定前端码 ⊆ 后端词表）
- Export parity：TS 孪生 `frontend/lib/mapspec-compiler/mapspec-to-svg.ts`；220 字符跨孪生 fixture；语义 scene oracle `describeRenderScene`（`render-scene.ts:77`）
- 服务端锚点（D6）：`POST /api/v1/export` 收 `render_diagnostics` → sidecar 持久化；`GET /export/diagnostics/{filename}` fail-closed（`app/api/routes/map.py:157-239`）
- user-wins（D2）：`quality_loop.py` `suppressed_repairs`(:471-473,542-554,592-596)；`lifecycle_engine.py:1570-1583` legend.visible=False 抑制自动修复；层可见性 user-wins :266,:490-539,:800

## I. MapSpec

- TS 契约 `frontend/lib/mapspec-compiler/types.ts`：`MapSpec`(:222)、`MapSpecLayer`(:110)、`MapSpecSource`（`content_revision` :57-60）、`MapSpecComponent`(:169，21 类联合)、`MapSpecLayoutConfig`(:214)
- 后端 `app/services/mapspec/`：`lifecycle_engine.py`（意图引擎）、`store.py`、`pipeline.py`（ref content_revision 盖章 :120-133）；`mapspec_store.py` 为兼容 adapter
- 事务/revision：单调 `mutation_revision`（`lifecycle_engine.py:76-81`）、`expected_revision` CAS（落后→`superseded` :80-89）、`_cartographic_mutation_revision` 状态键（`store.py:229`）、磁盘 sidecar `revisions/`
- 意图族：Init/SetView/UpsertSource/UpsertLayer/PatchComponent/RemoveComponent/DuplicateComponent/RebindComponent/RemoveLayer/SetLayout/Checkpoint/Rollback
- source 条目带 `ref/ref_id/data_fingerprint/profile_fingerprint`（`mapspec_store.py:179-198`）

## J. Render Observation 链路（浏览器→后端，生产已接线）

- 产生端：`frontend/lib/mapspec-runtime/runtime-evidence.ts:212-248`（`collectCartographicRuntimeObservation`：visible/source_converged/style_converged/render_complete/source_status/feature_count）；`render-observation.ts:272-312`（`collectRenderObservation` + components + runtime_errors 环(≤8) + charts）；settle `waitForRenderSettle`(:224-250, 400ms)；chart 遥测 `chart-render-registry.ts:46-60`（rendered/data_points/pending）
- 传输门：`app/api/routes/chat.py:1450` `POST /sessions/{id}/cartographic-observation`（DTO ≤256KB；fingerprint 门 `stale_mapspec_fingerprint`；client_generation 单调门；**服务端盖章 mapspec_revision**；latest-wins 存 `map_state["_cartographic_observation"]`）
- 消费端唯一：`render_observation.py` `validate_render_observation`(:142-382) → telemetry→finding 映射（render_layer_missing/render_source_missing/render_incomplete/render_style_not_applied/render_component_missing/chart_data_missing/render_error/render_revision_stale/render_unverified），`MAX_RENDER_FINDINGS=8`(:380)
- ADR-0086：增维不换通道、服务端盖章 revision、唯一消费方 finalizer

## K. Findings 词汇全景（4+2 套）

1. Harness Finalizer finding codes（`completion/contracts.py:36-95`，34 码，逐字见 04 附录）
2. Cartography Render Diagnostic（`render_diagnostics.py:43-139`，18 码）
3. Export Degradation（前端 `export-chrome.ts:130-150`，与 2 同词表子集关系测试锁定）
4. Quality Finding（`app/lib/quality/manifest.py:82` + 棘轮基线 `docs/quality/findings-baseline.json`，闸 `tests/quality/test_findings_ratchet_gate.py`）
5. CartographyFinding / 语义 check（`semantic_checks.py:41`，16 个 check 码 + 函数级检查）与组合校验码（`composition_validation.py:138-255`，15 码）
- 刻意双轨（非重复）：`layer_missing`(desired) vs `render_layer_missing`(observed)；`chart_data_missing`(live) vs `chart_ref_unavailable`(export)
- 相邻待收敛：`zone_collision`(组合期) vs `_detect_floating_overlaps`(完成期 `semantic_checks.py:2430`)；`layout_conflict`(finalizer) vs solver `overflow_suppressed`

## L. Workbench V5（PR #1169，ADR-0105）

- 组织态持久化：`PatchWorkbenchState` 整体替换 `mapspec["workbench"]`（`lifecycle_engine.py:1401-1415`），校验 `_workbench_doc_error`(:381-448：version==5、groups 无环、`lockedLayerIds: string[]`)，≤256KB，走既有锁+CAS，不建表
- 用户操作回传：可见性 `visibility-transaction.ts`、样式 `styleCommands.ts`、删除 `layerCommands.ts`（同一 mutation 引擎）；`workbenchCommands.ts` 仅 set_mode；组织态 `workbenchSlice.ts` + `frontend/lib/workbench/*`；协同 `collab.ts` BroadcastChannel `wb5:{sessionId}`（已提交 doc+revision，CAS last-writer-wins，无 presence/CRDT）
- **锁 guard 缺口（✅ 二次验证）**：前端命令层有锁分区（`visibility-transaction.ts:225-248` 全锁→`failed/layer_locked`；`styleCommands.ts:72`），但**后端 `lifecycle_engine.py` 对锁只有 doc 形状校验（:444-446），agent 直调 mutation 路径无锁检查**；component 级锁定词表不存在

## M. 组件状态 / 确定性视觉检查现状

- 统一组件生命周期词表（requested/materialized/rendered/visible/layout_valid/data_bound/diagnostics）**不存在**；现有投影：spec `enabled`、解析态 `ResolvedMapComponent`、观察态 `ObservedComponent.mounted`（`render-observation.ts:54-70`）、scene `RenderSceneComponent`、solver placement reason（`layout_solver.py:184-467`：requested/fallback_zone/overflow_suppressed/required_overflow_kept/duplicate_singleton/none）
- 确定性检查：`label_engine.py` solve（:257-316,408）**求解器生产零接线**，仅 `fit_label_text/wrap_label_text` 经孪生接线；`_detect_floating_overlaps`（spec 口径，前端实测 rect 已上报未消费）；solver overflow 已接线；**像素级 legend overflow/component offscreen/label clipping 无**；visual evaluator/VLM seam 无生产门（仅 `runtime_validator.py` 评估面）

## N. 已有质量闸 / 生成物（后续每 wave 必须保持绿）

- `pytest -m cartography`（release-blocking，本基线 708 passed）
- findings 棘轮：`docs/quality/findings-baseline.json` + `tests/quality/test_findings_ratchet_gate.py`（新 code 上限 0）
- `scripts/gen_workflow_catalog.py`（→ `docs/workflows/workflow-catalog.md`，`--check`）
- `scripts/gen_science_benchmark_manifest.py`（→ `docs/science/BENCHMARK_MANIFEST.md`）
- `scripts/gen_quality_manifest.py`（→ `docs/quality/QUALITY_MANIFEST.md` / `quality-manifest.json`，字节一致门）
- `component-catalog.generated.json` ← `app/lib/cartography/export_component_catalog.py:build_catalog`
- perf：`-m perf`（`tests/benchmarks/test_perf_harness.py`，`PERF_UPDATE_BASELINES=1` 刷新）
- real_services：`-m real_services`（需 REAL_SERVICES=1，自跳过）

## O. V6 收敛缺口总表（已满足项不重复开发）

已满足（复用）：七维完成契约 + verdict；失败 11 类 + remediation + 预算；resume anchor + ref 重水合；durable trace；facet 血缘 + `reusable_inputs`；context policy 三件套；去重门 + 行指纹 V2；lexical retrieval + 双语料 + 评测钉门；methodology 12 族 + typed DAG + package/diff/recompute 纯函数；render observation 链路（增维不换通道）；user-wins 抑制通道；MapSpec CAS/revision。

缺口（V6 本体）：
1. runtime 不执行 typed DAG（两套图双轨，node_id 无互引）
2. `compute_affected_subgraph`/`diff_workflow_packages` 零生产调用——partial recompute 未闭环
3. artifact↔node 无持久双向索引
4. 语义检索 hook 空置；retrieval 未消费真实情境（DAG node/artifact/CRS/budget）
5. 无 node-local context 模型
6. `ExecutionPlan`(geocompute) 与 SessionPlan/Compiler 三轨并存（需边界裁决，非消灭）
7. LLM `make_plan` 路径不附 V4 证据
8. 后端锁 guard 缺（agent 直调 mutation 绕过前端锁）；component 级锁无
9. 统一组件生命周期状态词表无
10. 像素级确定性视觉检查无；visual evaluator seam 无
11. Completion 未消费视觉 findings；repair 无 finding↔state 指纹防循环（现有 MAX_RUNTIME_REPAIR_PASSES=2 是粗粒度）
12. `classify_and_remediate` 生产 dispatch 接入待核实
