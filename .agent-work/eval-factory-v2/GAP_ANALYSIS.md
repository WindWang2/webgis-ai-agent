# GAP ANALYSIS — 13 items vs baseline faa453a8

Verdicts: **EXISTS** (usable as-is) / **PARTIAL** (exists, concrete gaps listed) / **MISSING** (nothing found). Every claim is backed by a file path; counts measured 2026-09-16 by importing builders.

---

## 1. Inventory coverage (unified manifest/index) — **PARTIAL**

- EXISTS: 14+ corpora with deterministic builders, dup-id guard asserts, stable ordering (`app/evaluation/conformance.py:679-682`, `quality_corpus.py:1341-1347`, `case_matrix.py:612-614`).
- MISSING: **no single manifest/index enumerating all cases across corpora** (no `iter_all_cases()`, no counts registry, no per-case provenance record). Counts live only in builders + regression tests (`tests/quality/test_quality_scenario_corpus.py` asserts >=5000; `case_matrix.get_expected_total` only covers golden+matrix=306). `scripts/gen_science_benchmark_manifest.py` is an unrelated science-benchmark generator.
- Gap for V2: build an index module (see DECISIONS.md D3) that registers every corpus with id prefix, count, group shard, and hash of the frozen expectation tables.

## 2. BenchmarkCase schema vs multi-turn / ExpectedEvidence / AllowedAlternatives — **PARTIAL**

Schema today (`app/evaluation/case.py:48-155`): single `query: str`, `group` (closed Literal, 40 values), expected/forbidden task(s)/recipe(s)/capabilities/algorithms/warning codes, `expected_ontology_task`, qualification/fallback, `scenario_kind`, `expected_tool_classes`, `expected_export_formats`, `max_context_schema_bytes`, `forbid_network_tools`, `trace_requirements`, `plan_only`, `fixture_aliases`, `script: List[ScriptStep]`, `expected_artifact_types`, `component_assertions`, `numeric_assertions`, `expected_interaction_semantics`.

| Capability | Verdict | Evidence |
|---|---|---|
| Multi-turn sessions | **MISSING** in `GISBenchmarkCase` | one `query: str` only. Multi-turn exists only as (a) `CompositeScenario` 2-turn metadata with expectation codes, never executed (`runtime_corpus.py:327-419`); (b) replay-bench `TurnSpec` sequences replaying *recorded* ops (`app/lib/harness/replay/scenarios.py` multi-turn suite); (c) `RUNTIME_SITUATIONS["multi-turn-followup"]` executed as a 2-call dataflow script, not a conversation (`runtime_corpus.py:476-481`). No case schema carries turn history, and no runner executes turn N conditioned on turn N-1 state. |
| Expected evidence refs | **MISSING** | `trace_requirements` is an intent declaration only (case.py:129-132, explicitly "供后续工作消费"). Nothing asserts a claim/evidence id, provenance node, or grounding ref. |
| Allowed-alternative tool sets | **PARTIAL** | `allowed_algorithms` is prefix-set based (case.py:81-83, runner.py:285-293); `expected_tasks`/`expected_recipes` are set-membership alternatives (case.py:76-87); `expected_tool_classes` is coverage-not-exact (runner.py:342-349); retrieval corpus has `valid` + `must_not` sets (`retrieval_eval_corpus.py:44`). But there is no first-class "acceptable tool-name set" at case level, and algorithm prefixes ≠ tool names. |
| Tolerance/semantics for statistical answers | **PARTIAL** | `NumericAssertion op="approx"` with absolute `tol` (case.py:38-41) covers numeric tolerance on executed results. No semantic tolerance for statistical claims at plan tier (e.g. "higher/lower", rate-vs-count phrasing) beyond warning codes (`EQUITY_MISSING_DENOMINATOR`, `TREND_INSUFFICIENT_OBSERVATIONS`). |
| Tags/domains | **PARTIAL** | closed `group` Literal (additive extension is the established pattern), single `scenario_kind` string, `family` fields inside corpus dataclasses; no free-form tags, no multi-label domain field. |
| Chinese+English text | **EXISTS** | conformance bilingual expansion (`conformance.py:622-641`), methodology zh/en pairs (`methodology_corpus.py`), quality expansions zh-only seeds, retrieval corpus `lang="en"` cases (`retrieval_eval_corpus.py:154,171`). |

## 3. 300+ high-quality small cases, bilingual, coreference, scope/time/statistical semantics — **PARTIAL**

- Volume + bilingual + scope: **EXISTS and exceeds** — 20,088 conformance (59 families × zh/en × 12 scopes × 9 utterances), 6,406 quality (× 12 data states), 598 retrieval gold, 104 goal-satisfaction, 306 golden+matrix. Scope variants include multi-city and province (`conformance.py:26-39`).
- Statistical semantics: **PARTIAL** — data-state profiles cover `numericSampleCount`/variance/zero-variance/empty (`quality_corpus.py` `_BASE_FACTS`, `DATA_STATE_PROFILES`); anti-claim covers denominator/per-capita and trend observation counts (`anti_claim.py:26-84`, `WC-trend-*`); kriging small-sample blocked (`WC-kriging-blocked-no-field`). No "which district is higher"-class relational-statistics answer cases.
- Time semantics: **PARTIAL** — `temporalObservationCount` states + trend obligations; no change-detection family corpus at plan tier beyond matrix `change` family cases; no temporal-scope (season/period) variant axis.
- **Coreference: MISSING** — no case models anaphora/ellipsis ("把它放大", "该区域", "same buffer but for hospitals"). CompositeScenario turn-2 queries are full restatements, not coreference. `methodology_corpus.ambiguous_variants` is under-specification, not coreference.

## 4. Hard negatives (point-vs-choropleth, count-vs-rate, CRS metric, missing data, wrong AOI, no-capability, synonym-tool competition) — **PARTIAL**

| Negative type | Verdict | Evidence |
|---|---|---|
| Point-vs-choropleth | **PARTIAL** | proportional-symbol vs choropleth families in matrix (`case_matrix.py` `_FORM` F003/F004); school-points vs admin-choropleth map types in `closed_loop_corpus._MAP_TABLE`; no explicit "points requested, polygons wrong" adversarial pair with `must_not` |
| Count-vs-rate | **EXISTS (thin)** | `AC-denominator-percapita` (人均必须先有分母), `WC-equity-*` obligation pair, `rate_aggregation` capability in closed-loop equity row |
| CRS metric | **EXISTS** | `WC-kriging-angle-crs` (blocked), `WC-slope-angle-crs` (degraded + `TERRAIN_METRIC_CRS_REQUIRED`), `FL-CRS-1` taxonomy case (`CRSError` → `substitute_operator`), quality data-state `crs` variants |
| Missing data | **EXISTS** | `empty_partial` failure category (FL-EMPTY-1/FL-PARTIAL-1), runtime `missing-data` exec case (ref:missing → structured error), quality data states (empty set / missing field), `WC-kriging-blocked-no-field` (absence must be authoritative `numericFields=[]`) |
| Wrong AOI | **MISSING** | scope variants prove *same* semantics across cities; nothing asserts honest failure when the AOI is outside data coverage / contradicts the layer extent |
| No-capability (honest incapability) | **PARTIAL** | `dissolve` family explicitly excluded because capability absent (`quality_corpus.py` 排除规则); retrieval `out_of_scope` abstention cases (`oos_abstention_rate`, `retrieval_eval_corpus.py:1286-1292`); no plan-tier corpus asserting a refusal/degradation contract for missing capability |
| Synonym-tool competition | **EXISTS** | retrieval `near_duplicate` (24) + `paraphrase` (240) kinds with `must_not`; `batch_geocode_cn` vs `geocode_cn` hard-negative pair; `update_layer_appearance` vs `apply_layer_style` ambiguous pair |

## 5. Mission interruption / recovery / goal revision / artifact reuse / cancel — **PARTIAL**

- Production surface EXISTS (#1320): `MissionRuntimeService` full lifecycle + `MissionRecoveryCoordinator` + `MissionResourceLedger` (quota charge/reserve/exhausted) + `MissionArtifactOwnership` + state machine with terminal/fencing (`mission_runtime/contracts.py:26-113`); `MissionStore(factory=...)` injectable for hermetic sqlite (`store.py:112-121`).
- Evaluation coverage is **declarative + unit-pinned**, not end-to-end: chaos rows `CH-restart-resume-anchor`, `CH-resume-dangling-ref`, `CH-cancel-no-retry`, `CH-ledger-budget-cross-worker` pin invariants to unit tests (`chaos_corpus.py:54-110`); `RUNTIME_SITUATIONS` declares `multi-turn-followup` (plan_continuity, goal_supersede_lineage) and `stale-artifact`/`cancel` with traceability pointers (`runtime_corpus.py:60-200`); e2e variants `inject-cancel`/`inject-stale-artifact` are walkable strings only.
- MISSING: a runner that executes a *multi-turn mission* (bind mission → turns → interrupt → resume → cancel → artifact reuse) deterministically and scores it. No fake clock (`_utcnow()` wall-clock in `store.py:31`); goal-revision (`goal_revision`, `MissionFrontier`) has no corpus at all; artifact-reuse across turns has no case schema field.

## 6. SkillPolicy core/none/shadow/blocked/fallback — **PARTIAL**

- Production EXISTS: all 6 modes + kill switch + shadow isolation (`skills/policy.py:38-70`).
- Unit tests EXISTS and are strong: `tests/unit/gis_harness/test_skill_policy_v1.py` covers guide/fallback (`test_low_confidence_falls_back`), none (`test_kill_switch`, `test_no_skill_preserves_existing_behavior`), blocked (`test_quarantined_blocked`, `test_geometry_mismatch_blocks_choropleth`), shadow (`test_shadow_evaluation_works`, `test_shadow_cannot_mutate_state`, `test_induced_excluded_from_trusted_path`), determinism (`test_same_inputs_same_policy_decision`).
- Benchmark-level **MISSING**: no corpus of `SelectionFacts → expected SkillPolicyDecision` rows, no case schema field to declare a policy contract, no report metric (policy_mode_correct). `execute_guided` has no test either.

## 7. Evidence unsupported / stale / contradicted / positive-proof — **PARTIAL**

- Production EXISTS (#1328): `ClaimStatus` with `UNSUPPORTED/CONTRADICTED/STALE` (`evidence_claim/contracts.py:83-88`), `verify_claim` with `positive_proof` flag + narrative-cannot-be-supported + tenant isolation (`verify.py:69-99`), `contradiction.py`, `freshness.mark_evidence_stale` descendant propagation, `grounding_projection`.
- `app/evaluation/anti_claim.py` evaluates *methodology-honesty warning codes* (a different, plan-tier sense of "anti-claim") — it does **not** touch ClaimStore.
- **MISSING**: no evaluation corpus drives ClaimStore verdicts (unsupported-by-default, stale propagation to descendant claims, pairwise contradiction, positive-proof acceptance, cross-tenant rejection). The unit tests (`tests/unit/gis_harness/test_evidence_claim_graph_v1.py`) cover behavior but are also **PR #1335's hot files** — a V2 corpus must be a separate consumer, not an extension of those tests.

## 8. Cartography eval (map type, legend, labels, CVD, layout, render observation) — **EXISTS (deterministic axes) / PARTIAL (coverage holes)**

- EXISTS: #1321 unified 3-axis feedback `UnifiedCartographyFeedback` (visual/template_codegen/gis_semantics, honest `not_evaluated`) wired into Pi verdict injection (`app/lib/harness/cartography_feedback.py:58-96`, `pi_agent_harness.py`); `template_codegen_evaluator` (schema/compile/composition/component-reuse, pure over MapSpec); `render_observation.derive_component_layout_findings` (overlap/offscreen deterministic layout) + `validate_render_observation` (revision-stamped, stale→honest downgrade); deterministic judge stub for replays (`tests/harness_replay/replay_judge.py`); runner component assertions for `title/north_arrow/scale_bar/attribution/legend` facets (`runner.py:512-523`); closed-loop 17 map types; cartography quality facts + ratchet (`tests/quality/conftest.py`, `test_cartography_ratchet.py`); golden image diff (`test_golden_image_diff.py`); CVD-aware palettes/symbology exist in production (`app/lib/cartography/palettes.py`, `symbology.py`).
- PARTIAL: no corpus asserts **label placement/overlap** semantics as cases (only L4 rule names inside replay snapshots); **CVD has no evaluation axis** (palettes support it; nothing measures "palette is CVD-safe for this map"); visual axis of #1321 degrades to `not_evaluated` when no VLM — by design, so V2 must supply the deterministic visual facts (component rects) to make the visual axis evaluable offline; `map_critique.py` exists but is not consumed by any corpus.

## 9. Fault injection (timeout / provider-open / ref-missing / fake DB / Redis failure) — **EXISTS**

- `failure_corpus.py`: 17 cases over 8 categories — provider_timeout (incl. `TimeoutError` exception object), transient_db (pool exhausted), empty_partial, map_source_error, process_restart, stale_workspace, cancellation (`OperationCancelled`), retry_exhaustion, plus CRS — each walked through the real remediation budget ladder to `abort_with_disclosure` (no sleeps; signature-based).
- `chaos_corpus.py`: 17 scenarios incl. Redis blip (`CH-redis-blip`), kill -9 lock holder, torn trace writes, duplicate settle, cross-session isolation, dangling resume ref — each pinned to a *verified-collectable* pytest node (gate test prevents fake rows).
- `reliability_corpus.py` (19 scripted) + `runtime_corpus.build_runtime_execution_corpus` (60 real-dispatch cases: ref-missing, no-progress, big-payload).
- Fake DB/Redis: `MissionStore(factory=...)` + `tests/quality/conftest.py` sqlite dual-engine pattern; `fakeredis` is a repo dependency (dependabot #1296) and used by acceptance chaos tests.
- Gap (minor): no *provider-circuit-open* (repeated failure → open breaker) fault class; "provider-open" beyond retry ladder is only implicitly covered by `CH-retry-exhaustion-terminates`.

## 10. Metrics (GoalSatisfaction / ToolChoice / InvalidSelection / EvidenceGrounding / MapCompleteness / Recovery / Resource / Security) — **PARTIAL**

| Metric | Verdict | Where |
|---|---|---|
| GoalSatisfaction | **EXISTS** | `evaluate_goal_satisfaction` + 104-case corpus; false_pass_rate & not_evaluated_honesty are first-class |
| ToolChoice (task/recipe/capability correct) | **EXISTS** | plan-tier metrics in `runner.py:373-403`; retrieval p@1/r@k (`retrieval_eval_report`) |
| InvalidSelection | **EXISTS** | `invalid_selection_rate` + `must_not` + `tier3_leak` in `RetrievalEvalReport`; `forbidden_algorithms` in runner; `unnecessary_tool_count` |
| EvidenceGrounding | **MISSING** (as metric) | ClaimStore unused by evaluation; runner has no grounding metric; `chain_gate` measures stage coverage, not claim support |
| MapCompleteness | **PARTIAL** | `map_product_complete` = boolean mapspec_present (runner.py:842); component/facet assertions boolean; #1321 template/codegen axis is a score but not aggregated into any benchmark report; cartography quality facts DB is separate from `app/evaluation/report.py` |
| Recovery | **PARTIAL** | failure corpus proves ladder termination and expected first/exhausted actions per class, but there is **no aggregate recovery-rate/success-after-fault metric**, and chaos invariants are pinned per-test, not scored |
| Resource | **PARTIAL** | `tool_call_count/retry_count/max_tool_calls/max_context_schema_bytes/context metrics (overflow, schema-token ratio)` exist; `MissionResourceBudget` exists in production; no corpus charges budgets/meters tokens or wall-clock as a scored metric |
| Security | **PARTIAL** | `tier3_leak=0`, `forbid_network_tools`, tenant isolation inside `verify_claim`, XML security fencing in chat context — but **no prompt-injection/attack corpus anywhere in app/evaluation** (grep: only `failure_corpus.py` matched "injection", as fault-injection) |

## 11. Report (baseline diff / regression attribution / per-domain scoring / ratchet) — **PARTIAL**

- `app/evaluation/report.py` is a markdown table (fixed metric order) + failures + skipped sections. **No** baseline diff, **no** regression attribution, **no** per-domain (group) aggregation, **no** ratchet.
- The capabilities exist *scattered*: `retrieval_eval_report.by_kind` (per-kind), `app/lib/harness/replay/bench.py::compare_results` + `--baseline` (digest/count drift), `replay/ratchet.py` (tolerant ratchet rows), `tests/quality/structural_baselines.json` + `test_structural_perf_gates.py`, cartography ratchet service + `test_cartography_ratchet.py`, `test_findings_ratchet_gate.py`.
- Gap: unify into the benchmark report (see DECISIONS.md D6) rather than rebuilding.

## 12. Offline deterministic default; LLM judge optional only — **EXISTS**

- Runner docstring + implementation: zero LLM (`runner.py:1-7`); `render_verified` honestly `None` offline (runner.py:823).
- Retrieval eval pins the semantic channel off for determinism (`retrieval_eval_corpus.py:1322-1327` "评测确定性口径"); embedding retriever covered by separate skipif tests.
- Visual evaluator default-off seam (`visual_evaluator.py`); VLM judge record-only, offline replay uses deterministic judge; replay bench enforces offline (`scripts/replay_bench.py` "offline 强制").
- Anti-leakage discipline documented in `methodology_corpus.py` (期望为审定工件，编译器从未见过) and `quality_corpus.py` (冻结表不得照抄运行时输出).

## 13. Leakage / self-fulfilling-test review — **PARTIAL**

- EXISTS as *written discipline*: methodology corpus anti-leakage red line (`methodology_corpus.py:14-17`), quality corpus frozen-contract-table re-audit rule (`quality_corpus.py:23-27`), chaos corpus test-node verification (`chaos_corpus.py:6-9`), build-time guard asserts.
- MISSING: no automated leakage check (e.g. corpus text must not contain expected ids/answer strings; expectation tables must not be generated from runtime output — currently enforced only by convention and review), no provenance field on cases ("audited-by/date/source"), no self-fulfilling-pattern lint (expectation derived from the same function under test).

---

## Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | Inventory coverage | PARTIAL |
| 2 | Case schema (multi-turn/evidence/alternatives/tolerance/tags/bilingual) | PARTIAL (multi-turn & evidence refs MISSING; bilingual EXISTS) |
| 3 | 300+ bilingual cases, coreference, scope/time/statistics | PARTIAL (coreference MISSING; volume EXISTS) |
| 4 | Hard negatives | PARTIAL (wrong-AOI MISSING; point-vs-choropleth & no-capability thin) |
| 5 | Mission interruption/recovery/goal-revision/artifact-reuse/cancel | PARTIAL (production rich; end-to-end deterministic eval MISSING) |
| 6 | SkillPolicy modes | PARTIAL (unit EXISTS; benchmark corpus/metric MISSING) |
| 7 | Evidence unsupported/stale/contradicted/positive | PARTIAL (production EXISTS; eval corpus MISSING; #1335 hot) |
| 8 | Cartography axes | EXISTS (deterministic) / PARTIAL (labels-as-cases, CVD axis) |
| 9 | Fault injection | EXISTS |
| 10 | Eight metric families | PARTIAL (EvidenceGrounding MISSING; Recovery/Resource/Security/MapCompleteness partial) |
| 11 | Report diff/attribution/per-domain/ratchet | PARTIAL (pieces scattered; report.py basic) |
| 12 | Offline deterministic default | EXISTS |
| 13 | Leakage review | PARTIAL (discipline only) |
