# CURRENT ARCHITECTURE — how evaluation fits together at faa453a8

## 1. Primary data flow (ADR-0092 benchmark pipeline)

```
corpus builder (app/evaluation/*_corpus.py, golden_cases.py, case_matrix.py)
        │  deterministic expansion (audited family tables × scope × utterance × lang × data-state)
        ▼
GISBenchmarkCase (pydantic, app/evaluation/case.py)          ← data-only; no planner imports
        │
        ▼
GISBenchmarkRunner.run_case (app/evaluation/runner.py:778)
        ├─ PLAN tier (_run_plan_tier:188)
        │     resolve_map_request_intent(query) → MapProductPlanner.plan_from_intent
        │     asserts: expected_task(s), capabilities (precision/recall), allowed/forbidden
        │     algorithms (prefix sets), expected_recipe(s), product facets, max_tool_calls,
        │     methodology warning patterns + stable warning codes (expected/forbidden),
        │     ontology top-1, tool classes, export formats, context-schema byte budget,
        │     offline (network) contract, V3 qualification/fallback via compile_workflow,
        │     double-run determinism (check_determinism)
        ├─ EXECUTE tier (_run_execute_tier:527, only when script/probes/fixtures declared)
        │     fixture builders → session_data_manager.store (unique session per case)
        │     scripted dispatch via REAL ToolRegistry (args "fixture:<alias>" resolution,
        │     expect_error_contains failure-semantics) → step results + byte blobs
        │     MapSpec assertions (component types), artifact_registry types,
        │     interaction-semantics probes (user-wins, expired-no-remount via
        │     runtime_repair.classify_runtime_repairs), numeric goldens
        │     (step_result/step_result_bytes/mapspec/fixture/quantity × value/len/sum/
        │     first/mean × ==/>/>=/</<=/approx-tol)
        ▼
CaseResult {case_id, group, name, status: pass|fail|skipped, passed, metrics{},
            plan_evidence{}, failures[], skipped_reason, elapsed_ms}
        │
        ▼
report.render_markdown (app/evaluation/report.py)            ← fixed _METRIC_ORDER table;
                                                                None rendered "n/a" (honest)
```

Metrics assembled per case (B3 + V3): `task_correct, capability_precision, capability_recall, algorithm_correct, methodology_honesty_ok, ontology_top1_correct, recipe_selection_correct, no_false_professional_analysis, qualification_states_correct, fallback_tier_correct, planning_deterministic, unnecessary_tool_count, numerical_correct, artifact_contract_valid, map_product_complete, render_verified(=None offline, by design), tool_call_count, retry_count, reused_artifact_count, elapsed_ms`.

## 2. Specialized corpus pipelines (each: frozen data + deterministic driver)

| Pipeline | Case → Driver → Verdict source |
|---|---|
| Workflow contracts (`anti_claim.py`) | `WorkflowContractCase` → `compile_workflow(query, recipe_id, profile)` + `derive_product_verdict` → role/obligation statuses, method/data blockers, warning codes, fallback codes, verdict (READY_WITH_WARNINGS / BLOCKED_BY_METHOD / …) |
| Goal satisfaction (`goal_satisfaction_corpus.py`) | `GoalSatisfactionCase` (chapter/product fact snapshots) → `evaluate_goal_satisfaction` → GoalVerdict (satisfied/partial/blocked/failed/not_evaluated) + per-requirement states; `must_not_pass` rows power **false_pass_rate ≡ 0** |
| Failure taxonomy (`failure_corpus.py`) | `FailureCase` (fault signature) → `classify_harness_failure` + `remediation_for` budget ladder to `abort_with_disclosure` (structural no-infinite-loop proof) |
| Chaos (`chaos_corpus.py`) | `ChaosScenario` → *metadata corpus*; integrity = a gate test that scans the repo asserting every `test_node_id` is really collectable (no fake capability claims) |
| Reliability (`reliability_corpus.py`) | `ReliabilityCase` (`ScriptedCall` tuple) → `simulate_agent_loop` on a real registry → error-code/result-shape/no-progress invariant violations |
| Runtime (`runtime_corpus.py`) | `RuntimeCase.plan_case` → plan tier; `RuntimeExecutionCase` → `simulate_agent_loop` (5 executable situations: tool-failure, missing-data ref, no-progress, multi-turn dataflow, context overflow); `CompositeScenario` (2-turn, expectation codes) is **walkable metadata only** — no runner executes turn 2 conditioned on turn 1 |
| Retrieval (`retrieval_eval_corpus.py`) | `RetrievalEvalCase` → `DynamicToolSurface.select` (semantic channel pinned off for determinism) → `RetrievalEvalReport` (p@1, r@5/10, invalid_selection_rate, fallback_rate, tier3_leak=0, oos_abstention, over_abstention, calibration ECE, by_kind) |
| Tool-retrieval perf (`runtime_metrics.py`) | golden `expected_capabilities` → `AlgorithmRegistry.tool_to_capability` reverse map (single source of truth) → r@k/p@k/irrelevant_rate; plus routing/execution/context metric dataclasses |
| Chain completeness (`chain_gate.py`) | session JSONL (`trace_store.read_chains`) → `evaluate_chain_gate` (ratio >=0.95 with explicit `na_stages`, or `expected_stages ⊆ covered` set gate) — pure function, offline consumer |
| Closed loop (`closed_loop_corpus.py`) | 6-segment scenarios over frozen vocabularies (MAP_TYPE_IDS, FAULT_KIND_IDS, REPAIR_CLASSES, 5 verdict tokens); consumed by `tests/cartography/test_cartography_closed_loop.py` + `tests/unit/test_closed_loop_corpus_v6.py` |
| Replay bench (`app/lib/harness/replay/`) | `Scenario{TurnSpec[ScenarioOp]}` canned production receipts → `OfflineReplayer` (sandboxed mutation store, ref resolver, offline judge env) → expect-tree `{evaluated, passed, reason}` + tolerant ratchet rows; suites all/core/multi-turn/faults; `--baseline` digest/count diff |

## 3. Interfaces to production (what evaluation actually exercises)

- **Planning**: `app/services/gis_harness/intent.py::resolve_map_request_intent`, `planner.py::MapProductPlanner.plan_from_intent`, `gis_ontology.py::match_task_ontology`, `workflow_compiler.py::compile_workflow` (qualify_data 4-state + fallback_v3), `product_facets.py::derive_facet_contract`, `template_catalog.py`.
- **Tools**: `app/tools/registry.py::ToolRegistry` (+`init_tools`), descriptors expose `replay_safe`, `side_effect`, `destructive_level`, `tier`, `model_visible`, `output_semantic_type`, `network`, `schema_size` — the runner reads all of these as assertion surfaces. Dispatch is the *real* production dispatch (`reg.dispatch(tool, args, session_id)`), so offline tools run for real; nothing is a mocked LLM.
- **Session/artifact state**: `app/services/session_data.py::session_data_manager` (MemorySessionStore default), `app/services/mapspec/store.py::mapspec_store_instance`, `app/services/artifact_registry.py::list_artifacts`.
- **Runtime repair**: `runtime_repair.classify_runtime_repairs` — probed inline by runner (`_probe_user_wins` / `_probe_expired_no_remount`, runner.py:707/741) with hand-built chapter/mapspec/observation dicts (this is today's de-facto render-observation stub).
- **Failure taxonomy**: `failure_taxonomy.classify_harness_failure` / `remediation_for`.
- **Goal satisfaction**: `goal_satisfaction.evaluate_goal_satisfaction` (GoalVerdict 5-token).
- **Mission runtime (#1320)**: `app/services/mission_runtime/` — `MissionRuntimeService` facade (create/start/suspend/resume/cancel/complete), `contracts.py` (MissionState machine + `transition_allowed`, `MissionResourceBudget.charge/reserve/release`, MissionRefs, Checkpoint, SwarmRunDurable), `store.py::MissionStore` (SQLAlchemy `GISMissionRow`; **injectable session factory** `MissionStore(factory=...)` for hermetic sqlite tests; wall-clock `_utcnow`, no injected clock), `recovery.py::MissionRecoveryCoordinator`, `resources.py::MissionResourceLedger`, `artifacts.py::MissionArtifactOwnership`, `swarm_bridge.py`. API route: `app/api/routes/mission_runtime.py`.
- **SkillPolicy (#1327)**: `app/services/gis_harness/skills/policy.py` — `SelectionFacts → SkillPolicy.resolve → SkillPolicyDecision`; closed mode vocabulary `{none, guide, execute_guided, shadow, blocked, fallback}`; trust tiers `{core, candidate, experimental, quarantined, deprecated}`; `TRUSTED_PACKS=("core",)`; kill switch env `GIS_SKILL_POLICY=0`; shadow path = induced-skill side-evaluation with zero production writes (`skills/shadow.py`); deterministic same-input-same-decision. Supporting: `resolver.py`, `situation.py`, `contract.py`, `promotion.py`, `planning_projection.py`, `hotpath.py`.
- **Evidence/Claim/Provenance (#1328)**: `app/services/gis_harness/evidence_claim/` — `store.py::ClaimStore`, `claims.py::claim_from_statistic` (typed claims from analysis outputs; LLM prose never authoritative), `contracts.py` (`ClaimStatus` incl. `supported/unsupported/contradicted/stale`, `EvidenceFreshness.STALE`, `ClaimType`, tenant ids, bounded traversal), `verify.py::verify_claim` (verification steps, `positive_proof` flag, tenant isolation, narrative-claims-unsupported), `contradiction.py` (pairwise typed contradiction), `freshness.py` (descendant invalidation, no eager recompute), `graph.py` (RelationGraph), `grounding.py::grounding_projection` (bounded Pi-facing block), `query.py::why_claim`, `narrative.py`, `carto_binding.py`, `census.py`.
- **Hot-path convergence (#1329)**: `app/services/gis_harness/hotpath_convergence/` — opt-in env flags (`GIS_MISSION_HOTPATH`, claim-ingest flag), `mission_bind.maybe_bind_mission_for_turn` (no durable writes when off), `skill_bind.bind_skill_guidance_at_plan_seam`, `claim_ingest.ingest_on_settle/ingest_map_product_settle`, `pi_card.build_hotpath_pi_context`, `session_ctx.get_or_create_claim_store/turn context`. This is the seam a V2 mission-level evaluator should drive instead of inventing a second host.
- **Render/visual/cartography (#1321 + V6 waves)**: `render_observation.py` (pure functions `derive_component_layout_findings` overlap/offscreen, `validate_render_observation` revision-stamped observation validation; observation payload is *data* posted by the frontend — trivially stubbed), `visual_evaluator.py` (seam: `GIS_VISUAL_EVALUATOR=module:callable`, **default off**, whitelist triggers, finding-only output, never touches MapSpec), `app/lib/harness/visual_judge/` (VLM critic, record-only), `app/lib/harness/cartography_feedback.py` (#1321 unified `UnifiedCartographyFeedback`: axes visual / template_codegen / gis_semantics, honest `not_evaluated`, projected into `[CARTOGRAPHY_VERDICT]`), `app/lib/harness/template_codegen_evaluator.py` (schema/compile/composition/component-reuse checks — pure functions over MapSpec), `app/lib/cartography/palettes.py`+`symbology.py` (CVD-aware palettes), cartography quality facts DB (`app/models/cartography_quality.py`, `tests/quality/conftest.py` sqlite harness) + ratchet (`cartography_ratchet.py`).

## 4. Fake-provider / determinism story

- **There is no LLM anywhere in `app/evaluation`** — plan tier is the deterministic rule/recipe planner; execute tier dispatches real offline tools; verdicts are structural. "Fake provider" infrastructure in the classic sense (FakeLLM) is not used by this stack; the equivalent determinism devices are:
  - scripted `ScriptedCall` sequences (`replay.py`, `reliability_corpus.py`, `runtime_corpus.py::_SITUATION_SCRIPTS`);
  - canned production-receipt ops in the replay bench (`ScenarioOp` with `result=` fixtures);
  - seeded fixture builders (`fixtures.py`: `random.Random(42/7/11)`);
  - deterministic offline judge: `tests/harness_replay/replay_judge.py::deterministic_judge` (translates L4 failed_rules into judge-shaped criticisms — no LLM);
  - offline judge env in the replayer (`app/lib/harness/replay/replayer.py::_offline_judge_env`).
- Failure simulation is **signature-based, not sleep-based**: `failure_corpus.py::_exc` constructs `TimeoutError`/`OperationCancelled`/`CRSError` objects fed to the real classifier ("timeout 类用消息标记而非真实等待").
- Known wall-clock surfaces: `runner.py` uses `time.monotonic` only for `elapsed_ms` (never asserted); `MissionStore` uses `_utcnow()` for lease TTLs (deterministic mission eval needs freezing or an injected clock).

## 5. Extension points a V2 factory should reuse (all proven patterns)

1. **Opt-in contract tier on the runner** — `_run_v3_contract_tier` (runner.py:410) shows the house style: new optional case fields, zero-declared = zero-behavior, metrics `None` when not declared, failures appended. V2 tiers (policy/evidence/mission/security) should follow it.
2. **Additive case fields** — the `group` Literal has been extended 3 times already (conformance-*, quality-*, anti-claim); `scenario_kind` free string gives shardable labels without schema change.
3. **Audited-table × deterministic-expansion** — `ConformanceFamily`/`DATA_STATE_PROFILES`/`ScenarioPack` pattern: hand-audited expectation tables, mechanical expansion, build-time guard asserts (dup ids, unique queries, ≤64 profile variants), `sort(key=id)`.
4. **Frozen vocabulary dataclasses** — closed_loop's `MAP_TYPE_IDS`/`FAULT_KIND_IDS`/`REPAIR_CLASSES`/verdict tokens for cross-corpus composability.
5. **Real-entry-point driving** — failure corpus drives `classify_harness_failure`; nothing re-implements production logic in the eval layer. V2 mission/policy/evidence corpora should call `MissionRuntimeService`/`SkillPolicy.resolve`/`verify_claim` directly.
6. **Report**: `_METRIC_ORDER` list is the single place to register new metric columns; `CaseResult.metrics` is a free dict so new keys flow through automatically.
7. **Gates**: `chain_gate.evaluate_chain_gate` (pure function, `expected_stages` subset mode) is the model for V2 gates; `replay/ratchet.py` tolerant ratchet + `tests/quality/structural_baselines.json` show the baseline-diff conventions.
8. **Sharded tests**: `tests/quality/test_quality_scenario_corpus.py::SCENARIO_PREFIXES` slicing keeps full-corpus replay under the 60s per-test timeout — reuse for any ≥thousand-case corpus.
