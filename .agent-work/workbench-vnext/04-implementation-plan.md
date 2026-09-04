# 04 — Implementation plan (milestone order, commit strategy)

Invariant for every milestone: SessionPlan/Harness/MapSpec/ToolRegistry/artifact+workflow truths stay single; everything new is an additive projection or an additive schema/route.

## M1 — ADRs + boundary docs (docs(adr))
- ADR-0096 agent-product-plane-vnext: plane boundary, owned concepts, no Data Fabric internals.
- ADR-0097 analysis-graph-projection: explicit graph = API/UI projection of PlanGraph+ProductGraph (derived, never persisted).
- ADR-0098 semantic-product-families-v2: first-class site_selection/suitability/risk/equity/coverage; honest planning rows; replaces G27–G29 fallback lock-in.
- ADR-0099 map-product-lifecycle-v2: open/restore/fork/rerun/merge semantics on the ledger; mapspec snapshot binding; auto-record.
- ADR-0100 pi-cancellation-unification: one cancel seam; AbortSignal propagation; wave fairness; subagent cancel; truthful skill refresh.
- Update CONTEXT.md glossary entries (Analysis Graph, Product Verdict, Decision Workspace, methodology warning codes).

## M2 — Semantic GIS Intelligence V2 (feat(semantic))
Files: app/services/gis_harness/intent.py (TaskType + rules + negative-lookahead updates), recipes.py (5 new recipes), product_templates.py, app/lib/gis/capability_registry.py (mcda_evaluation, criteria handling), analysis_patterns.py (patterns already exist — wire to planner rows), planner.py (honest requirement rows: criteria/denominator/network roles; methodology warning codes), pattern_projection.py (code field).
Behavior: 选址/适建/风险/公平/覆盖率 queries resolve to dedicated task types with dedicated recipes; planner emits role-typed data requirements (criterion layers, denominator, constraints) + methodology warnings with stable codes (e.g. `MCDA_MISSING_CRITERION`, `EQUITY_MISSING_DENOMINATOR`); update golden cases G27–G29 to expect new tasks; keep old queries stable (regression guard: existing G1–G26 unchanged).

## M3 — Explicit Analysis Graph (feat(harness))
- app/services/gis_harness/analysis_graph.py: `build_analysis_graph(session_id)` → serializable `{goal, nodes[{id, kind, purpose, capability, algorithm, tool, status, depends_on, evidence, artifacts, warnings, recompute_impact}], edges, blocked_reason, next_action}` merged from PlanGraph + ProductGraph + facets + action intent + lineage. Zero new persisted truth.
- API route GET /api/v1/sessions/{sid}/analysis-graph (+ project variant) in app/api/routes/.
- SSE event `analysis_graph_updated` piggybacked on session_plan updates (bounded payload).
- Tests: projection determinism, bounded size, supersede behavior.

## M4 — Map Product lifecycle V2 (feat(product))
- Migration (additive): map_products columns `mapspec_snapshot` (JSON, nullable), `label`, `actor`, `parent_version_no` (fork lineage), `lineage_kind` (linear|fork|restore|merge|rerun).
- MapProductService: `open_version` (read-only materialization check), `restore_version` (style-only via checkpoint+patch; full only when authoritative artifacts alive — else BLOCKED disclosure), `fork_version` (new lineage from version), `rerun_version` (bind version→run→from_step via diff details), `merge_versions` (constrained: only non-conflicting dimension pairs, style+analysis allowed, both-changed-style refuses), auto-record hook after run completion.
- REST: POST /projects/{id}/map-products/{v}/restore|fork|rerun|merge; GET .../{v}/open.
- Frontend map-product-versions.tsx: open/restore/fork/merge actions + disclosure states.
- Tests: five-dimension selective recompute respected; style-only restore never triggers analysis; fork lineage; merge refusal on conflicting dims; auto-record idempotent.

## M5 — Pi V6 (feat(pi))
- `app/services/chat/cancellation.py`: single `request_session_cancellation(session_id, reason, origin)` joining tracker token + bridge abort + cancellation registry + durable jobs. Refactor task.py/jobs.py/chat.py call sites to it.
- Extension index.mjs: honor `_signal` → AbortController on fetch (Pi-side abort cancels HTTP tool call).
- Wave fairness: per-session slot cap (default 2) inside `_wave_semaphore` acquire (fair-ish bounded queue); expose queue depth gauge.
- subagent.py: wire CancellationToken + registry.
- Skills: `refresh_skill_surface` — registry-side reload + documented respawn notice (truthful bounded refresh; no fork of Pi).
- Tests: cancel paths matrix, fairness starvation test, extension abort unit, subagent cancel.

## M6 — Cartographic QA verdict + components (feat(cartography))
- map_completion contracts: product verdict vocabulary READY/READY_WITH_WARNINGS/NEEDS_REPAIR/BLOCKED_BY_DATA/BLOCKED_BY_METHOD mapped from existing findings (F_* codes) + methodology warnings.
- Components: methodology_note + uncertainty_panel + decision_panel registered (registry + templates + frontend renderers + parity regeneration).
- Tests: verdict mapping, component parity, template advertisement ≤ renderer support.

## M7 — Frontend workbench (feat(workbench))
- Analysis graph panel (session plan tab extension): node list w/ status, purpose, warnings, next action; drill-down inspector.
- Evidence inspector (versions/runs): data used, dataset version, CRS, algorithm+why, params, fallbacks, uncertainty, missing data, methodology warnings — structured human-readable + drill-down (no raw JSON default).
- Decision workspace: right-dock panel reading decision results (via artifacts/session store API), weight editor with deterministic recompute (calls decision engine re-evaluation endpoint), sensitivity/Pareto/robustness readouts, assumption vs observed distinction.
- Version lifecycle UI from M4.
- Map canvas keyboard accessibility: arrow-key pan, +/- zoom, keyboard-select (bounded).
- All panels: APG tab patterns, progressive disclosure, existing design tokens.

## M8 — Interaction closure (feat(interaction))
- Selection context → agent visibility: bounded selection descriptor in world_state (ids/predicate/summary, ≤50 ids) — exists partially; verify + tests (hidden layer user-wins during finalize; chart placement user-wins; agent "these areas" answers from selection refs).

## M9 — Evaluation V2 (test(evaluation))
- Phase 1 ≥100 cases: data-driven matrix expansion of golden_cases.py (intent × data-condition × language), split modules per family; parametrized pytest.
- Phase 2 ≥300: add interaction/runtime-condition cases (plan-tier where possible, execute-tier for high-value).
- Multi-turn scenario tests A–D (deterministic, offline): chatless — drive intent→plan→dispatch→mapspec→product chain directly (no LLM).
- English + Chinese coverage; forbidden-warning assertions; recomputation assertions via diff service.

## M10 — Reviews (5 gates) + fixes.
## M11 — Final regression + PR.

## Validation commands (local only)
- Backend fast: `python -m pytest tests/unit -x -q` (subset by milestone), `python manage.py gis-benchmark --offline`.
- Frontend: `pnpm vitest run` (targeted), `pnpm typecheck`, `pnpm lint`, `pnpm build` when affordable.
- ruff: `ruff check app tests`.
