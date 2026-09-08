# GIS Harness V4 — Autonomous Spatial Reasoning & Execution Runtime
# Implementation Plan (living document)

Branch: `feat/gis-harness-autonomous-runtime-v4`
Worktree: `../webgis-ai-agent-harness-v4`
Baseline: `origin/master@16d1c70`

## What V3 already delivered (from ADRs, to be verified against code)

- ADR-0101: Workflow Recipe DSL V2 (WorkflowProfile on CartographyRecipe), 164 recipes /
  24 domain packs, data roles (15 vocab), scientific obligation →
  `scientific_preconditions.evaluate_precondition` (five-value verdict), deterministic
  12-phase workflow compiler, completion contract V2 (7 dimensions), fallback taxonomy,
  3,240 plan conformance corpus.
- ADR-0103 (Pi GIS Runtime V3): ToolDescriptor V3 (rich metadata), capability provenance
  from AlgorithmRegistry, dynamic tool surface V3 (`tool_surface_v3.py` — lexical baseline +
  capability retrieval + contract filter + rank + schema projection), model router +
  provider health, GisBudgetAdvisor (advice-only context budgeting, 12 categories),
  GisProgressTracker (no-progress: unchanged_map / unchanged_workflow / repeated_planning),
  18-stage evidence chain skeleton (`gis_trace.py`) with emitters at routing+dispatch seams,
  replay V3 (tool-surface A/B, chain coverage, route diff), runtime_metrics.
- ADR-0103 (Data/Artifact/Workspace V3): `app/lib/data/` ArtifactContract V3,
  FingerprintSet + classify_change + staleness_verdict, LifecycleState algebra,
  bounded Welford profiling (`app/lib/data/profile.py`, 50k gate), quality taxonomy,
  lineage at dispatch seam, content-evidenced reuse, catalog federation, snapshot verify.

## V4 delta (what this branch adds) — per wave

- W1 WorkflowInstance: runtime state machine *projection* over SessionPlan +
  WorkflowProfile; incremental re-evaluation on artifact/data/style events;
  recompute dimensions (style-only ≠ science); StateRevision + StateFingerprint;
  BlockedReason taxonomy wired to data_qualification + preconditions.
- W2 Data→Resolver: DatasetProfile → role binding → preconditions → ScaleProfile →
  AlgorithmResolver → BackendSelection → ExecutionPlan seam; interpolation case study
  (IDW/RBF/TIN/OK/UK/RK by evidence not keywords).
- W3 SpatialGoalGraph: serializable bounded diffable goal graph; deterministic expander;
  reconciles with SessionPlan chapters (projection, not second truth).
- W4 Tool Retrieval V4: phase/artifact/failure-aware ranking atop tool_surface_v3;
  registry-fingerprint index invalidation; ≥2000-case retrieval corpus + metrics.
- W5 Context Memory Runtime V4: EXECUTE the policy (KEEP/CONDENSE/OFFLOAD_REF/SUMMARIZE/
  DROP_OLDEST/RELOAD_REF) with fingerprints + safety-fact protection + overflow recovery;
  synthetic 32K/64K/128K/256K tests.
- W6 Specialist Subagent Team Runtime: role contracts (allowlist, mutation policy,
  budgets, model role, expected outputs, failure behavior); recursion/parallel-wave tests.
- W7 Map Observation Closed Loop: Desired→render→Observed→QA→repair→re-observe→verdict;
  full verification checklist; observation feeds final completion gate.
- W8 18-stage evidence chain: emitters at all 18 stages; ≥95% completeness metric + gate.
- W9 Evaluation corpus ≥20K deterministic plan/runtime cases + ≥100 e2e scenarios.

## Wave status

- [ ] W1 — owner: main + subagents — status: pending audits
- [ ] W2 — pending audits
- [ ] W3 — pending audits
- [ ] W4 — pending audits
- [ ] W5 — pending audits
- [ ] W6 — pending audits
- [ ] W7 — pending audits
- [ ] W8 — pending audits
- [ ] W9 — pending audits

## Constraints (from task)

- master read-only; all work in worktree; additive changes to frozen seams only
  (ToolRegistry, CapabilityRegistry, AlgorithmRegistry, SessionPlan, MapSpec,
  ArtifactContract, ExecutionPlan) with compat + contract tests + ADR.
- No second truth: WorkflowInstance = runtime projection of SessionPlan/WorkflowProfile;
  SpatialGoalGraph reconciles with SessionPlan; retrieval layers the registry.
- Local verification only; controlled concurrency; ruff + compile sweep + targeted tests.
- Two+ review rounds; PR after rebase on latest origin/master.

## Open questions (to resolve from audits)

1. Exact persistence path for WorkflowInstance (session store? DB? in-memory per session?).
2. Where AlgorithmResolver lives and its current inputs (audit 03).
3. tool_surface_v3 extension points vs rewrite (audit 04).
4. Context: who owns final prompt assembly (bind_turn_prompt?) for policy execution (audit 05).
5. Subagent host mechanics in Pi (audit 08).
6. Existing corpus counts (audit 07) — is 20K conformance corpus already merged on master
   (commit 978f814)? Then W9 must ADD runtime-category cases, not duplicate.
