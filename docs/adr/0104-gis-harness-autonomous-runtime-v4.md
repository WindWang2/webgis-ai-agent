# 0104. GIS Harness V4 — Autonomous Spatial Reasoning & Execution Runtime

**Date:** 2026-09-07
**Status:** Proposed
**Branch:** `feat/gis-harness-autonomous-runtime-v4`
**Baselines:** ADR-0101 (GeoWorkflow Recipe & Conformance), ADR-0103 (Pi GIS Runtime V3),
ADR-0103 (Data/Artifact/Workspace V3)
**Audits:** `.agent-work/harness-v4/01..08` (read-only, master@16d1c70)

## Context

Phase-0 audits confirm the contracts are strong but the production runtime still
re-derives reasoning each turn and re-validates completion wholesale:

1. The 15-stage deterministic workflow compiler is evaluation-only; production uses a
   two-shot planner whose finalize snapshot (`gis_chapter["map_product"]`) is
   re-validated wholesale behind a dedup gate. No instance identity, no monotonic
   state revision, no per-stage evidence versioning, no artifact-staleness
   propagation, and `rows_fingerprint` is blind to parameter/algorithm changes.
   Qualification/obligation blocks do not recompute when data arrives.
2. `DatasetProfileV3` (the richest profile) is produced on demand but never persisted
   or linked (`ArtifactContract.profile_ref` has zero producers); `RefDescriptor`
   carries no CRS (`profile_from_descriptor` hardcodes `crs=None`), so
   projected-CRS scientific gates are dead on the live path; precondition fact keys
   (`numericFields`, `temporalObservationCount`, `bandCount`, …) have zero
   producers; backend selection runs only inside tools.
3. Tool retrieval (V3) ranks on name/tags/domains/capabilities only; descriptor
   fields (scale/latency/memory class, CRS/unit semantics, deterministic flag,
   input_artifacts, fallback_tool) are declared but unused; no prior-failure or
   continuation feedback; the lexical index is keyed on a schema-only fingerprint.
4. Context budgeting is measure-and-advise; only DROP_OLDEST (legacy engine) and
   dispatch-time offload execute. SUMMARIZE does not exist; RELOAD_REF is broken by
   silent LRU eviction (payload+alias+descriptor deleted); no deterministic
   overflow recovery; content-blind folding can drop verdict-bearing messages.
5. The subagent runtime has budgets + fail-closed mutation filtering but 5 of the 9
   V4 specialist roles are missing, the `spawn_subagent` tool exposes no `role`,
   roles lack expected-output/failure-behavior declarations, and there is no
   parallel spawn or budget roll-up.
6. The map observation loop is real but structural-only: chart presence, map-model
   compatibility at completion, zoom-form extent, and positive uncertainty
   disclosure are unverified; the pixel validator remains an honest heuristic
   agent tool and must stay non-gating.
7. The 18-stage evidence chain has production emitters at only 5 stages
   (≈28% completeness); traces are in-process rings with no durable serialization,
   so chain completeness cannot be gated offline.
8. The evaluation corpus is 20,088 plan-tier conformance cases (100% plan_only)
   plus a 306-case intent corpus and 7 E2E scenarios; runtime-tier failure/edit
   categories have no cases and E2E coverage is far below the ≥100 target.

## Decisions

1. **WorkflowInstance is a runtime projection, not a second plan truth.**
   New `app/services/gis_harness/workflow_instance.py` derives a pure, deterministic
   `WorkflowInstance` (instance id `session:envelope:plan`, monotonic
   `StateRevision`, canonical-JSON `StateFingerprint`) from the session chapter +
   bound artifacts + observations, and persists one bounded block at
   `gis_chapter["workflow_instance"]` following the `map_product` persist pattern
   (fail-closed session lock, drift guards, presence-replacement merge). Stages
   project PlanGraph rows; `BlockedReason` maps the five existing blocker
   vocabularies 1:1; `RepairAction` unifies the three existing repair faces as a
   Literal; evidence carries `{fingerprint, source_rows_fingerprint}` so staleness
   is fingerprint mismatch, never a guess. SessionPlan rows stay written only by
   `_mark_progress` (ADR-0076 invariant).
2. **Change events carry recompute dimensions.** A first-class `WorkflowEvent`
   (DATA_ARRIVED / ARTIFACT_PRODUCED / ARTIFACT_STALE / ALGORITHM_CHANGE /
   PARAMETER_CHANGE / STYLE_MUTATION / OBSERVATION / TOOL_FAILURE) classifies into
   the existing `RECOMPUTE_DIMENSIONS` (data/algorithm/parameter/style/output).
   Style events advance presentation state only — science stages are untouched.
   Data/algorithm/parameter events invalidate the downstream dependency closure
   (reverse `infer_dependency_edges`) and re-run the pure qualification/obligation
   evaluators when the bound profile hash changed. Transitions are recorded in a
   bounded per-instance log (reason code + evidence fingerprint + revision).
3. **Invalidation sees parameters and algorithms.** `rows_fingerprint` gains
   `resolved_algorithm` + a canonical hash of per-row params, so a parameter-only
   edit breaks the finalize dedup gate (fixes the "verdict valid forever" hole).
   Same input ⇒ same fingerprint is preserved and pinned by tests.
4. **Profiles become first-class resolver evidence.** `ArtifactContract.profile_ref`
   gains producers (dataset artifacts persist a bounded `DatasetProfileV3` digest via
   the existing session artifact registry path); `RefDescriptor` gains an optional
   `crs` field (additive) and `profile_from_descriptor` stops fabricating
   `crs=None`; `DatasetProfile.to_resolver_profile` becomes the single adapter that
   emits the resolver's fact vocabulary (geometry kind, CRS class, feature count,
   numeric/categorical/temporal fields, null ratios, raster dims/bands);
   plan-time algorithm re-resolution and backend selection consume those facts;
   precondition evaluation refuses to fabricate facts (absent evidence ⇒ `unknown`,
   and `*_field_required` gates degrade to `unknown` instead of false-rejecting
   when the field is known-absent only from the fact bundle's perspective).
   Interpolation-family resolution keys on facts (point count, numeric measure,
   projected CRS, duplicates, distribution, trend, uncertainty requirement,
   compute envelope) with the existing hinted-promotion as the text>facts veto.
5. **Retrieval V4 layers above the registry (no second registry).** `ToolLexicon`
   is enriched from descriptor fields; a pure deterministic rerank
   (phase, artifact semantic types, CRS semantics, scale class, latency/memory
   class, deterministic flag, prior failure/no-progress, continuation stickiness)
   inserts before the V3 contract filter; `ToolSelectionContext` gains three
   optional fields (session artifact types, recent tool outcomes, continuation
   tools); the lexical index is keyed on the full descriptor fingerprint; tier-3
   and destructive gating remain dispatch-time and unreachable from ranking.
6. **Context policy executes the advisor's advice.** New
   `app/services/chat/context_policy.py` applies KEEP/CONDENSE/OFFLOAD_REF/
   SUMMARIZE/DROP_OLDEST/RELOAD_REF at assemble time, before history truncation.
   Safety pins protect confirmation/self-healing/verdict-bearing messages;
   summaries are fingerprinted (canonical content hash) and cached per turn range;
   evicted refs spill a durable copy to the artifact store with a tombstone line in
   the prompt; overflow triggers one deterministic re-trim+retry ladder instead of a
   bare failure. `GisBudgetAdvisor` remains the advice source; the policy is its
   executor, not a second budget model.
7. **Specialist subagent team is a declarative role registry extension.** Five new
   roles (spatial_scientist, algorithm_reviewer, map_observer, result_verifier,
   doc_crosschecker + planner/corpus_worker refinements) declare tool allowlist,
   mutation policy, max rounds/tool calls, heavy-tool budget, wall-time budget,
   model role, expected outputs, and failure behavior; `spawn_subagent` exposes
   `role` (validated; unknown roles fail closed) and `parallel_tasks` (1-6 batch
   routed to `run_parallel`; absent = legacy single-spawn path unchanged); the
   sub-engine's registry is wrapped with a dispatch-membership proxy so the
   allowlist is enforced at the execution boundary, not only at schema visibility
   (review R3); role∩caller constraints stay narrowing-only; bounded parallel
   spawn (semaphore cap 2) with per-parent budget roll-up; recursion/budget/
   cancellation tests pin the semantics. Known limitation: token-level roll-up is
   not enforced (the engine returns no usage data) — tool-call/heavy/wall-time
   budgets are the enforced accounting.
8. **Map observation completes the loop into the verdict.** The finalizer gains
   validators for: required chart/component presence (`chart_required`), map-model
   compatibility audited at completion (not only composition time), extent
   sanity including zoom-form viewports, stale-artifact layer detection, and
   positive methodology/uncertainty disclosure evidence (not just blocked
   obligations). Pixel/screenshot evidence stays out of the gating path and is
   disclosed as heuristic (`heuristic_visual_proxies`), keeping structural vs pixel
   observation honestly separated.
9. **The 18-stage chain becomes complete and durable.** Emitters land at the 13
   missing stages (intent, interpretation, discovery, profile, requirements,
   workflow selection, algorithm resolution, tool selection, artifact production,
   map model, template, components, MapSpec/render/observation/verdict/output as
   applicable per seam); chains serialize to bounded sanitized JSONL alongside
   provenance manifests; `chain_completeness()` becomes an offline regression gate
   wired into replay (`chain_completeness_report`). The gate is exercised over a
   **real-seam scripted scenario** (ToolDispatchService dispatch of the planning
   tools + real finalizer + persistence), with stages that cannot occur in a
   headless scenario (model routing, tool surface, map observation, user output,
   repair-on-clean-run) declared N/A per scenario and disclosed in the report —
   missing emitters are never silently excused. Known limitation: the
   completeness figure over live LLM traffic is measurable by the same report
   once turns persist, but this PR pins the scripted-scenario lane only.
10. **Evaluation corpus adds a runtime tier — with honest composition.** The
    20,088 plan conformance corpus is frozen. The runtime tier has two layers,
    stated separately to avoid inflation (review R3): (a) a **situation-indexed
    plan-identity regression** — 24 audited runtime situations (expectation codes
    + regression-suite traceability) × 8 audited families × scopes × zh/en ×
    utterances = 3,456 cases over 144 unique queries; plan contracts are
    situation-invariant (pinned), and the situation metadata indexes (does not
    fake) the regression suites that lock each behavior; (b) a **real execution
    layer** — 60 scripted cases over the 5 dispatch-observable situations (tool
    failure, missing data, no-progress, multi-turn dependency, large payload) ×
    families × data scales, executed through `simulate_agent_loop` on a real
    registry with error-code/outcome/no-progress assertions. ≥100 E2E scenario
    definitions (7 bases × 9 variants × zh/en) are deterministic turn-script
    records consumable by the existing scenario runner. Combined deterministic
    instances ≥23K; gates stay deterministic (no LLM-judge). Known limitation:
    situations without dispatch-observable semantics (style edits, cancellation,
    provider fallback) are locked by their dedicated suites, not by this corpus.

## Compatibility

- All frozen seams (ToolRegistry, CapabilityRegistry, AlgorithmRegistry,
  SessionPlan, MapSpec, ArtifactContract, ExecutionPlan) receive additive changes
  only; `RefDescriptor.crs` and `ArtifactContract.profile_ref` producers are
  additive optional fields; SessionPlan writers are unchanged; MapSpec mutators are
  unchanged. Contract/parity tests pin: same-input fingerprints, plan-tier corpus
  regressions (306 + stratified conformance sample), tier-3 leak = 0, and the
  `map_product` block readers ignoring unknown keys.
- `rows_fingerprint` content change intentionally breaks stale gates once (old
  persisted snapshots re-validate) — the designed direction, disclosed here.
- Every new runtime surface ships behind a module-level kill switch defaulting on,
  with honest degradation when disabled (e.g. `GIS_WORKFLOW_INSTANCE=0` skips the
  instance pass; `GIS_CONTEXT_POLICY=0` restores advice-only behavior).

## Consequences

- Long tasks stop re-deriving state from prose: the instance block, its revision and
  evidence fingerprints give the LLM and the finalizers a durable, bounded,
  replayable state object per workflow run.
- Profile→resolver wiring makes scientific gates live on the production path and
  makes interpolation-class algorithm selection evidence-driven.
- Known limitations: no pixel-level visual verification (structural observation
  only, honestly disclosed); SUMMARIZE uses extraction + bounded LLM-summary hook,
  not a guaranteed-quality summarizer; RELOAD_REF durability is bounded by the
  artifact store's session TTL; runtime corpus cases are deterministic simulations
  of failure categories, not live provider failures.
