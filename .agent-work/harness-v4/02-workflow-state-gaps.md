# 02 — GIS Harness / SessionPlan / Workflow Compiler State Audit (Wave 1 gaps)

Repo: webgis-ai-agent-harness-v4 @ 16d1c70. Read-only audit. All paths relative to repo root.
Headline: **the codebase already has ~80% of the V4 vocabulary under different names, but it is
split across two planes (session SessionPlan-dict vs project WorkflowRevision-DB) and one
re-evaluation model ("recompute-everything behind a cheap dedup gate"), with no per-node
evidence/revision identity and no artifact-staleness propagation.** None of the target names
(WorkflowInstanceId/WorkflowStage/WorkflowState/DependencyState/EvidenceState/BlockedReason/
RepairAction/CompletionState/StateRevision/StateFingerprint) exist anywhere in `app/` (grep: 0 hits).

---

## (a) Current state model map

### A1. What is compiled, and when

**Production compile (two-shot, in `webgis_map_intent` / `webgis_map_product`):**
- `MapProductPlan` — `app/services/gis_harness/planner.py:191-227`. Fields: `plan_id`
  (deterministic `sha1(query|recipe_id)[:12]`, planner.py:230-234), `intent`, `recipe_id`,
  `template_id`, `data_requirements: [DataRequirement]`, `analysis_steps: [AnalysisStep]`,
  `map_layers: [PlannedLayer]`, `components`, `statistics/charts`, `fallbacks:
  [FallbackDecision]` (recipes.py:101-111, with V2 `downgrade_class`/`disclosure`),
  `status: Literal["draft","finalized"]` (planner.py:208), `completeness`, `eligibility`,
  `algorithm_selections: [AlgorithmSelectionRecord]` (planner.py:167-176),
  `manifest_fingerprint` (planner.py:218), `methodology_warnings` (planner.py:223),
  `workflow_contract: Optional[dict]` (planner.py:227).
  - `DataRequirement.status: Literal["pending","available","unavailable","failed"]`
    (planner.py:146); `AnalysisStep.status: Literal["pending","done","skipped","unavailable","failed"]`
    (planner.py:159); both carry `depends_on: [capability ids]` + `bound_ref` +
    `resolved_tool/resolved_algorithm` + `optional` (planner.py:141-164).
- Shot 1 — draft at intent time: `MapProductPlanner.plan_from_intent` (planner.py:352-611),
  called from `webgis_map_intent` (tools.py:464) and `webgis_map_product` (tools.py:631,
  replaying the same recipe/template for plan continuity). Result dumped as
  `{"plan": plan.model_dump()}` (tools.py:526) and persisted as the SessionPlan `gis_chapter`
  by `apply_tool_result("webgis_map_intent")` (session_plan.py:588-630; supersede on goal-key
  change session_plan.py:594-614, same-goal replace + void-merge session_plan.py:616-630).
- Shot 2 — finalize at product time: `finalize_with_profile` (planner.py:629-956), called at
  tools.py:673. Freezes: eligibility report, semantic fallbacks, `workflow_contract`
  (planner.py:1003-1130 — data-role binding from real bound rows planner.py:1029-1045,
  obligation eval, warnings merge/dedupe planner.py:1050-1069, triggered FallbackDecisions
  planner.py:1071-1111), algorithm re-resolution with profile (planner.py:674-687), layer
  enable/disable + point-layer promotion (planner.py:713-801), component composition
  (planner.py:803-952), `status="finalized"` + `assess_completeness` (planner.py:954-956,
  1155-1202).
- Planner is a shared process singleton with bounded memo keyed on
  `(intent, recipe, template, available_tools, manifest fingerprint)` (planner.py:411-430;
  `get_planner_runtime` planner_runtime.py:39-50). Pure function; no session state (ADR-0076).

**The 15-stage deterministic compiler is NOT in the production path.**
`compile_workflow` (workflow_compiler.py:130-549; stage list COMPILER_STAGES
workflow_compiler.py:35-51: normalize_intent, map_task_ontology, resolve_task_family,
resolve_scope, resolve_recipe_candidates, resolve_data_roles, qualify_data, plan_candidates,
compile_capability_dag, evaluate_obligations, resolve_algorithms, compute_transformations,
resolve_cartography, produce_map_product_plan, produce_completion_contract) producing
`WorkflowCompilation` (workflow_compiler.py:76-118) with per-stage `WorkflowStageRecord`
(status ok|skipped|blocked, workflow_compiler.py:57-73) is invoked **only** from the offline
evaluation harness: `app/evaluation/runner.py:379` and `app/evaluation/anti_claim.py:237`.
Production `webgis_map_intent`/`webgis_map_product` call the planner directly, skipping
qualify_data/plan_candidates/fallback_tier stages.
- Workflow contract layer (declarations, not a runtime): `WorkflowProfile` +
  `DataRoleRequirement`/`ScientificObligation`/`CompletionRequirement`/`WorkflowFallbackPolicy`
  (workflow_schema.py:104-180), `DataRoleResolution` (workflow_schema.py:185-197: status
  unresolved|bound|external|degraded), `ObligationEvaluation` (workflow_schema.py:213-222:
  status satisfied|warning|degraded|blocked|unknown), `WorkflowContractReport`
  (workflow_schema.py:236-249, with `method_blockers`/`data_blockers`), vocabularies
  DATA_ROLES (workflow_schema.py:34-50), COMPLETION_DIMENSIONS 7-dim (workflow_schema.py:74-82),
  RECOMPUTE_DIMENSIONS (workflow_schema.py:89), DOWNGRADE_CLASSES (workflow_schema.py:86).
- Multi-candidate + family layers: `generate_plan_candidates` (plan_candidates.py:39-48
  scoring; reroute only when top-1 scientifically blocked, workflow_compiler.py:300-322),
  WorkflowFamily/CompositeRecipe/ScenarioTemplate (workflow_families.py:33-100), registry
  cross-validation (registry_validation.py:31-50 `validate_gis_library`).

### A2. Where the "finalize snapshot" lives

Two snapshots, one session-plane, one project-plane:
1. **Session plane (the live one):** `gis_chapter["map_product"]` block, written by
   `maybe_finalize_map_product` → `map_product_block` (completion/pipeline.py:351-399,
   592-602). Freezes: `MapCompletionResult.to_dict()` (status/findings/repairs/viewport/
   layer/component/export/render/final_map_status, completion/contracts.py:332-382),
   `checked_revision` (MapSpec mutation revision), `render_observation_seq`,
   `rows_fingerprint`, `repair_memory` (≤32), and `product_verdict` (+ 7-dim
   `completion_dimensions`, contracts.py:211-280). Written under the per-session lock with
   four mid-run drift guards (goal supersede, revision moved, rows changed, observation
   advanced — pipeline.py:539-582).
2. **Project plane (durable):** `MapProductVersion` + `WorkflowRun`/`WorkflowRevision`
   (app/models/project.py:106,147). `WorkflowEngine._ensure_revision` appends an immutable
   graph revision keyed by `graph_fingerprint` (workflow_engine.py:213-274);
   `compute_product_fingerprint` hashes inputs+compute-plan+mapspec+outputs
   (map_product_service.py:65-79). Promoted from SessionPlan by the deterministic converter
   `build_workflow_recipe` (workflow_promotion.py:80-133, 208-245; gate
   `promotion_blockers` workflow_promotion.py:179-205 — every required row must be
   available/done; `bound_ref`/`status` deliberately dropped as session-bound, :37-45).

**Incremental re-evaluation: none.** The finalizer re-runs the whole validator suite per
trigger, gated by an idempotence check (`_dedup_gate_blocks` completion/pipeline.py:298-328):
skip iff stored terminal AND `checked_revision` == current `_cartographic_mutation_revision`
AND `render_observation_seq` equal AND `rows_fingerprint` equal (pipeline.py:495-499).

**Update triggers today:**
- new artifact / tool result → `apply_tool_result` → `_mark_progress` flips capability rows
  (session_plan.py:481-675, 442-478) → rows_fingerprint changes → gate breaks → re-finalize.
  Also called on dispatch failure: rows → `failed` (session_plan.py:570-586).
- turn settle → `maybe_finalize_map_product(final_gate=True)` (agent_pi_bridge.py:2156-2160);
  per-tool-result trigger agent_pi_bridge.py:724-726.
- render observation POST → finalize + bounded `run_runtime_repair` (chat.py:1609-1655).
- style/component edit (MapSpec mutation intents, lifecycle_engine.py:188-345) → bumps
  `_cartographic_mutation_revision` → gate breaks → re-validate **mapspec only** (rows
  untouched ⇒ no science re-eval). `webgis_component_update` documents "never triggers data
  re-query/re-analysis" (tools.py:1178-1186 region).
- data-arrival for **qualification/obligations**: NOT recomputed — data-role/obligation
  states are re-derived only when `webgis_map_product` re-invokes `finalize_with_profile`
  (planner.py:1003) or the eval-only compiler re-runs stage 6/7/9. New data reaching a
  capability row does not itself re-open a `blocked` qualification.
- registry generation change → `session_plan_stale` STALE_PLAN note (session_plan.py:157-174;
  runtime_manifest.is_stale_plan app/lib/gis/runtime_manifest.py:103-116).

### A3. Dependency expression & invalidation

- Declared: `depends_on` on plan rows, filled at plan time by
  `infer_dependency_edges` — registry artifact-type intersection `A.out ∩ B.in ⇒ A→B`,
  cycle-skipping (plan_graph.py:173-203; persisted planner.py:307).
- Derived: `build_plan_graph` / `PlanGraph._evaluate` (plan_graph.py:254-474) — pure,
  re-evaluated on every read: ready derivation, mandatory-dep propagation (`blocked_by`
  on unavailable, plan_graph.py:423-450), optional-failure absorption (plan_graph.py:396-397),
  capability-fallback unlock (plan_graph.py:398-401), and **blocked-recovery** (blockers all
  satisfied → back to pending, plan_graph.py:452-458). This is the one place where "blocked
  states auto-recompute when facts change" already holds — but only for DAG row states.
- Invalidation propagation downstream of a changed/stale **artifact**: does not exist.
  What exists: per-session `ArtifactRegistry` lifecycle
  `valid→superseded→stale→expired→failed` (artifact_registry.py:48-53, sweep_statuses :679)
  with `ArtifactRecord.inputs/replaces/revision` (artifact_registry.py:84-104);
  facet lineage liveness + `recompute_capabilities` + `reusable_inputs`
  (product_lineage.py:55-76, 105-136, 267-292); runtime repair refuses to remount dead refs
  and emits execution debts (runtime_repair.py:213-219); action layer upgrades dead-source
  repair to `retry_capability` (action_intent.py:240-255). But nothing marks downstream
  capability rows/artifacts stale when an upstream artifact changes — rows only move via
  `_mark_progress` on tool results.

### A4. Style-only vs science-affecting

- Vocabulary exists twice: `RECOMPUTE_DIMENSIONS = ("data","algorithm","parameter","style","output")`
  (workflow_schema.py:89) declared per recipe via `recompute_dimensions`
  (workflow_schema.py:179; recipe_packs e.g. distribution.py:276 `["data","parameter","output"]`),
  and `_FACET_RECOMPUTE_DIMS` per facet kind (analysis_graph.py:33-41 — legend/annotation are
  `["style"]`-only).
- Behavior exists in the project plane: `MapProductService.diff_versions` five booleans +
  `analysis_recomputation_expected = data|algorithm|parameter` and style-only proof modes
  (map_product_service.py:1-20 docstring, 587-655; `style_only` restore :441-449; disjoint
  merge rule :516-549).
- Behavior in the session plane is **implicit**: separate dedup-gate keys — rows
  (science) vs checked_revision (presentation) vs render seq (observation) — mean style
  mutations re-validate presentation only (pipeline.py:331-348, 495-499). There is no
  first-class "change event with a dimension" anywhere in the session runtime.
- **Hole:** `rows_fingerprint` is `capability:status:bound_ref` only (pipeline.py:340-348) —
  parameter/args or algorithm changes on a row do NOT change it, so a parameter-only edit
  does not invalidate the stored verdict.

### A5. State/status enums inventory (file:line → values)

| Enum/Literal | Location | Values |
|---|---|---|
| ProgressStatus | app/services/session_plan.py:56 | pending, complete, voided, unavailable, failed |
| DataRequirement.status | gis_harness/planner.py:146 | pending, available, unavailable, failed |
| AnalysisStep.status | gis_harness/planner.py:159 | pending, done, skipped, unavailable, failed |
| PlanNodeStatus | gis_harness/plan_graph.py:51-58 | pending, ready, running, complete, skipped, unavailable, failed |
| MapProductPlan.status | gis_harness/planner.py:208 | draft, finalized |
| AlgorithmSelectionRecord.status | gis_harness/planner.py:171 | resolved, unavailable |
| PlannedLayer.role | gis_harness/planner.py:181 | primary, secondary, reference |
| WorkflowStageRecord.status | gis_harness/workflow_compiler.py:60 | ok, skipped, blocked |
| DataRoleResolution.status | gis_harness/workflow_schema.py:190-193 | unresolved, bound, external, degraded |
| ObligationEvaluation.status | gis_harness/workflow_schema.py:217-218 | satisfied, warning, degraded, blocked, unknown |
| QUALIFICATION_STATES | gis_harness/data_qualification.py:30-32 | eligible, transform_required, degraded, blocked, unknown |
| FALLBACK_TIERS | gis_harness/fallback_v3.py:28 | preferred, degraded, minimal, blocked |
| DOWNGRADE_CLASSES | gis_harness/workflow_schema.py:86 | equivalent, approximation, proxy, degraded, not_allowed |
| Finalization status | gis_harness/completion/contracts.py:30-33 | pending, needs_repair, complete, failed |
| render_status | completion/contracts.py:87-91 | verified, issues, stale, unknown, not_applicable |
| final_map_status | completion/contracts.py:97-100 | verified, verified_with_degradation, failed, unknown |
| Product verdict | completion/contracts.py:105-109 | READY, READY_WITH_WARNINGS, NEEDS_REPAIR, BLOCKED_BY_DATA, BLOCKED_BY_METHOD |
| Artifact status | app/services/artifact_registry.py:48-53 | valid, stale, expired, superseded, failed |
| Facet status | gis_harness/product_graph.py:41-45 | done, pending, failed, ready, off |
| Repair actions (finalizer) | completion/contracts.py:284-286 | add_component, enable_component, show_layer |
| Repair actions (runtime) | gis_harness/runtime_repair.py:63-65 | reassert_spec_layer, restore_expected_visibility, reassert_component |
| GISActionIntent vocab | gis_harness/action_intent.py:65-107 | run/retry_capability, produce_layer/chart/statistics, repair_runtime_layer, reassert_mapspec, reobserve, finalize_product × execution_mode (capability/runtime_repair/observation/finalization) × action_class (execution/product/runtime_repair/observation/finalization debts) |

### A6. Fingerprint / revision inventory

| Concept | Location |
|---|---|
| `recipe_content_fingerprint` (sha256 canonical dump incl. workflow V2) | workflow_schema.py:647-660; cached recipes.py:763-764, 820-868 |
| runtime `manifest_fingerprint` + `is_stale_plan` | app/lib/gis/runtime_manifest.py:21,70,103-116; stamped planner.py:440; disclosed tools.py:132-143,1146-1147; consumed session_plan.py:157-174 |
| registry/ontology/family content fingerprints | recipes.py:820; intent.py:649; gis_ontology.py:1571; workflow_families.py:573 |
| `rows_fingerprint` (chapter rows) | completion/pipeline.py:331-348 ([:512] at :384-385, :495-499, :562) |
| MapSpec `_cartographic_mutation_revision` / `_current_cartographic_fingerprint` | map_state keys; read gis_world_state/state.py:170,182; CAS `expected_revision` tools.py:127-129, runtime_repair.py:392-423 |
| render observation revision + server-side stamping | render_observation.py:13-20, 91-95, 143-153 |
| repair ledger fingerprinting (observation + action-set) | runtime_repair.py:90-99, 343-355 |
| source `data_fingerprint`/`profile_fingerprint` in MapSpec sources | app/services/mapspec_store.py:186-196 |
| artifact `revision`, `replaces`, lineage inputs | artifact_registry.py:84-104 |
| Project plane: dataset/graph/run/content fingerprints | app/services/provenance/fingerprint.py:44,63,84,145; append-only `WorkflowRevision.revision_no` + `graph_fingerprint` workflow_engine.py:213-274 |
| Project plane product fingerprint + 5-dim diff | map_product_service.py:65-79, 587-655 |

### A7. Qualification (data_qualification.py) — checks & emissions

Per data role, deterministic, five-state (`QUALIFICATION_STATES` :30-32). Checks:
geometry-kind match (:338-370), numeric measure field via algorithm-layer precondition
`numeric_field_required` (:373-390, delegation rule :234-254), denominator strong-hint fields
(:393-415, hints imported from workflow_schema:85-88), null_ratio > 0.5 (:418-438), sample
size ≥ 1 → `EMPTY_DATASET` blocked (:441-450), projected-CRS only when a workflow obligation
declares it (:453-467), temporal dimension fact (:470-472). Resolution-state short-circuits:
unresolved+block → blocked, unresolved+degrade → degraded, external → unknown
(EXTERNAL_ACQUISITION_UNVERIFIED, :292-334). Deterministic convergence (:474-501).
Emits `DataQualification{state, reason_code, detail, remediation[], checks, confidence}`
with `RemediationStep{operation ∈ REMEDIATION_OPS :44-53, target, params, reason_code,
disclosure, auto_applicable, confidence}` (:150-169). Consumers: compiler stage 7
(workflow_compiler.py:250-293), auto-applicable remediations materialized as transform steps
(workflow_compiler.py:432-446), fallback tier (fallback_v3.py:61-156), candidate scoring
(plan_candidates.py:42-48).

### A8. Repair (runtime_repair.py) + finalizer repairs

`classify_runtime_repairs` (runtime_repair.py:131-253): render_layer_missing with live
source → `reassert_spec_layer` (UpsertLayerIntent); mounted-but-invisible with spec-visible +
not user-owned → `restore_expected_visibility` (PatchLayerPresentationIntent batch); required
component unmounted → `reassert_component`; confirmed-dead source → **execution debt** (never
remount, :213-219); user-owned → no-op disclosure. Bounded: `MAX_RUNTIME_REPAIR_PASSES = 2`
(:57), ledger per observation-fingerprint in `map_state["_runtime_repair_state"]` (:343-349),
CAS `expected_revision`, stale observation ⇒ empty plan (:150-151, 317-320).
Finalizer desired-state repairs: `add_component` / `enable_component` / `show_layer`
(completion/repairs.py; codes contracts.py:284-286), ≤ `MAX_FINALIZATION_PASSES = 2`
(contracts.py:15), cross-pass `repair_memory` (≤32, pipeline.py:351-358).

### A9. Completion contracts (completion/)

Answers "is the final map product done" (completion/__init__.py:1-46): validate (execution /
artifacts / layers / components / semantics / layout validators) → bounded repair →
re-validate; render observation validation appended (pipeline.py:165-181); V3 final-map
verification aggregated after status freeze (pipeline.py:204-279). Status taxonomy
pending → needs_repair/failed/complete (contracts.py:30-33). `evaluate_completion_contract`
derives the 7 C7 dimensions from result + `chapter.workflow_contract` + methodology warnings
+ not_allowed fallbacks (contracts.py:131-208); `derive_product_verdict` folds them into the
5-value verdict with BLOCKED_BY_DATA/BLOCKED_BY_METHOD overriding READY (contracts.py:211-280).
Evaluated after every tool result (agent_pi_bridge.py:726), at turn settle with
`final_gate=True` (agent_pi_bridge.py:2158), and after each render observation (chat.py:1611).
Planning-time counterpart: `compilation.completion_contract` with honest `None` (unknown)
non-data/science dims (workflow_compiler.py:510-535).

### A10. Persistence + update path (the seam)

- `SessionPlan` envelope, SessionStore alias `session-plan` (session_plan.py:23-26, 67-78):
  `gis_chapter` = MapProductPlan **dict**, `progress: [CapabilityProgress{capability, status,
  bound_ref}]` (:59-64). Load/save :284-320; creation double-checked-lock :323-353; every
  mutation under per-session fail-closed lock with `lock.lost` guards :506-568.
- Single writer: `apply_tool_result` (session_plan.py:481-675) — intent supersede/replace,
  product merge (`merge_map_product_result` presence-replacement protocol: key present ⇒
  replace, empty list is evidence, :518-545), per-tool capability completion.
- Read-side projections, all pure (no second truth): `format_session_plan_projection`
  (session_plan.py:177-268) → plan_graph block + product graph line + action-intent line;
  `build_analysis_graph` (analysis_graph.py:173-247); facet graph/lineage/verdict as above.

---

## (b) Gap table — V4 target → existing reuse point or missing

| V4 target | Existing reuse point (file:line) | Gap |
|---|---|---|
| WorkflowInstanceId | `SessionPlan.envelope_id` (session_plan.py:70) + `plan_id` (planner.py:230); project: `WorkflowRun` id + `WorkflowRevision.revision_no` (workflow_engine.py:213) | No stable id for "the running workflow instance" as opposed to plan draft; same-goal replace keeps envelope id but swaps chapter without an instance counter; supersede archives old envelope (session_plan.py:594-614) — usable lineage hook |
| WorkflowStage | `COMPILER_STAGES`/`WorkflowStageRecord` (workflow_compiler.py:35-73) = compile stages, not lifecycle stages; recipe `analysis_steps` rows = steps | Missing: durable stage identity. Compiler output is eval-only and never persisted to the session |
| WorkflowState | MapProductPlan.status draft/finalized (planner.py:208) + finalization 4-status (contracts.py:30-33) + verdict 5-value (contracts.py:105-109) + DAG 7-node-status (plan_graph.py:51) | No single machine that composes plan×execution×product; states live in 4 unrelated vocabularies; "complete" means different things per vocabulary |
| DependencyState | `depends_on` + PlanNode `blocked_by`/`fallback_to`/`input_refs` (plan_graph.py:87-99, 368-474) with blocked-recovery | Missing: dependency on *artifact revision* (only type-level). No propagation of upstream artifact staleness into downstream DependencyState |
| EvidenceState | `bound_ref` rows (session_plan.py:442-478), `ArtifactRecord` status+liveness (artifact_registry.py:48-53; product_lineage.py:55-76), obligation/qualification evidence dicts (workflow_schema.py:199-233; data_qualification.py:183-192) | Evidence exists but unversioned & unkeyed to the stage that consumed it; no per-node "evidence revision" ⇒ cannot answer "is this stage's evidence still current" |
| BlockedReason | `data_blockers`/`method_blockers` (workflow_schema.py:246-248), `blocked_by` (plan_graph.py:99), fallback `blocked_reasons` (fallback_v3.py:39), qualification `reason_code` (data_qualification.py:176), finding codes (contracts.py:36-77) | Rich reason codes but scattered across five vocabularies; no enum unifying BLOCKED_BY_DATA vs DAG-blocked vs render-blocked |
| RepairAction | 3 runtime actions (runtime_repair.py:63-65) + 3 finalizer actions (contracts.py:284-286) + 9 action-intent actions with mode/class (action_intent.py:65-107) | Split across three repair faces (map_completion desired-state, cartography AUTO_SAFE, runtime_repair — see runtime_repair.py:1-29); no shared RepairAction type or budget ledger keyed by state revision |
| CompletionState | 7-dim contract eval (contracts.py:131-208) + verdict + `final_map_status` | Dim states are recomputed wholesale per trigger; no persistence of per-dimension evidence revision; planning-time dims are `None`-unknown only in the eval-only compiler (workflow_compiler.py:510-519) |
| StateRevision | `_cartographic_mutation_revision` (state.py:170), `WorkflowRevision.revision_no` (workflow_engine.py:245), `ArtifactRecord.revision`, `render_observation_seq` (pipeline.py:369) | No revision for the *workflow/plan* itself: rows_fingerprint is content-ish but not monotonic; nothing increments when a capability row changes |
| StateFingerprint | `rows_fingerprint` (pipeline.py:331-348), `manifest_fingerprint`, `recipe_content_fingerprint`, mapspec fingerprint, action fingerprint | No single instance-level fingerprint; rows_fingerprint ignores params/algorithm (real hole, §A4); mapspec fingerprint is style-plane only |
| Behavior: blocked auto-recompute on data arrival | DAG blocked-recovery is read-time pure (plan_graph.py:452-458) — free | Qualification/obligation blocked states do NOT recompute on data arrival (only on `webgis_map_product` re-run / eval compiler). Need event: row bound → re-run stage 6/7/9 |
| Behavior: staleness invalidates only affected downstream | Facet lineage `recompute_capabilities` + `reusable_inputs` (product_lineage.py:105-136); execution debts (runtime_repair.py:213-219); dead-ref retry upgrade (action_intent.py:240-255) | Missing: closure computation marking exactly the downstream capability rows/artifacts stale when an upstream artifact changes/expires; today only dead-render debt is detected |
| Behavior: style-only ≠ science recompute | Separate dedup-gate keys (pipeline.py:331-348); 5-dim diff project-side (map_product_service.py:587-655); component-mutation guarantee (tools.py:1178-1186); mutation intents never touch rows | Missing: first-class change classification in the session runtime (which dimension changed, computed where?); rows_fingerprint blind to parameter/algorithm edits |
| Behavior: algorithm/parameter/data change → correct dims | Vocabulary + per-facet dim maps (workflow_schema.py:89,179; analysis_graph.py:33-41); diff computes dims project-side | Not computed session-side; no consumer that maps a dim to a set of stages to invalidate |
| Deterministic transitions | All evaluators are pure functions (plan_graph._evaluate, contracts, fallback_v3, qualification); planner memo (planner.py:411-430) | Transition *ordering* is implicit (per-trigger full recompute); no recorded transition log/trace per instance (trace.py is global counters, stage-level only :256-281 runtime_repair) |
| Bounded traceable evidence | Budget constants everywhere (_STAGE_* workflow_compiler.py:53-54; MAX_FINDINGS/MAX_* contracts.py:15-21; to_bounded_dict pattern) | Evidence not assembled per-instance into one bounded, replayable record (WorkflowCompilation is the closest, eval-only) |
| Stable fingerprints | All hashers are canonical-JSON sha256 (workflow_schema.py:647; fingerprint.py:44-79; map_product_service.py:65) | Multiple incompatible canonicalizations; V4 should standardize on `canonical_dumps` + sha256 |

---

## (c) Recommended integration seam (additive)

1. **Do not fork compile truth — attach WorkflowInstance as an additive `gis_chapter` key**, exactly
   like `map_product`: new module `app/services/gis_harness/workflow_instance.py` that
   (i) reads the chapter dict + bound facts, (ii) derives the instance state as a **pure
   function**, (iii) writes a single bounded block `gis_chapter["workflow_instance"]` under
   the existing session lock, reusing the `map_product` persist pattern (lock, goal guard,
   revision guard, rows guard — copy pipeline.py:539-582) and the presence-replacement merge
   protocol of `merge_map_product_result` (session_plan.py:518-545). ADR-0076 invariant
   ("SessionPlan is plan truth", docs/adr/0076) is preserved: instance block is projection+
   derived-state, rows remain written only by `_mark_progress`.
2. **Trigger/recompute seam = the existing dedup-gate keys.** A WorkflowInstance transition
   pass should run where `maybe_finalize_map_product` runs (tool result bridge
   agent_pi_bridge.py:724; turn settle :2156; observation POST chat.py:1609), keyed by
   (rows_fingerprint, checked_revision, render_seq) so blocked-recompute and downstream
   invalidation re-derive only when inputs changed. Extend `rows_fingerprint`
   (pipeline.py:331-348) to include `resolved_algorithm` + a hash of `params` — this is the
   minimal fix that makes parameter/algorithm changes visible to invalidation.
3. **Reuse, don't invent:**
   - WorkflowInstanceId := `f"{session_id}:{envelope_id}:{plan_id}"` (envelope_id already
     changes on supersede; plan_id is content-derived). Optionally a monotonically increasing
     `StateRevision` counter stored in the instance block, incremented per transition pass.
   - WorkflowStage := PlanGraph nodes (plan_graph.py:80-101) + stage rows; WorkflowState :=
     fold of PlanNodeStatus + finalization STATUS_* + product_verdict via
     `derive_product_verdict` (already a pure fold).
   - DependencyState := per-edge projection of PlanGraph `_sat` logic + ArtifactRecord
     liveness (`_liveness_of` product_lineage.py:55-76); downstream closure from
     `infer_dependency_edges` reversed — pure function, no new truth.
   - EvidenceState := wrap existing evidence dicts (obligations/qualifications) with
     `{fingerprint, source_rows_fingerprint}` so staleness = fingerprint mismatch.
   - BlockedReason := single enum unioning `data_blockers`/`method_blockers`
     (workflow_schema.py:246-248), `blocked_by` (plan_graph.py:99), `F_EXECUTION_BLOCKED`
     (contracts.py:40); map legacy codes 1:1.
   - RepairAction := extend the runtime_repair vocab (runtime_repair.py:63-65) with the
     finalizer codes and the action-intent mode/class (action_intent.py:73-107) into one
     Literal; budget ledger already exists (REPAIR_STATE_KEY pattern).
   - StateFingerprint := sha256(canonical_dumps(rows+deps+evidence-refs)) reusing
     provenance/fingerprint.py:44.
   - Data-arrival recompute: on `apply_tool_result` complete with bound_ref, re-run stages
     6/7/9 equivalents (`resolve_data_roles`/`qualify_workflow_data_roles`/
     `evaluate_workflow_obligations` — all pure, workflow_schema.py:387-642) against the new
     profile; they already handle bound_refs from real rows (planner.py:1029-1045).
4. **Promote the 15-stage compiler into production** by having `webgis_map_intent` /
   `webgis_map_product` call `compile_workflow` (or a split `compile_plan` / `compile_finalize`)
   and persist `WorkflowCompilation` fragments — otherwise every V4 guarantee is eval-only.
   The planner calls inside compile_workflow (workflow_compiler.py:343-351) already match the
   production call sites (tools.py:464/631/673), so this is a wiring change, not a fork.
5. **Project-plane parity:** keep `workflow_promotion` as the only bridge to WorkflowRevision;
   add `manifest_fingerprint`/instance fingerprint to promotion metadata (already carries it,
   workflow_promotion.py:169) so a promoted recipe knows which StateFingerprint generation it
   reproduces.

---

## (d) Risks

1. **Two truth planes.** Session chapter is an untyped dict (schema drift from
   `MapProductPlan` pydantic is possible); project plane is typed+DB with its own revision
   machinery. An instance model spanning both risks a third truth. Mitigate: instance block
   derived only from session chapter + artifact descriptors; promotion stays one-way
   (workflow_promotion.py:1-17 "projection, not a second planner").
2. **`_mark_progress` writes three places at once** (progress rows + data_requirements rows +
   analysis_steps rows, session_plan.py:442-478) — two copies of the same status. V4 must
   treat the chapter rows as canonical (plan_graph reads those) and progress as envelope
   metadata, or the instance state will disagree with projections.
3. **Gate-key blindness.** `rows_fingerprint` excludes params/algorithm (:340-348); a
   parameter edit today leaves the final verdict "valid" forever. Fixing the fingerprint
   changes gate behavior (more re-validations) — bounded but measurable.
4. **Full recompute per trigger.** Finalizer re-runs all validators whenever any key changes;
   adding instance transitions to the same triggers multiplies work on chatty turns. Keep
   instance pass O(nodes) pure and behind the same gate; never re-run qualifications unless
   the bound profile actually changed (hash the resolver profile).
5. **Concurrent-writer discipline.** Session lock is fail-closed on degraded locks and every
   writer checks `lock.lost` (session_plan.py:506-568); a new instance writer must follow
   exactly this protocol or reintroduce last-write-wins on Redis.
6. **Verdict coupling.** `derive_product_verdict` (contracts.py:211-280) is the machine
   contract consumed by frontend/final-gate; folding WorkflowState into it must stay additive
   (new keys only) or dashboards/tests break — precedent: `map_product` block keys are
   additive and old readers ignore them (pipeline.py:351-399).
7. **Vocabulary unification risk.** At least 8 overlapping status vocabularies (§A5);
   naively "unifying" them will break pinned tests (plan_graph._ROW_STATUS_TO_NODE,
   product_graph._ROW_STATUS_TO_NODE mapping tables are behavior). Prefer adapter enums that
   project onto existing values.
8. **Style-plane mutation volume.** Every layer drag/hide bumps `_cartographic_mutation_revision`
   and breaks the gate (re-finalize); an instance model keyed on the same revision will
   re-derive often. Style-only mutations should advance a *style* revision only — which is
   exactly the 5-dim classification the session plane lacks (build it before wiring
   StateRevision to the mutation counter).
