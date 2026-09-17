# DECISIONS — recommended V2 architecture (conservative: adapter/additive over rewrite)

Principle: the baseline already has a working deterministic factory (~36,900 cases, zero-LLM). V2 should **fill the 13 gaps by adding corpora and opt-in tiers**, not by inventing a second framework. Every decision below reuses a pattern that already exists in the repo (cited).

---

## D1. Keep `GISBenchmarkCase` as the unit schema; evolve additively — no schema replacement

- **Decision**: extend `app/evaluation/case.py::GISBenchmarkCase` with optional fields only:
  - `turns: List[ConversationTurn]` (new pydantic model: `query: str`, `scenario_kind`-style expectation codes, optional `expected_state` hints) — empty = single-turn case exactly as today;
  - `expected_evidence: List[ExpectedEvidence]` (claim_type + scope + freshness + `must_be_supported: bool`, optionally `positive_proof`);
  - `allowed_tools: Optional[List[str]]` (exact tool-name alternatives, complementing prefix-based `allowed_algorithms`);
  - `tags: List[str]` (free-form; `group` stays closed);
  - `allowed_answer_semantics` for statistical tolerance (e.g. `"rank_order"` / `"interval"` with tol) — modelled as a new opt-in assertion type rather than overloading `NumericAssertion`.
- **Rationale**: the `group` Literal has already been extended additively three times (conformance-*, quality-*, anti-claim — `case.py:53-69`); the V3 opt-in contract tier (`runner.py:410`) proves zero-declared = zero-behavior is safe; existing 36,900 cases keep validating untouched.
- **Alternatives considered**: (a) new `BenchmarkCaseV2` class — rejected: splits the runner and forces dual maintenance; (b) free-form `payload: dict` escape hatch — rejected: loses pydantic validation that keeps cases reviewable (ADR-0092 intent).

## D2. New V2 corpora live as new modules in `app/evaluation/`, following the audited-table pattern

- **Decision**: create `mission_corpus.py`, `skill_policy_corpus.py`, `evidence_corpus.py`, `security_corpus.py`, `hard_negative_corpus.py`, `cartography_axes_corpus.py` with id prefixes `MS-`, `SP-`, `EV-`, `SEC-`, `HN-`, `CARTX-`. Each corpus: frozen hand-audited expectation table + deterministic expansion + build-time guard asserts + `sort(key=id)`.
- **Rationale**: matches `anti_claim.py` / `closed_loop_corpus.py` / `conformance.py` house style exactly; no import cycles because case modules import nothing from planners (ADR-0092 header).
- **Alternatives considered**: JSON/YAML case files — rejected: repo convention is code-as-corpus with guard asserts (dup ids, unique queries); data files would bypass them.

## D3. Add `app/evaluation/index.py` — a manifest/registry over all corpora

- **Decision**: `index.py` registers every corpus builder with `{prefix, builder, count_fn, group_shards, expectation_table_hash}` and exposes `iter_all_cases()`, `corpus_manifest() -> {name, count, version_hash}` (deterministic ordering by registration). Mission/skill/evidence corpora whose "cases" are not `GISBenchmarkCase` get their own dataclass registered alongside.
- **Rationale**: GAP #1; also gives the report a denominator ("all cases enumerated") and CI a cheap drift signal (count/hash) without running the corpora. `chaos_corpus.corpus_node_ids()` shows the value of machine-checkable registries.
- **Alternatives considered**: generated JSON manifest committed to repo — rejected as primary (drift risk), but `write_manifest(path)` CLI flag is fine.

## D4. Mission-level evaluation: drive the real `MissionRuntimeService` hermetically; new `MissionMissionEval` adapter, no new agent host

- **Decision**: a thin adapter `app/evaluation/mission_driver.py` that:
  - constructs `MissionRuntimeService(MissionStore(factory=lambda: sqlite_engine(tmp)))` (the injectable factory is already the documented hermetic seam, `store.py:112-121`);
  - freezes time by monkeypatching `app.services.mission_runtime.store._utcnow` (single module-level clock function — cheapest deterministic control; an additive injected-clock param is the upstream-friendly option if ever touched, which V2 should not since #1335 owns it);
  - drives turns through the hot-path seams (`mission_bind.maybe_bind_mission_for_turn`, `session_ctx`, `claim_ingest.ingest_on_settle`) + the plan tier of the existing runner per turn — i.e. multi-turn = sequence of `GISBenchmarkCase.turns` executed against one session_id/mission_id;
  - injects faults by calling the real seams mid-mission: `OperationCancelled`, stale refs (expire via session store), resource-charge over quota, provider timeout via failure-signature classification;
  - scores: resumed_correctly, goal_revision_lineage, artifact_reuse_count (via `artifact_registry`), cancel_disclosed, resource_leak.
- **Rationale**: #1329 already wired these seams as the convergence point; #1320's store is explicitly built for hermetic tests; reuse means mission behavior changes (including #1335's fail-closed fixes) surface as corpus diffs — the desired regression semantics.
- **Alternatives considered**: (a) replay-bench recorded-op replay — kept for recorded-production missions but cannot generate novel interruption ordering; (b) full chat-engine loop — rejected: needs LLM providers, violates zero-LLM.

## D5. Runner: one new opt-in tier method per domain, following `_run_v3_contract_tier`

- **Decision**: add `_run_policy_tier` (calls `SkillPolicy.resolve` on constructed `SelectionFacts`; asserts mode/trust/shadow-candidate/rejected_codes; kill-switch case asserts `mode=="none"`), `_run_evidence_tier` (builds in-memory `ClaimStore`, applies `claim_from_statistic`/`verify_claim`/`mark_evidence_stale`/contradiction, asserts ClaimStatus + `positive_proof` + tenant rejection + grounding projection shape), `_run_security_tier` (injection payload queries assert: no tool escalation beyond `allowed_algorithms`, `forbid_network_tools`, forbidden warning codes absent, refusal/honest-degradation codes present). All append to `result.metrics` with `None`-when-undeclared honesty.
- **Rationale**: identical shape to `_run_v3_contract_tier` (`runner.py:410-478`); `CaseResult.metrics` is a free dict, and `report._METRIC_ORDER` is the single registration point for new columns.
- **Alternatives considered**: standalone policy/evidence runner classes — rejected: fragments `CaseResult` consumers (report, matrix gates) for no gain; keep one runner, many tiers.

## D6. Report: additive upgrade of `report.py` (JSON + baseline diff + per-group + ratchet hooks); do not touch existing markdown shape

- **Decision**: keep `render_markdown` byte-compatible; add:
  - `aggregate_by_group(results)` → per-group pass-rate + per-metric means (group is already on `CaseResult`);
  - `render_json(results, manifest)` → machine report incl. corpus manifest hash (pairs with D3);
  - `diff_against_baseline(current, baseline)` reusing the semantics of `app/lib/harness/replay/bench.py::compare_results` (digest/count drift) and tolerant-ratchet rows from `replay/ratchet.py`;
  - regression attribution = per-case diff buckets (new-failures / fixed / metric-moved), derived from per-case ids.
- **Rationale**: all pieces exist in `app/lib/harness/replay/` — this is unification, not invention; cartography quality facts already have their own DB-backed ratchet, referenced by link rather than duplicated.
- **Alternatives considered**: new `report_v2.py` — rejected: two report paths will drift; additive functions in one module with stable old function is the repo's own pattern.

## D7. SkillPolicy benchmark: facts-table corpus + one execute_guided test

- **Decision**: `skill_policy_corpus.py` = rows of `(SelectionFacts fields, expected SkillPolicyDecision subset)` covering all 6 modes incl. quarantined→blocked and promoted-candidate→guide; deterministic by construction (`resolve` is documented same-input-same-decision). Add the missing `execute_guided` coverage.
- **Rationale**: policy tests exist only at unit level (`tests/unit/gis_harness/test_skill_policy_v1.py`); the corpus converts them into regression-lock data with counts/manifest presence. No LLM; `GIS_SKILL_POLICY=0` case pins the kill switch.

## D8. Evidence benchmark corpus: consumer, not extension of #1335's tests

- **Decision**: `evidence_corpus.py` builds scenarios purely via public ClaimStore APIs; assertions assert *statuses and disclosure*, not internal step lists. Keep an adapter layer (`evidence_driver.py`) so #1335's fail-closed semantics changes land as corpus diffs.
- **Rationale**: PARALLEL_OWNERSHIP — verify/grounding/census are #1335-hot; V2 ownership must be a separate file.

## D9. Hard-negative + wrong-AOI + no-capability + coreference corpora

- **Decision**:
  - `hard_negative_corpus.py` plan-tier cases: point-vs-choropleth pairs (assert `expected_recipes` disjoint via `forbidden_algorithms`/`forbidden_warning_codes`), wrong-AOI cases (AOI outside fixture/data coverage → assert honest degradation code / empty-result disclosure, reusing `EMPTY_RESULT` taxonomy vocabulary), no-capability cases (assert refusal facet + no fabricated recipe — generalize the `dissolve` exclusion rule into cases);
  - coreference via `turns` (D1): turn-2 queries that are anaphoric ("把它改成…", "该区的医院") with expected re-binding semantics declared as expectation codes, executed by the D4 driver so re-binding is *actually evaluated*, not just declared.
- **Rationale**: reuses failure taxonomy vocab + runner forbidden/expected sets; coreference only becomes meaningful once multi-turn execution exists (else it is self-fulfilling metadata — the gap the leakage discipline warns about).

## D10. Cartography: assert on #1321 axes + render-observation fixtures; add a CVD axis check

- **Decision**: `cartography_axes_corpus.py` cases carry component/layout expectations evaluated through `render_observation.derive_component_layout_findings` and `template_codegen_evaluator.evaluate_template_codegen` with MapSpec/observation **fixtures built in `fixtures.py`** (deterministic component rects incl. an intentional overlap/offscreen case — formalizing today's inline probes at `runner.py:707-774`). Add a deterministic CVD check case family asserting palette selection from `app/lib/cartography/palettes.py` is CVD-safe (ΔE/contrast predicates already exist in symbology). Visual axis stays honest `not_evaluated` unless the deterministic facts are supplied — never an LLM.
- **Rationale**: #1321's unified feedback is the sanctioned aggregation point; fixtures-in-`fixtures.py` keeps the offline/deterministic contract (item 12) intact.

## D11. Naming/placement summary

| New artifact | Location | Reuses |
|---|---|---|
| Case schema fields | `app/evaluation/case.py` (additive) | Literal-extension pattern |
| Multi-turn driver | `app/evaluation/mission_driver.py` | hot-path seams + MissionStore factory |
| Corpora | `app/evaluation/{mission,skill_policy,evidence,security,hard_negative,cartography_axes}_corpus.py` | audited-table pattern |
| Manifest | `app/evaluation/index.py` | chaos corpus registry style |
| Runner tiers | `app/evaluation/runner.py` (opt-in methods) | `_run_v3_contract_tier` |
| Report | `app/evaluation/report.py` (additive fns) | replay bench compare/ratchet |
| Tests | `tests/quality/test_*_corpus.py` (shard by prefix), `tests/harness_replay/test_mission_replay_*.py`, `tests/unit/gis_harness/test_<x>_v2.py` | SCENARIO_PREFIXES sharding |
| Dir-local fixtures | new `conftest.py` in the new test dirs only | tests/quality/conftest.py sqlite pattern |

## D12. What NOT to do (guardrails from this baseline)

1. Do not edit `mission_runtime/**`, `evidence_claim/**`, `hotpath_convergence/**` (#1335) or `tests/conftest.py`, `app/lib/modelops/**` (#1336) — see PARALLEL_OWNERSHIP.md.
2. Do not add LLM judging anywhere in `app/evaluation` (item 12 is EXISTS and load-bearing); optional VLM stays behind the existing `GIS_VISUAL_EVALUATOR` / `CARTO_VISUAL_JUDGE_MODE` seams, record-only.
3. Do not copy runtime outputs into expectation tables (frozen-table re-audit rule, `quality_corpus.py:23-27`).
4. Do not fabricate measurements: new metrics must be `None` when not measured (runner honesty invariant).
5. Do not exceed the 60s per-test timeout: shard any thousand-scale corpus by prefix like `SCENARIO_PREFIXES`.
