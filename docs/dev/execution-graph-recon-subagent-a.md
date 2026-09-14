# Execution Graph + Incremental Replanning — Recon (Subagent A)

Baseline: `origin/master @ 580b33e9` (merge of PR #1272). All paths relative to repo root; `path:line` citations verified against that SHA. Read-only recon; no code changed.

---

## Executive summary

1. **How many DAG-ish abstractions**: 12+ distinct graph/plan structures exist. They split into (a) **derived projections over SessionPlan.gis_chapter rows** — PlanGraph, SpatialGoalGraph, ProductGraph, AnalysisGraph (all zero-persistence, rebuilt on read); (b) **compiled graphs** — TypedWorkflowGraph (V4), WorkflowPackage Registry + WorkflowRuntime V5 executable instances (SQLite, CAS), project-level WorkflowEngine runs; (c) **persisted plans** — SessionPlan (Pi/host truth), CanonicalPlan (ChatEngine truth), data_fabric AcquisitionPlan (linear, not a DAG); (d) **registries-as-graphs** — CapabilityGraph V8, ComponentGraph, quality ArtifactGraph. **THE production one is PlanGraph** (`app/services/gis_harness/plan_graph.py`): every production projection ([GIS Plan] block, WorkflowInstance, runtime_bridge workflow_runtime.v1, AnalysisGraph, ProductGraph, plan_runtime recompute seeds) derives from it or from the same chapter rows.
2. **Most important production call chain** (Pi host, `USE_NEW_AGENT=1` default): `POST /api/chat/stream` (`app/api/routes/chat.py`) → `_use_pi_bridge()` (chat.py:502) → `PiBridge.stream_prompt` (`app/agent_pi_bridge.py:2020`) → Pi subprocess → HTTP callback `/pi-tools/execute` → `dispatch_tool` (agent_pi_bridge.py:429) → `_dispatch_tool_bound` (agent_pi_bridge.py:474) → shared `ToolDispatchService` → on ok: `apply_tool_result` (`app/services/session_plan.py:541`) → `_mark_progress` (session_plan.py:502, single writer of row status) → `maybe_finalize_map_product` → `maybe_update_workflow_instance` (agent_pi_bridge.py:772) → `maybe_update_runtime_state` (:788) → `maybe_update_runtime_projection` (:803) → SSE via `cache_session_plan_sse` (:718).
3. **Affected-subgraph orphan**: does NOT exist as an orphan. `compute_affected_subgraph` (`app/services/gis_harness/workflow_v4/recompute.py:111`) is live, with two production callers: `runtime_bridge.derive_runtime_block` (runtime_bridge.py:381) and `resume_verify.verify_resume` (resume_verify.py:526). What IS missing: its `RecomputePlan` is **advisory only** — `format_recompute_line` (runtime_bridge.py:770) projects stale/recompute text to Pi; the Harness never schedules re-execution (deliberate "Harness 不执行工具" red line, plan_runtime.py:27).
4. **Top-3 convergence recommendation**: (1) evolve **PlanGraph** (capability DAG, deterministic node ids = capability ids, pure evaluator) into the Execution Graph core, adopting the typed DAG `cap:`/`data:`/`output:` node-id namespace already shared by runtime_bridge; (2) keep **workflow_runtime.v1 block** (runtime_bridge.py:533) as the runtime state sub-block (revision, stale, reuse_validation already there); (3) reuse **plan_runtime.PlanRuntimeBlock** (plan_runtime.py:176) for plan versioning + `request_replan` (plan_runtime.py:378) as the replan entry, and the **WorkflowRuntime V5 store/machine** (app/services/workflow_runtime/store.py:271 node-level CAS) only if durable cross-turn execution is in scope.
5. **ADR number: 0183**. Master has ADRs through 0179; open PRs claim 0180 ×3 (#1274/#1275/#1277), 0181 (#1276), 0182 ×2 (#1278/#1279). 0183 is the first uncontended number.
6. **Top overlap risks**: #1277 (harness_kernel + SessionPlan v2 edits `session_plan.py`, `agent_pi_bridge.py`, `execution_engine.py` — the exact files an Execution Graph touches), #1276 (wires qualification/candidate planners into production planning; edits `planner.py`/`recipes.py`), #1275 (`gis_situation` diff module — a competing "what changed" facts layer), #1279 (governor wraps the same dispatch block), #1274 (adds `pi_input_gate` into the dispatch pre-path).

---

## Part 1 — Inventory of graph/plan/DAG/workflow abstractions

### 1. `app/services/gis_harness/plan_graph.py` (540 lines)

- (a) **Purpose/schema**: dependency-aware projection of MapProductPlan rows ("Runtime v3"). `PlanNode` (plan_graph.py:80): `node_id` (= capability id), `kind` (requirement|analysis), `depends_on`, `status`, `bound_ref`/`output_ref`, `input_refs`, `optional`, `cost_class`, `fallback_to`, `blocked_by`. `PlanGraph` (plan_graph.py:103): nodes + `dropped_cycle_edges` + `unresolved_dependency_refs`; docstring declares it a pure derived view, never persisted (plan_graph.py:29-36).
- (b) **Lifecycle**: `PlanNodeStatus` = pending/ready/running/complete/skipped/unavailable/failed (plan_graph.py:51). `_evaluate` (plan_graph.py:368) is an idempotent fixpoint: optional-unavailable→skipped, mandatory blocked propagation, blocked-recovery, ready derivation; complete is never overwritten (plan_graph.py:379-385).
- (c) **Serialization**: pydantic models, in-memory only; rebuilt from persisted chapter rows each read (`build_plan_graph` accepts the persisted chapter dict, plan_graph.py:254). No version field; `manifest_fingerprint` carried from plan (plan_graph.py:107).
- (d) **Stable ids**: yes — `node_id == capability` (deterministic, plan-graph vocabulary). Edge inference is deterministic: declared `depends_on` wins; missing field → `infer_dependency_edges` from capability registry artifact-type intersection with incremental cycle-skip (plan_graph.py:173-203).
- (e) **Callers**: `session_plan.py:282-286` (format_session_plan_projection [GIS Plan] block), `chat/v6_context_blocks.py:315,376`, `gis_harness/action_intent.py:188`, `analysis_graph.py:53`, `plan_runtime.py:128-130`, `runtime_bridge.py:177`, `workflow_compiler.py:356`, `workflow_instance.py:504`, `tools.py:507`, `planner.py:718` (infer edges at plan build).
- (f) **Wired**: yes — this is the production DAG.
- (g) **Resume/CAS**: none locally; the persisted chapter rows it projects carry status/bound_ref; staleness handled by upstream (`session_plan_stale` manifest fingerprint, session_plan.py:157).
- (h) **Verdict: REUSE** — this is the natural Execution Graph core; missing pieces are durable node identity across plan revisions (id = capability survives re-plan; no run-scoped ids), and any executor.

### 2. `app/services/gis_harness/analysis_graph.py` (302 lines)

- (a) Three-layer inspection projection per ADR-0097: goal node + execution nodes (PlanGraph, ≤96) + product nodes (ProductGraph facets, ≤64) + deterministic `next_action` (analysis_graph.py:12-19, 211-235). `recompute_impact: "downstream"` marker on execution nodes (analysis_graph.py:72-73) and `recompute_dims` per facet (analysis_graph.py:33-41, 111).
- (b) Status mirrors PlanGraph/ProductGraph; no own state machine.
- (c) Plain bounded dict; `build_analysis_graph_for_session` (analysis_graph.py:260) loads SessionPlan + MapSpec + render observation, never raises.
- (d) ids: goal (`"goal"`), capability node ids, facet ids — stable.
- (e) Production caller: `GET /sessions/{session_id}/analysis-graph` route (`app/api/routes/analysis_graph.py:11,17`) → frontend `frontend/components/agent/analysis-graph-panel.tsx` (via `frontend/lib/api/analysis-graph`). Also tests (`test_workflow_guards.py:237`).
- (f) Wired: yes (REST read model for UI).
- (g) No persistence; fully rebuildable.
- (h) **Verdict: REUSE as the read/SSE-facing projection** of the future Execution Graph; it already defines the "recompute impact" vocabulary the incremental-replan work needs.

### 3. `app/services/gis_harness/goal_graph.py` (494 lines)

- (a) `SpatialGoalGraph` (goal_graph.py:152) — methodological skeleton (goal → acquire → inspect → validate → transform → analyze… → deliver + disclose), V4 Wave 3. `GoalNode.kind` from closed vocabulary (goal_graph.py:38-52), `dep_kinds` (data/science/artifact/map/disclosure, goal_graph.py:55-60).
- (b) `GoalNodeStatus` = pending/ready/satisfied/blocked/stale/skipped/failed (goal_graph.py:63-70) — read-only projection of row status (goal_graph.py:214-224).
- (c) `to_bounded_dict` schema `goal_graph.v1` with `fingerprint()` (canonical-JSON sha256 via workflow_instance.canonical_fingerprint, goal_graph.py:160-165); `diff()` added/removed/changed (goal_graph.py:197-209); `validate_candidate_graph` (goal_graph.py:405) deterministically validates LLM-proposed graphs (kind vocab, ≤48 nodes, ≤6 deps, cycles, capability existence).
- (d) ids: `acquire:<cap>`, `inspect:<cap>`, `validate:<cap>`, `<kind>:<cap>`, `disclose:<code>` — deterministic.
- (e) Caller: `analysis_graph.py:248-252` (embedded into analysis graph as `goal_graph` key).
- (f) Wired (via analysis-graph endpoint).
- (g) fingerprint + diff — good diff basis, no CAS.
- (h) **Verdict: LEAVE-ALONE** (inspection/methodology layer); its `diff()` and candidate validation are patterns to copy, not to extend into execution.

### 4. `app/services/gis_harness/product_graph.py` (716 lines)

- (a) Goal→Product facets projection (ADR-0085). `ProductNode` dataclass (product_graph.py:87): node_id/kind/status/artifact_ref/inputs. `build_product_graph` (product_graph.py:197) projects chapter rows + MapSpec layers/components; `build_facet_completion` (product_graph.py:530) adds per-facet completion with descriptors/observation/revision.
- (b) Status constants done/pending/failed/ready/off (product_graph.py:41-45); `_ROW_STATUS_TO_NODE` mapping (product_graph.py:47).
- (c) dataclass, bounded `summary_line` for Pi (product_graph.py:137); not persisted.
- (d) facet ids like `map_layer:<layer_id>`, derived — stable per chapter.
- (e) Callers: analysis_graph.py:87, runtime_bridge.py:428 (`_spec_source_ref`), action_intent.py, map_completion.
- (f) Wired.
- (g) No.
- (h) **Verdict: LEAVE-ALONE** (product-side projection; recompute dims already defined in analysis_graph).

### 5. `app/services/gis_harness/workflow_compiler.py` + `workflow_schema.py` + `workflow_v4/` + `workflow_families.py`

- **workflow_compiler.py (549 lines)**: V3 `compile_workflow` (workflow_compiler.py:130) producing `WorkflowCompilation` with `WorkflowStageRecord`s — consumed today only by evaluation/anti-claim (app/evaluation/anti_claim.py:232, runner.py:440) and as import base for compiler_v4 (compiler_v4.py:28). Legacy evidence generator. **Verdict: LEAVE-ALONE/DEAD-ish for execution purposes.**
- **workflow_schema.py (674 lines)**: `WorkflowProfile`/`DataRoleRequirement`/`ScientificObligation`/`WorkflowContractReport` (workflow_schema.py:104-263), `resolve_data_roles` (:387), `evaluate_workflow_obligations` (:463), `RECOMPUTE_DIMENSIONS` vocabulary (imported by recompute.py:25 — the single dimension vocabulary: data/algorithm/parameter/style/output/recipe). **Verdict: REUSE (vocabularies + role resolution).**
- **workflow_v4/typed_dag.py (499 lines)**: `TypedWorkflowGraph` with typed ports (artifact_type/geometry/CRS/unit), `TypedWorkflowNode` (`node_id` convention `<kind>:<name>`: `data:<role>`, `cap:<capability>`, `transform:<op>:<role>`, `output:<artifact>` — typed_dag.py:68, 345, 361, 401, 458), `validate_typed_dag` (:179 — dangling deps, port type compat, DFS cycle check, primary-output reachability), `build_typed_dag` (:298). Bounded ≤64 nodes/≤128 edges (typed_dag.py:42). **Verdict: REUSE — this node-id namespace is the de-facto shared runtime namespace (runtime_bridge.py:53-62 re-exports `capability_node_id`/`role_node_id`).**
- **workflow_v4/compiler_v4.py (390 lines)**: `compile_workflow_v4` (:94) → `WorkflowCompilationV4` (:52, includes methodology_family, method qualification, typed_dag, package_fingerprint). Called from runtime_bridge.py:153-156 and plan_orchestrator.py:673 (evidence only).
- **workflow_v4/recompute.py (198 lines)**: `WorkflowChange` (:40, dimension ⊆ RECOMPUTE_DIMENSIONS + target_kind ⊆ CHANGE_TARGETS :28), `RecomputePlan` (:56, recompute/reuse/reuse_artifacts/explanations, bounded), `compute_affected_subgraph` (:111) — forward closure over data-flow edges ∪ structural deps, O(V+E), conservative ("宁可多算"), port-suffix normalization fix at :95-108. **THE affected-subgraph engine. Verdict: REUSE.**
- **workflow_v4/diff.py (272 lines)**: `diff_workflow_packages` (:112) → WorkflowChange list → feeds compute_affected_subgraph (diff.py:12,119). **Verdict: REUSE for change classification.**
- **workflow_v4/acquisition.py, cartography.py, evaluation.py, methodology.py (992), obligations.py, package.py, parameters.py**: package assembly (`WorkflowPackage` in package.py) — the compiled, fingerprinted artifact persisted by the V5 registry.
- **workflow_families.py (614 lines)**: `WorkflowFamily`/`CompositeRecipe`/`ScenarioTemplate` (:36-72), `WorkflowFamilyRegistry` (:440). Family catalog (recipe → family mapping); supports compile_workflow_v4's method-family selection. **Verdict: LEAVE-ALONE.**

### 6. `app/services/gis_harness/workflow_instance.py` (1036 lines) + `workflow_promotion.py` (245)

- (a) WorkflowInstance = run-state state machine over chapter rows (ADR-0104 Wave 1). `WorkflowInstanceState` (workflow_instance.py:278): `instance_id` (session:envelope:plan), `state_revision` (monotone, content-change +1), `state_fingerprint`, `gate_fingerprint` (dedup gate), `stages` (InstanceStage :196 with StageEvidence fingerprint/status current|stale|unknown :181), `dependencies` (edge state :219), `science` recheck (:255), `transitions` ring (:234).
- (b) `StageState` = pending/ready/active/satisfied/blocked/stale/skipped/failed (:84) with `_NODE_TO_STAGE` 1:1 mapping from PlanNodeStatus (:98); `WorkflowEventKind` (:57) maps events → RECOMPUTE_DIMENSIONS (:69-81).
- (c) pydantic → `to_bounded_dict` schema `workflow_instance.v1` (:300); persisted at `gis_chapter["workflow_instance"]` single additive key (:49).
- (d) Node identity = capability (from PlanGraph); deps from same graph.
- (e) Production callers: `maybe_update_workflow_instance` (:868) triggered from agent_pi_bridge.py:770-772 (tool result), :890 (error), :2280; chat.py:1590-1592 (render observation); projected by `format_instance_line` (:832) into the SessionPlan projection (session_plan.py:224-228).
- (f) Wired.
- (g) Revision + fingerprints + gate (dedup); merges transitions (:772); content fingerprint (:759). No CAS across workers — guarded instead by session lock + goal/rows drift validation (:935-990 pattern, same as runtime_bridge).
- **Verdict: REUSE/ADAPT** — it is the closest existing "node states with evidence versions + monotone revision" structure. An Execution Graph could supersede the parallel `workflow_runtime.v1` block (see §10) or absorb it.

### 7. `plan_runtime.py` (479) + `planner_runtime.py` (60) + `planner.py` (1892)

- **plan_runtime.py** (V7 / ADR-0134 D2): plan **versioning + replan driver + failure-seeded minimal recompute**.
  - `compute_plan_fingerprint` (:63) = H(goal+rows(V2 signatures)+contract core) — any change → new version.
  - `seed_recompute_from_failures` (:106) — failed rows → capability + downstream closure (reuse elsewhere); pure.
  - `PlanRuntimeBlock` (:176) schema `plan_runtime.v1` persisted at `gis_chapter["plan_runtime"]`; version history ring ≤8, rollback points ≤4 (:50-52).
  - `maybe_advance_plan_version` (:316) — gate + derive + session-lock persist; triggered from runtime_state_machine.py:647-650.
  - `request_replan` (:378) — **the production replan entry**: budget check via durable_context.LOOP_BUDGETS["replan"] (:55-57), sets `replan_pending`, durable accounting via `update_recovery_state`; exhausted → `abort_with_disclosure`. Called from completion pipeline (app/services/gis_harness/completion/pipeline.py:904-906).
  - **Verdict: REUSE — this is the incremental-replan skeleton already in master** (version/replan-pending/min-rerun), missing an actual graph executor and affected-subgraph-driven replanning (its closure is failure-seeded only, not evidence-drift-seeded — that half lives in runtime_bridge).
- **planner_runtime.py**: module singleton accessor `get_planner_runtime()` (:39). Trivial.
- **planner.py** structure index (1892 lines): models `DataRequirement` (:535, statuses pending/available/unavailable/failed, `depends_on`, `optional`, `bound_ref`), `AnalysisStep` (:550, pending/done/skipped/unavailable/failed), `AlgorithmSelectionRecord` (:561, fallback_trail/backend evidence), `PlannedLayer` (:580), **`MapProductPlan`** (:591 — THE plan schema: data_requirements/analysis_steps/map_layers/components/algorithm_selections/manifest_fingerprint/methodology_warnings/workflow_contract, status draft|finalized :608), `_plan_id` = sha1(query|recipe)[:12] (:637); class `MapProductPlanner` (:661) — deterministic, no LLM/IO: `_resolve_capabilities` (:705), `plan_from_intent` (:772), `finalize_with_profile` (:1079), `_evaluate_workflow_contract` (:1678), `assess_completeness` (:1830). Singleton via planner_runtime. **Verdict: REUSE (plan schema + deterministic planner are the facts source; do not duplicate).**

### 8. `resume_anchor.py` (497) + `resume_verify.py` (595) + `recovery_ledger.py` (354)

- **resume_anchor.py**: project-level resume anchor (ADR-0118 D8) — minimal "where to continue" pointer (goal + key chapter blocks + trace cursor + ref list ≤128) + W14 per-ref revision snapshots (content_revision/content_hash/data_fingerprint) and workflow fingerprints (:25). Auth: owner-only recovery.
- **resume_verify.py**: verify-not-assume post-resume validation (:1-27): liveness/revision/data-existence/mapspec-dependency/fingerprints → verdicts live/stale/unknown; stale/unknown satisfied stages → stale + forward closure via **compute_affected_subgraph** ("W5 唯一入口", resume_verify.py:470-526). **Verdict: REUSE — it already contains the "stale seeds → affected closure" flow.**
- **recovery_ledger.py**: durable (session, tool, failure_class) → attempts ledger, write-through JSON + flock, TTL decay, `copy_between_sessions` for resume. **Verdict: LEAVE-ALONE** (budget side).

### 9. `capability_graph.py` (628) + `candidate_planner_v8.py` (176) + `qualification_v8.py` (383)

- **capability_graph.py**: V8 unified capability graph (ADR-0137) — nodes = identity only + bounded summary, closed relation vocabulary, registry-fingerprint-keyed immutable cache, `validate_graph()` machine gate. Read-only projection; retrieval entry.
- **qualification_v8.py**: pure six-context qualification (eligible/ineligible/degraded/unknown with structured reasons — no bools) + `ExecutionEstimate` with basis disclosure (measured|declared|estimated|unknown).
- **candidate_planner_v8.py**: CandidatePlan = graph retrieval → qualification filter → estimate sort → reliability penalty → deterministic tie-break; "execution still goes through ToolRegistry/ToolDispatchService single pipeline".
- (f) **ORPHAN status confirmed**: `grep -rn "qualification_v8|candidate_planner_v8" app/` minus self → **zero production callers** (matches PR #1276's Phase-0 finding). Only tests reference them.
- **Verdict: ADAPT later** — PR #1276 activates them for plan-time capability resolution; an Execution Graph should consume qualification results as node annotations, not re-implement them.

### 10. `durable_context.py` (200) + `trace.py` (185) + `trace_store.py` (673) + `observation_states.py` (172) + `runtime_state_machine.py` (837)

- **durable_context.py**: 3-layer durable context model (durable facts / rebuildable projections / forbidden),载体 = map_state `_recovery_state` + anchor additive keys; `LOOP_BUDGETS` includes the `replan` entry whose driver is plan_runtime.request_replan (:57).
- **trace.py**: in-memory bounded per-session event ring + counters (ADR-0088 P7); never blocks business.
- **trace_store.py**: durable evidence-chain segments (flock, monotone seq, settle idempotent, ADR-0118/0119 D6).
- **observation_states.py**: rendered-state ladder unknown→pending→mounted→loaded→rendered→data_present→semantically_correct; `to_workflow_health` (ok/degraded/blocked/partial).
- **runtime_state_machine.py** (V7 ADR-0134 D1): `HarnessRuntime` task-level phase machine — phases are **read-only derivations** of chapter facts; `RuntimeTransition` ring ≤16; SUSPENDED is an overlay flag; persisted `gis_chapter["runtime_state"]`; `maybe_update_runtime_state` also calls `maybe_advance_plan_version` (:647-650). Triggered from agent_pi_bridge.py:785-792 and chat.py:1602-1608.
- **Verdict: LEAVE-ALONE** (observability/phase layer), but it is the natural place an Execution Graph publishes "which node is executing".

### 11. `app/lib/quality/artifact_graph.py` (283 lines)

Repo **build-time** generated-artifact fingerprint ledger (source scripts → generated files, sha256 canonical, staleness check vs docs/quality/generated-artifacts.json). Unrelated to runtime GIS artifacts despite the name. **Verdict: LEAVE-ALONE / name-collision hazard only.**

### 12. `app/lib/cartography/component_graph.py` (615 lines)

MapSpec `layout.components` flat list → runtime graph (ComponentNode/ComponentLink; edge types binds_to/requires/groups/annotates/under; single source remains MapSpec). `topological_component_order` (component_graph.py:448) — one of the few topological sorts in-repo. Used by QA semantic checks + finalization. **Verdict: LEAVE-ALONE.**

### 13. `app/services/planning/` (capability.py, deps.py, followup.py, models.py, recovery.py, store.py)

- **models.py**: `CanonicalPlan` (:137) — persisted ChatEngine-side plan truth. `PlanStatus` lifecycle proposed→validated→running→partially_completed|completed|failed|cancelled|superseded (:20-51; partially_completed deliberately non-terminal, resumable :24-27). `CanonicalStep` (:111): id ("s1"), n, goal, tool_family, tool_binding, tool, args with `${stepId.path}` placeholders, `depends_on` (step ids), status, result_ref, error (FailureClass + RecoveryAction :66-99). `next_pending_steps` (:163) and `recompute_status` (:183) pure derivations; `bump_revision` (:224).
- **store.py**: `PlanStore` (:100) — write-through over session_data_manager at deterministic alias `plan-current` (:49); **revision guard** refusing cross-plan_id clobber when persisted revision is newer (store.py:180-198); supersede → history alias `plan-id:<plan_id>` (:205); L1 TTL 2s LRU (:54). Tombstone clear (:243).
- **deps.py**: static `${ref}` validation (:39), `_topological_order` (:77), `resolve_arg_refs` (:153).
- **recovery.py**: `classify_error` (:44) → FailureClass; `recovery_action_for` (:158) — replan_remaining is an existing action word.
- **capability.py**: tool capability validation of plans (:111).
- (e) Callers: plan_orchestrator.py:35-41 (CanonicalPlan is truth; orchestrator `Plan` is a compatibility projection :7-10), execution_engine `_flush_plan`/`_maybe_plan`; **Pi path does NOT use CanonicalPlan** (pi-host-seams.md:39).
- (g) Revision guard ≈ optimistic concurrency, not full CAS.
- **Verdict: ADAPT** — CanonicalPlan already has per-step depends_on + resumable partially_completed + revision; if the Execution Graph must serve the ChatEngine path too, converge on this model's step semantics, but its steps are tool-level (not capability-level), so unification is nontrivial. PR #1277 K4 plans a one-way projection adapter (CanonicalPlan → SessionPlan).

### 14. `app/services/session_plan.py` (853 lines)

- `SessionPlan` (:67): envelope_id/session_id/user_goal/`gis_chapter` (dict holding the MapProductPlan dump + additive runtime blocks)/`progress` list of `CapabilityProgress` (:59)/replaced/superseded.
- **`_mark_progress` (:502) is the single writer** of row status — writes both `plan.progress` rows and gis_chapter `data_requirements`/`analysis_steps` rows (status + bound_ref) (:523-540). `apply_tool_result` (:541) maps tool hits → capabilities (via `capabilities_hit_by_tool` :477 and `_tool_to_capability` :469) and marks complete/failed; `merge_map_product_result` (:592).
- Projection: `format_session_plan_projection` (:177) composes head line + instance_line + recompute_line + [GIS Plan] DAG block + products + next_action; `events_to_sse` (:331) refuses CanonicalPlan event names on the Pi path (`CANONICAL_PLAN_EVENT_NAMES` guard).
- Slot/open: `ensure_session_plan_slot` (:383), supersede events (:456), archive (:416); `goal_key` (:96) = scope|subject|task stable same-goal key; `session_plan_stale` (:157) registry-generation staleness.
- Persist: session store with per-session lock (load/save :344/:367).
- **Verdict: REUSE — SessionPlan is the host plan truth; the Execution Graph must be one more additive `gis_chapter` projection (the established extension pattern: workflow_instance / workflow_runtime_v6 / plan_runtime keys), never a second writer.**

### 15. `chat/plan_orchestrator.py` (885) + `chat/planner.py` (42) + `chat/engine_instance.py` (36)

- plan_orchestrator: `PlanStep`/`Plan` dataclasses (:176/:188) as projections of CanonicalPlan; `should_plan` gates (:293, followup kinds skip planning); `make_plan` (:721) LLM planning with H-1 deterministic short-circuits (minimal gate :514; harness synth from MapProductPlanner at `_synth_plan_from_harness` :534 with `_HARNESS_SYNTH_MIN_CONFIDENCE=0.65` :67); `_compile_v4_evidence` (:653) attaches compile_workflow_v4 bounded evidence; `advance_step` (:875) marks steps done on tool completion (qualified wildcard for "core", PRESENTATION_TOOLS excluded :92).
- planner.py: module-level get/set/clear/`mark_step_done`/`make_plan` indirection over the orchestrator singleton.
- engine_instance.py: singletons for engine + registry.
- **Verdict: LEAVE-ALONE** on the ChatEngine side; the Execution Graph's natural integration on that path is `advance_step` → graph node completion.

### 16. `chat/execution_engine.py` (2795 lines) — structure index + tool loop

Key members: `_select_tools` (:457), `_build_system_prompt` (:608), `_maybe_plan` (:1068), `chat` non-stream (:1257) → `_chat_locked` (:1364), `chat_stream` (:1778), `_dispatch_tool` (:2647) → `_pipeline_dispatch` (:2657), `_persist_tool_messages` (:902), `_repair_orphaned_tool_calls` (:863).

Tool-loop execution path (chat_stream, Pi is the default host but this is the fallback):
1. Turn lock held for the entire turn (:1805-1825); `task_start` SSE (:1868).
2. `_maybe_plan` in keepalive pump (:1877-1913) → `plan_ready` SSE (:1933) with real done flags.
3. LLM streaming loop: `token`/`content` events (:2057, :2114); on tool_calls: parallel wave dispatch — each tool via `_dispatch_tool` → `_pipeline_dispatch` → ToolDispatchService; SSE per wave: `step_start` (:2154), `tool_call` (:2161), cancellation `step_cancelled` (:2207/2255/2333), `step_error` (:2278/2368), `plan_step_done` (:2316 — orchestrator step check), `step_result` (:2349/2391) + `tool_result` with slim_event (:2339/2392).
4. Results recorded via `_persist_tool_messages` (:2434) in original tc order; orphan tool_call repair (:2447); no-progress circuit breaker (`_stream_no_progress_streak`, :2494-2512).
5. Finalize: `plan_finalized` (:1961 — only if a non-terminal plan existed this turn), `task_complete` (:2545), `done` (:2551).
- Note: the legacy engine does **not** call `session_plan.apply_tool_result` — that wiring is Pi-bridge-only. The Execution Graph cannot assume both hosts feed it today; #1277's kernel aims to unify this.
- **Verdict: LEAVE-ALONE (fallback path)**; new graph events should ride the shared dispatch/SSE seams, not this 2.8k-line generator.

### 17. `app/services/workflow_engine.py` (1696) + `app/services/workflow_runtime/` (6887 lines total)

- **workflow_engine.py**: "Persistent Workflow & DAG Re-run Execution Platform" — project-scoped `Workflow/WorkflowRevision/WorkflowRun/Artifact` DB rows; snapshot + fingerprint contracts (graph/dataset/run fingerprints); `rerun_from_step` / `execute_workflow_run` (used by app/tools/project_tools.py:268,279 and routes/project.py:16). Provenance invariants (INV-SNAP/INV-PART/INV-REPLAY). This is the **user-authored project workflow DAG** feature — separate lineage from the GIS harness. **Verdict: LEAVE-ALONE** (different domain), but its replay/resume fingerprint discipline is a good reference.
- **app/services/workflow_runtime/** — Semantic Workflow Runtime V5: "把 Workflow V4 编译产物从 evidence-only 升级为可执行、可恢复、可增量重算、可复用证明的运行时事实" (__init__.py:1-15). Layers: Package Registry (DB, semver) → Runtime Instance Store (**instance rows + per-node rows, two-level CAS**, store.py:1-10) → Binding Gate → DAG Execution Driver → GeoCompute adapter → Artifact Binding/Reuse Index → semantic change → affected subgraph → incremental recompute + reuse proof.
  - `machine.py`: pure transition validation + READY set (build_adjacency normalizes `node.port` endpoints — same normalization as recompute.py:95).
  - `driver.py`: wave scheduling, READY→RUNNING CAS claim tokens, reuse fingerprint loop, cancel at wave boundaries, orphan RUNNING reset on lease expiry.
  - `store.py:271` `transition_node` node-level CAS (optimistic lock + conflict re-read + SQLite busy backoff).
  - `recompute.py:155` returns `cas_conflict` for deferred change application; pending changes ≤16, drain at wave boundaries.
  - `reuse.py`: node_reuse_fingerprint → index → eligibility → STALE/RUNNING→SUCCEEDED-with-reuse-evidence.
- Production exposure: full REST API `app/api/routes/workflow_runtime.py` (`/packages/register`, `/instances`, `/instances/{id}/run|cancel|changes|recompute-plan|nodes/{id}/retry|clone|debug`, :97-387). **But**: the chat/Pi production path does NOT drive these instances — sessions don't create WorkflowRuntime instances automatically (only main.py:475 recovery hook, session_plan.py:581 hooks attach plan metadata, mapspec_mutations.py:222 style-change hook record into instances if present). It is a complete, wired-but-awaiting-consumption execution substrate. **Verdict: REUSE (if the Execution Graph needs durable execution/CAS/reuse), else LEAVE-ALONE; do not rebuild a second node store.**

### 18. `app/services/mapspec/` + `mapspec_checkpoint_store.py`

- `checkpoint.py`: CheckpointStore — whole-checkpoint content addressing (same (mapspec+refs) → reuse id), per-ref blob dedup by content sha256 (`blobs/<sha>.json`), atomic writes, rollback (checkpoint.py:1-12).
- `lifecycle_engine.py`: MapSpec intent mutations (InitProject/SetView/UpsertLayer/RemoveLayer/SetLayout), transactional candidate-build-validate-persist, checkpoint→disk→redis ordering with rollback (:1-13); `mutation_revision` counter (mapspec_store.py:76) is the cartographic mutation revision consumed by runtime gates.
- `coordinator.py`: compile MapSpec→MapLibre via TS CLI.
- `store.py`/`pipeline.py`: persistence + compile pipeline; `mapspec_checkpoint_store.py` is a 7-line re-export.
- **Verdict: LEAVE-ALONE** (presentation layer; its revision number feeds graph gates, and its checkpoint/content-hash patterns are precedent for artifact dedup).

### 19. `app/services/data_fabric/` — acquisition plan

- `contracts.py`: `AcquisitionStep` (:122, step_type closed vocab: source_select/bbox_clip/field_projection/aggregate_pushdown/time_filter/pagination/sampling/version_pin), `CostEstimate` (:141), `AcquisitionBudget` (:152), **`AcquisitionPlan`** (:163) — `plan_id`, `dataset_key`, `version` (pinned for replay), `steps` (ordered list — **linear pipeline, not a DAG**), `diff()` (:178). `FallbackDecision` (:203) records source switches with `comparable=False` conservatism.
- Planner side: `data_fabric/planning/` (compiler.py, cost_model.py, explain.py, replay.py) compiles/adjudicates these; execution via `fabric/runtime.py` + adapters. ADRs 0173-0175 cover it.
- **Verdict: LEAVE-ALONE** (data-supply domain); relevant only as a diff/replay pattern reference.

### 20. Artifact/ref stores

- **artifact_registry.py** (1107, ADR-0082): per-session `ArtifactRecord` per tool product; artifact_id **reuses the existing ref string** (`ref:geojson-…`) so `bound_ref` rows are compatible projections; derives `ArtifactGraph` (producer/consumer/lineage/replacement) as pure functions — no second truth.
- **artifact_revisions.py** (496): append-only durable revision ledger; idempotent on (artifact_id, content_sha256); content truth solely in BlobStore.
- **artifact_lifecycle.py** (693): disk reclamation sweeps (exports/reports/uploads).
- **ref_lifecycle.py** (221): **single invalidation contract** for refs — the only sanctioned state transition API; all caches (ref_payload_cache, spatial_index, tile LRU) hook the same points (the #1111 ghost-data incident is the motivating doc).
- **ref_payload_cache.py** (164): (session_id, ref_id) resolved-payload LRU, TTL+byte-bounded, invalidation hooked at overwrite/delete/store-evict/clear.
- **app/lib/artifact_cache.py** (702, ADR-0048): content-addressed disk cache for expensive deterministic GIS outputs; key = sha256(source identity+mtime+size, operation, params, **owner scope**).
- **app/lib/tool_cache.py** (479): Redis tool-result cache; key `tool_cache:v2:<sha256[:16]>` including owner scope; **TTL-only invalidation; `ref:`-taking tools are never cached** (correctly — refs mutate).
- **Tenancy**: owner-scope hashing shared across caches; reuse/reads never cross owner (workflow_runtime __init__ red line).
- **Verdict: REUSE** — refs already are stable artifact pointers with content hashing and a single invalidation contract; Execution Graph node outputs should keep using `bound_ref` + artifact_registry records (runtime_bridge W5 already consults `list_artifacts` for reuse validation, runtime_bridge.py:647-656).

### 21. `app/agent_pi_bridge.py` (2734 lines) — Pi loop bridge & hook points

Structure: `PiToolRequest/PiToolResponse` (:145/:157); dispatch cache rendezvous (:301-341); **`dispatch_tool` (:429) → `_dispatch_tool_bound` (:474)** — the single production tool callback: dedup/repeat handling, harness event recording, TaskTracker step recording with late-turn drop guard (:666-703), then on ok: `apply_tool_result` (:711) → SSE cache; `maybe_finalize_map_product` (:763); **`maybe_update_workflow_instance` (:772); `maybe_update_runtime_state` (:788); `maybe_update_runtime_projection` (:803)**; on error: failed marking with lock-contention retry (:843-880). `PiBridge` class (:1336) — subprocess lifecycle, turn lease, abort, `prompt` (:1708), `stream_prompt` (:2020). `PiBridgePool` (:2642) per-session bridge pick.

**Where a graph executor can hook without duplicating Pi's tool loop**: exactly this `_dispatch_tool_bound` post-result chain (it is already the sequential trigger point for every derived projection) and/or `runtime_state_machine.maybe_update_runtime_state` which already fans out to plan-version advance. A graph-driven scheduler should NOT live inside Pi (the LLM loop stays in Pi per architecture); it should consume the same trigger and *advise* the next tool (projection) or, if given execution authority, sit beside dispatch like WorkflowRuntime V5's driver does with its claim-token/CAS protocol (driver.py docstring: CAS as the sole dispatch arbiter, chat-side writes to claimed nodes rejected with CLAIM_MISMATCH).

### 22. `app/services/gis_world_state/`

- `state.py`: `build_world_state` — unified bounded read model over mapspec (desired) + map_state (runtime registry + revision + observations) + provenance; zero payloads, bounded summaries.
- `mutation.py`: `apply_gis_mutation` facade — identity/CAS/transaction → lifecycle engine; origin policy (UserPresentationGuard: agent cannot reverse user presentation decisions).
- `provenance.py`: bounded decision-chain append.
- **Versioning**: no world-state version per se; MapSpec `mutation_revision` + `_cartographic_mutation_revision` in map_state serve as the epochs (used by runtime gates, runtime_bridge.py:632). **Verdict: LEAVE-ALONE.**

---

## Part 2 — Targeted greps

### `affected` / `invalidat` / `compute_affected` / `subgraph`

- `compute_affected_subgraph`: definition workflow_v4/recompute.py:111; production callers runtime_bridge.py:381, resume_verify.py:526; advertised as "唯一受影响子图引擎" (runtime_bridge.py:327, resume_verify.py:470). **No orphan copy exists; no second engine.**
- Downstream-closure variants (capability-level, not typed-DAG): `plan_runtime._downstream_closure` (plan_runtime.py:89) and `workflow_instance._downstream_closure` (workflow_instance.py:629) — duplicate small closures on the PlanGraph shape (candidates to unify under one helper).
- "invalidate" appears once in harness prose (workflow_instance.py:493); the real invalidation contract is ref-level: `ref_lifecycle.py` (single invalidation transition API, ref_lifecycle.py:1-12) + workflow instance STALE propagation.

### `topolog`

- `topological_component_order` — component_graph.py:448 (the only general topo sort in app/).
- Topo-approximate ordering by sorted sets: RecomputePlan.recompute "拓扑近似序" (recompute.py:58, sorted node ids); plan_runtime recompute sorted closure (plan_runtime.py:136).
- WorkflowRuntime V5 driver uses ready-set waves, not an explicit topo sort (driver.py).

### `semantic_key` / `stable_key` / `dedupe_key` — **zero hits** in app/. Existing identity vocabulary instead: `node_id` (typed DAG `<kind>:<name>`), `capability` (PlanGraph), `goal_key` (session_plan.py:96), `plan_id` (planner.py:637 sha1), `row_signature`/`rows_fingerprint` (workflow_instance.py:149/167), `state_fingerprint`/`gate_fingerprint`/`canonical_fingerprint` (workflow_instance.py:129,808), `cache_key` via `make_cache_key` (tool_cache.py), `node_reuse_fingerprint` (workflow_runtime/reuse.py).

### `idempoten` / `receipt` / `side_effect`

- `idempoten`: 12 files — app/core/idempotency.py (HTTP Idempotency-Key middleware, ADR-0138: replay first response 24h, SET NX single-flight, SSE excluded), checkpoint idempotent settle (trace_store), artifact revision idempotency (same content → same row, artifact_revisions.py:6-8), plan save idempotent gates (runtime_bridge/plan_runtime), workflow_runtime CAS re-read idempotent completion (store.py:289-293).
- `receipt`: no hits (no receipt abstraction).
- `side_effect`: no direct hits; the closest is `parallel_safe` ("无副作用/输入独立 → 可并行", typed_dag.py:80) and `parallel_safe=len(deps)<=1 and not optional` heuristic (typed_dag.py:405).

### `revision` + `cas`

- Node-level CAS: workflow_runtime/store.py:271 `transition_node(expected_from, …)`; instance-level CAS on state_revision (store.py:5-7); recompute apply conflict → `cas_conflict` (workflow_runtime/recompute.py:155).
- Monotone revisions without CAS: `state_revision` (workflow_instance.py:283), `runtime_revision` (runtime_bridge.py:582-586), plan `version` (plan_runtime.py:180), MapSpec `mutation_revision` (mapspec_store.py:76), CanonicalPlan.revision guard (planning/store.py:186-198), anchor content_revision snapshots (resume_anchor.py).

### SSE event vocabularies

- Legacy engine (execution_engine.py): `task_start`, `keep_alive`, `token`, `content`, `plan_ready`, `plan_step_done`, `plan_finalized`, `step_start`, `tool_call`, `step_result`, `tool_result`, `step_error`, `step_cancelled`, `task_cancelled`, `task_complete`, `task_error`, `done` (lines listed at execution_engine.py:1821-2566).
- SessionPlan/Pi events (session_plan.events_to_sse :331; consumed in frontend/lib/hooks/use-sse-stream.ts:939-941): `session_plan_updated`, `session_plan_progress`, `session_plan_superseded`, plus `map_finalization` (agent_pi_bridge.py:822) and `resume_gap` (use-sse-stream.ts:1121), `explorer_progress` (:512). Delivery: Pi path caches SSE by toolCallId (`cache_session_plan_sse` agent_pi_bridge.py:311) → mapper flushes.
- WS: `broadcast_ws_event(session_id, event_type, data)` (ws_service.py:73).
- Frontend consumers: use-sse-stream.ts handles the full legacy vocabulary + session_plan_* (:939) + plan_ready/plan_step_done/plan_finalized (:868-917); `use-session-plan.ts` reduces session_plan_* deltas; `session-plan-panel.tsx` renders; `analysis-graph-panel.tsx` polls `GET /sessions/{id}/analysis-graph`.
- **A new Execution Graph event family should reuse the `session_plan_*` additive convention** (Pi+legacy both deliver it; CanonicalPlan names are explicitly forbidden on the Pi path, session_plan.py:333-335).

---

## Part 3 — PR / ADR / review reconciliation

### Open PRs (all baseline `origin/master @ 580b33e9`)

| PR | Claims | Overlap with execution-graph work | Contract seams it exposes |
| --- | --- | --- | --- |
| **#1270** fix(ci): adaptive-wave hygiene + mapspec CLI alias | Fixes red master Backend Tests (jiti alias, watermark 56, conftest env pins, regen artifacts) | None (CI/hygiene) | — |
| **#1273** refactor(qc-loop): 5-round review & optimize convergence | 5-round review over cartography+gis_harness (177 files), P1 fixes, lane coverage ≥51% | Touches gis_harness broadly but fixes only; watch merge conflicts in harness modules | Review findings ledger as comments |
| **#1274** Pi typed tool surface hardening (claims **ADR-0180**) | Per-turn schema byte budget (`apply_surface_byte_budget`, default 32KB, PI_SURFACE_BYTE_BUDGET), Pi pre-dispatch input gate (`pi_input_gate.validate_pi_tool_arguments` before dedup/ref-resolution), surface metrics + parity | Touches `agent_pi_bridge.py`, `tools/registry.py` — the dispatch pre-path a graph executor must respect; new gate runs before any graph dispatch decision | `pi_input_gate`, `pi_surface_metrics` |
| **#1275** GIS Situation / World Model v1 (claims **ADR-0180** — collision!) | New `app/services/gis_situation/**` (facts.py, **diff.py**, compiler.py, consistency.py, contract.py, observation.py, projection.py, queries.py, turn_context.py); per-turn push-compiled structured session context; touches `chat.py`, `pi_turn_context.py`, `ws_service.py` | **Direct conceptual overlap**: its diff module is a second "what changed" facts layer; execution graph node staleness should consume (not duplicate) gis_situation facts. #1276's comment proposes `gis_situation facts → QualificationContext` adapter as follow-up | `gis_situation.diff`, GISSituation contract |
| **#1276** GIS Capability Graph V1 (claims **ADR-0181**) | Activates the orphan V8 graph; four provider projections (recipe/template/component/adapter); `resolve_capabilities` facade into both host planning chains; situation-aware qualification | Touches `planner.py`/`recipes.py`/`tools.py`/`tool_surface.py`; makes qualification/candidates production — Execution Graph nodes should carry its qualification annotations | `resolve_capabilities` |
| **#1277** Pi-native GIS Harness Kernel + SessionPlan v2 (claims **ADR-0180** — collision!) | New `app/services/harness_kernel/` (models/runtime/legacy_adapter/projection/metrics) hoisting apply-orchestration out of the bridge; SessionPlan v2 additive turn/revision/steps/decisions/recovery; K4 CanonicalPlan→SessionPlan one-way projection; K6 `session_plan_step` additive SSE event; touches `session_plan.py`, `agent_pi_bridge.py`, `execution_engine.py`, `chat.py` | **Highest overlap**: same files, same "runtime state on SessionPlan" idea; D-001 "no fifth plan class" — an Execution Graph must position itself relative to harness_kernel's GISSessionRuntime | `harness_kernel.runtime`, `session_plan_step` event |
| **#1278** GIS skill procedure library (claims **ADR-0182** — collision with #1279) | `app/services/gis_harness/skills/**` (contract/_base/catalog/composition/bridges/evidence/benchmark) + YAML libraries incl. `library/benchmark_corpus_v1.yaml`; Skill = reusable domain procedure (ordered steps + decision points + obligations + fallback + completion evidence) between goal and capability | Skill procedures are candidate *sources* of execution graph templates; loose coupling via `SituationLike` protocol | `skills/contract.py` (procedure IR), benchmark corpus |
| **#1279** resource-cost governor (claims **ADR-0182** — collision with #1278) | New `app/services/governor/**` + `config/governor_budgets.json`; admission/budgets/backpressure wrapped ~15 lines inside `tool_dispatch_service.dispatch` + 8-line read-only accounting hook in `context_assembler.py` | Governor sits at dispatch — an executor scheduling parallel waves must request admission through it | governor adapter at dispatch |

Reviewer threads: #1276 has one coordination comment (by WindWang2): file-face zero intersection with #1274/#1275; **"ADR 撞号警告"** that #1274 vs #1275 both occupy 0180 and "两线合并时需有一方改号"; proposes the gis_situation→QualificationContext adapter as first follow-up. #1274/#1275/#1277/#1278/#1279 have no review comments yet. Merged #1271: self-inspection comment fixed 2 gate-blocking issues (TS compile svg2pdf decl; ruff). Merged #1272: M5 closeout note (10-wave ads-v1, wave tags as rollback points) + inspection comment fixing ruff F401s in its tests. **No reviewer notes about planning/execution duplication beyond what's above.**

### ADR numbering

- Master `docs/adr/` ends at **0179** (`git ls-tree origin/master docs/adr/`: …0178-ads-v1-observability-matrix.md, 0179-ads-v1-closeout.md).
- Claimed in open PR branches (verified via `gh pr diff <n> --name-only`): **0180 ×3** — #1274 `0180-pi-typed-tool-surface-hardening.md`, #1275 `0180-gis-situation-world-model.md`, #1277 `0180-pi-native-harness-kernel-sessionplan.md`; **0181** — #1276 `0181-gis-capability-graph-v1.md`; **0182 ×2** — #1278 `0182-gis-skill-procedure-library-v1.md`, #1279 `0182-harness-resource-governor-v1.md`.
- **Recommendation: docs/adr/0183-…** for the execution-graph ADR (first number with zero claims; given two 0182 claimants, one may renumber to 0183 — coordinate in PR description or jump to 0184 if a collision materializes at merge time).

### Established docs/vocabulary

- **docs/research/pi-host-seams.md**: labels ChatEngine-only / Pi-only / Shared / Missing for entry/identity, round planning, tool visibility, prompt/context, SSE, dispatch cache. Key facts: `USE_NEW_AGENT` default True (chat.py `_use_pi_bridge`); Pi deliberately skips classify_followup/should_plan/make_plan (chat.py comment #726); CanonicalPlan+plan-current is ChatEngine-only; **"SessionPlan envelope — Missing" is stale** (session_plan.py now exists; #1275/#1277 PRs both note this); `plan_ready`/`plan_step_done`/`plan_finalized` are ChatEngine-only SSE names; SessionPlan SSE was "Missing" at write time, since delivered as `session_plan_*`. Compression: compute and map-quality are shared; turn planning/prompts/plan SSE are ChatEngine-side; Pi sees one untyped `webgis_execute` proxy. **Seams an Execution Graph must target: shared ToolDispatchService (ADR-0006), shared `session_id` GIS world (ADR-0055), SSE split builders (ADR-0022 — no shared emitter; Pi uses dispatch-cache rendezvous).**
- **docs/gis-harness.md**: harness定位/职责边界; §6 MapProductTemplate/MapProductPlan; "Pi 路径与规划链（#726 审计裁决）" — CanonicalPlan/decision_log legacy-only, `webgis_map_intent`/`webgis_map_product` are the GIS planning tools on the Pi path; V4 sections document WorkflowInstance, SpatialGoalGraph, 18-stage evidence chain.
- **UBIQUITOUS_LANGUAGE.md** (opinionated glossary, wins over older docs): MapSpec, MapSpec generation/fingerprint, **Superseded** (never act on it; avoid "stale" as alias), Validity ladder NOT_EVALUATED→…→SEMANTIC_VALID, Observed Map (only production runtime oracle), CartographicQuality, Cartography Verdict (pass|fail|not_evaluated), **not_evaluated** (never counts as pass), Fail-closed, Inject/Pull, Quality loop, AUTO_SAFE repair, Self-skip, Real-services lane, Perf lane. **Naming guidance for the new work: use "SessionPlan" (not "host plan"), keep the harness red lines (derived projections, single writer `_mark_progress`, fail-closed, bounded); avoid introducing a "plan graph" term that collides — existing code says PlanGraph / typed DAG / goal graph.**

---

## Part 4 — Test infrastructure & scenarios

- **Harness/planning tests**: `tests/unit/gis_harness/` — 87 files. Directly relevant: `test_plan_graph.py`, `test_plan_runtime_v7.py`, `test_typed_dag_v4.py`, `test_workflow_v4_recompute_realform.py`, `test_workflow_v4_semantics.py`, `test_workflow_v4_budget.py`, `test_workflow_v4_production.py`, `test_workflow_instance.py`, `test_runtime_bridge_v6.py`, `test_runtime_state_machine_v7.py`, `test_resume_anchor_v5.py`, `test_analysis_graph.py`, `test_goal_graph.py`, `test_product_graph.py`, `test_compiler_v4.py`, `test_action_intent.py`, `test_repair_planner_v6.py`, scenario/corpus: `test_runtime_corpus_v4.py`, `test_runtime_v4_scenarios.py`, `test_methodology_corpus_v4.py`, `test_conformance_corpus.py`, `test_multiturn_scenarios.py`, `test_product_closure_scenarios.py`, `test_recovery_scenario_v5.py`.
- Planning-package tests: `tests/unit/` planning tests live alongside (e.g. tests/test_chat_engine_planning.py, tests/test_plan_mode_redis.py, tests/unit/core for store/planning modules); workflow_runtime V5 tests: `tests/unit/` + `tests/integration` (search `tests/ -name "*workflow_runtime*"`); session_plan: `tests/test_chat_session_plan_route.py`, `tests/test_pi_session_plan_host.py`.
- **pytest config** (`pytest.ini`): testpaths=tests, asyncio_mode=auto, per-test timeout 60s (thread), `addopts = --cov=app --cov-report=term-missing` (coverage on by default; gate via CI `--cov-fail-under`), markers: `heavy` (pip-only deps, `-m heavy`), `perf` (`-m perf`, baselines refreshed with PERF_UPDATE_BASELINES=1; unfiltered runs self-skip perf), `cartography` (`-m cartography`, deterministic release-blocking closed-loop gate, no Node/LLM/network), `real_services` (REAL_SERVICES=1). No xdist in config (CI chooses). Fastest scoped run: `pytest tests/unit/gis_harness/test_plan_graph.py -q` (plain, no markers needed).
- **Perf corpus patterns**: `tests/perf/` — test_harness_v6_perf_contracts.py, test_geocompute_v6/v7_perf.py, test_runtime_v2_perf_contracts.py, test_semantic_retrieval_perf_v6.py, test_context_assembly_baseline.py. Benchmarks in `tests/benchmarks/` (perf harness behind `-m perf`).
- **Scenario corpora** (generative, deterministic): `app/evaluation/scenario_corpus.py` — V7 ScenarioCorpus: domain packs × parameter slots × deterministic expansion, ≥2000 scenarios gate (MIN_CORPUS_SIZE), per-capability coverage gate, stable case_ids, zero-LLM; also `app/evaluation/runtime_corpus.py`, `quality_corpus.py`; PR #1278 adds `app/services/gis_harness/skills/library/benchmark_corpus_v1.yaml` (not in master). A graph-execution benchmark corpus should follow the ScenarioCorpus pack/slot/gate pattern.
- **Frontend wiring (facts only)**: `frontend/lib/hooks/use-sse-stream.ts` is the single SSE reducer (event vocab at :512-1128, session_plan_* at :939); `frontend/lib/hooks/use-session-plan.ts` reduces session_plan_* deltas into the panel state; `frontend/components/chat/session-plan-panel.tsx` renders the plan/progress; `frontend/components/agent/analysis-graph-panel.tsx` fetches the derived analysis graph via `frontend/lib/api/analysis-graph` ← `GET /sessions/{session_id}/analysis-graph` (`app/api/routes/analysis_graph.py:16`). A graph event stream can reuse the same `session_plan_*` channel + reducer pattern; a richer graph view can extend the analysis-graph REST read model.

---

## Appendix — Key file:line quick reference

| Fact | Citation |
| --- | --- |
| Single writer of row status | app/services/session_plan.py:502 (`_mark_progress`) |
| Pi tool callback trigger chain | app/agent_pi_bridge.py:711,763,772,788,803 |
| Affected-subgraph engine | app/services/gis_harness/workflow_v4/recompute.py:111 |
| Typed DAG node-id namespace | app/services/gis_harness/workflow_v4/typed_dag.py:68,298; runtime_bridge.py:55-62 |
| runtime state block schema `workflow_runtime.v1` | app/services/gis_harness/runtime_bridge.py:533 |
| Plan versioning + replan driver | app/services/gis_harness/plan_runtime.py:63,176,316,378 |
| Instance revision/evidence model | app/services/gis_harness/workflow_instance.py:149,167,181,278 |
| Executable DAG runtime w/ node CAS | app/services/workflow_runtime/store.py:271; driver.py; machine.py |
| Reuse validation (evidence-based) | app/services/gis_harness/runtime_bridge.py:456-531 |
| CanonicalPlan + revision guard store | app/services/planning/models.py:137; planning/store.py:180-198 |
| Resume verify → stale closure | app/services/gis_harness/resume_verify.py:470-526 |
| Ref single invalidation contract | app/services/ref_lifecycle.py:1-12 |
| SessionPlan SSE channel | app/services/session_plan.py:331; frontend/lib/hooks/use-sse-stream.ts:939 |
