# 00 — Baseline (master @ f00c8fb, 2026-09-04)

Worktree: `../webgis-ai-agent-workbench-vnext`, branch `feat/agentic-gis-workbench-vnext` from `origin/master` f00c8fb.

## What already exists on master (verified by 5 recon agents + direct reads)

| Area | State on master |
|---|---|
| SessionPlan | Envelope w/ embedded `gis_chapter` (MapProductPlan dump), progress rows, SSE events `session_plan_*` (ADR-0076) |
| PlanGraph | `app/services/gis_harness/plan_graph.py` — derived DAG (depends_on, status propagation, recommended_next, `[GIS Plan]` text block). NOT exposed as API/UI object |
| ProductGraph/facets | `product_graph.py`, `product_facets.py`, `product_action.py`, `action_intent.py`, `product_lineage.py` — all derived projections (ADR-0085/0087/0088) |
| Map Product ledger | `map_products` table + `MapProductService` (append-only, 5-dim diff, pairwise diff); REST GET/POST; frontend version panel w/ diff chips + rerun-from-step. NO open/restore/fork/merge; NO auto-record on run completion; version row has fingerprint only (no mapspec body) |
| Workflow runtime | `WorkflowEngine`: rerun_from_step (descendant invalidation + stale-algorithm seeds + input drift), replay/resume/compare. Selective recompute exists HERE |
| Pi runtime V5 | `PiBridgePool` (md5 affinity), TurnLease INV-P1..P4, `_active_turns` table, Redis PiTurnRegistry, abort snapshot semantics, chaos tests. Cancellation joined by 3 hand-written abort() call sites; extension ignores AbortSignal; process-wide FIFO wave semaphore (no per-session fairness); subagent.py has zero cancel wiring; skills frozen at spawn |
| Semantic layer | 12 TaskTypes, 13 recipes, 42 capabilities, 52 algorithms, 11 analysis patterns (advisory). Site-selection/suitability/risk/equity/coverage queries fall through to `fallback_distribution_default` (intent.py:427); G27–G29 golden cases LOCK IN the fallback |
| Methodology warnings | planner.py plan_from_intent: pattern-based `{pattern, missing_roles, disclosures, pitfalls, stage}` dicts; benchmark asserts; NO frontend rendering; no stable code enum |
| Decision V3 | Full `DecisionEngineV3` (WSM/TOPSIS, Pareto, Monte Carlo, sensitivity, robustness/regret, admissibility) — tools only (tier 2/3), no HTTP surface, no UI |
| Cartographic QA | map_completion pipeline (validators F_*, repairs ≤2 passes), CartographicQuality production gate, render observation revision gating |
| Frontend workbench | NavRail+ContextPanel+MapPanel+PanelDockHost+EmbodiedHud shell; selection linkage map↔chart↔table (selection-store, ADR-0091); mapspec-runtime defect families closed w/ tests; map canvas a11y weak |
| Evaluation | 33 golden cases G1–G33 (all Chinese), plan-tier (deterministic, no LLM) + execute-tier runner, CLI `manage.py gis-benchmark`, pytest wrapper |
| Checkpoints | Session-scoped MapSpec checkpoint/rollback (20 retention, CAS monotonic) — separate truth from project ledger |

## Verified gaps to close (mapped to /goal sections)

- §3 Analysis Graph: serialize PlanGraph+ProductGraph via API/SSE + UI panel; keep derived-projection invariant (ADR-0076 forbids persisting).
- §4 Semantic V2: TaskTypes site_selection/suitability/risk_exposure/spatial_equity/service_coverage (+ English), recipes, capability `mcda_evaluation`, honest planner rows (criteria/evidence/warnings), update G27–G29.
- §5 Methodology: stable warning codes, UI rendering, preservation through versions.
- §6 Map Product: mapspec snapshot binding, open/restore(style-only + full)/fork/rerun-from-version/auto-record/constrained merge.
- §8 Shared world state: exists (gis_world_state + UserPresentationGuard); extend with analysis graph + pending mutations exposure.
- §11 Pi: unified cancellation seam, extension AbortSignal, per-session wave fairness, subagent cancel, truthful skill refresh.
- §12 Decision Workspace: API + UI for DecisionEngineV3.
- §13 Evidence: provenance/evidence inspector UI (backend data exists).
- §14 QA: map verdict vocabulary READY/READY_WITH_WARNINGS/NEEDS_REPAIR/BLOCKED_BY_DATA/BLOCKED_BY_METHOD on top of map_completion.
- §15/16 Evaluation: 33 → 100 → 300+; multi-turn scenarios A–D; Chinese+English.
