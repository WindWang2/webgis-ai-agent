# 08 — E2E Scenario Map (webgis-ai-agent-quality-v1)

Audited: 2026-09-08. Read-only pass over `app/evaluation/`, `tests/` (818 py files), `scripts/gen_*.py`, chat/harness seams. All paths relative to repo root. Line numbers verified.

Stage vocabulary used throughout: **CHAT** (NL intake / SSE / Pi turn) → **HARNESS** (intent → planner → workflow compile → contract) → **TOOLS** (registry dispatch) → **DATA** (session store / ingest / fabric) → **ALG** (algorithms) → **ART** (artifact registry / contract) → **CARTO** (MapSpec / cartography semantics) → **MAP** (map product finalize / verdict) → **VERIFY** (observation / harness gates / runtime validator).

---

## 1. Executive summary

- The repo already owns a **strong declarative evaluation framework** — `app/evaluation/` (ADR-0092/0101) — whose `GISBenchmarkCase` (`app/evaluation/case.py:48`) is 80% of a ScenarioSpec DSL: plan-tier contract (task/recipe/capabilities/warning codes), execute-tier scripted `ScriptStep`s over seeded fixtures, and assertions (artifact types, MapSpec components, numeric goldens, interaction semantics). It is pydantic data interpreted by `GISBenchmarkRunner` (`app/evaluation/runner.py:163`), **zero LLM by construction**.
- Full-chain *deterministic* scenarios exist but are **hand-assembled in three disconnected theaters**: (a) evaluation corpora that start at HARNESS and never enter CHAT; (b) chat/Pi tests that fake the LLM per-test and stop before real tool pipelines; (c) map-product/cartography scenario suites that enter at MapSpec and never see the planner. Only `tests/unit/gis_harness/test_multiturn_scenarios.py` crosses HARNESS→MAP, and even it enters at `resolve_map_request_intent`, not at a chat message.
- **No central fake LLM provider exists.** 27 test files patch `call_llm` seams independently; the planner seam has a documented footgun (patching `chat.llm_client.call_llm` instead of `chat.planner.call_llm` "silently hits the network" — `tests/unit/test_planning_v3_scenarios.py:20-24`).
- **Biggest coverage holes for a full E2E corpus**: CHAT→HARNESS transition inside a scenario; ART→CARTO (analysis artifact → MapSpec layer/legend) inside a scenario; MAP→VERIFY against anything other than a *synthetic* completion face (`app/evaluation/anti_claim.py:299-313`; `render_verified` is hard-coded `None` "honest" at `runner.py:760`); budget ceilings beyond plan-time `max_tool_calls`; trace invariants and observation probes as scenario-level assertions; and execute-tier fixtures for bad CRS, invalid geometry, and temporal data (all three are covered only at algorithm/oracle or profile-contract level).
- A generated scenario corpus should **extend `GISBenchmarkCase`**, reuse `FIXTURE_BUILDERS` (`app/evaluation/fixtures.py:149`), the probe DSL from `tests/fixtures/runtime/*/probes.json`, and `WorkflowContractCase`, and follow the two existing generation patterns: frozen-JSON replay (`scripts/gen_science_oracles.py` → `tests/science_oracles/data/*.json`) and audited-family × deterministic-expansion (`app/evaluation/conformance.py`, 59 families × 12 scopes × 9 utterances = 20,088 cases).

---

## 2. Existing scenario suites

| Suite | Stages covered | What it asserts | LLM fake strategy | Determinism |
|---|---|---|---|---|
| `app/evaluation/scenarios.py` — `GISScenario` (7 scenarios, S-schools-distribution … S-terrain-hydrology), run by `tests/unit/gis_harness/test_conformance_corpus.py:152` | HARNESS (plan cases) + TOOLS→DATA→ART→CARTO→MAP (execute tier: `webgis_map_product` on fixtures) + HARNESS-contract (obligations/verdict) | 3 tiers must all pass: plan contract (task/recipe/caps/warning codes), scripted execute (components `title/north_arrow/scale_bar/attribution`, numeric fixture assertions, `runner.py:555-594`), workflow contract (verdict `READY_WITH_WARNINGS` / `BLOCKED_BY_METHOD`) | **None needed** — planner is rule-based: `resolve_map_request_intent` (`app/services/gis_harness/intent.py:660`) + `MapProductPlanner.plan_from_intent(use_memo=False)` (`runner.py:192-198`) | Full: no LLM, no network, seeded fixtures; scenario = frozen composition of cases (`scenarios.py:197-217`) |
| Golden cases G1–G33 (`app/evaluation/golden_cases.py`, 33 cases) + case matrix (`case_matrix.py`) — total ≥300 locked by `tests/unit/gis_harness/test_benchmark_harness.py:25` | HARNESS all; execute tier on G1/G4/G5/G6/G8/G9/G11/G12/G31–G33 (TOOLS→DATA→ART→CARTO→MAP) | task/recipe/capability/allowed+forbidden algorithms/`max_tool_calls`/product facets; G4 asserts 150k POI result stays under byte budget via `step_result_bytes` (`golden_cases.py:97`, `runner.py:118-132`); G5 NDVI numeric golden via `quantity` (`runner.py:597-615`); G8/G9 interaction semantics (`user-wins`, `artifact-expired-no-remount` — real `classify_runtime_repairs` probes, `runner.py:644-711`) | None needed (same rule planner) | Full; offline; skipped-honestly when tools unregistered (`runner.py:479-482`) |
| Conformance corpus (`app/evaluation/conformance.py`): 59 `ConformanceFamily` × 12 `SCOPE_VARIANTS` × 9 `UTTERANCE_VARIANTS` × zh/en = **20,088 plan-only cases** | HARNESS only | Semantic identity invariant: every paraphrase in a family resolves to same task/recipe/capabilities/warning codes; full run in default lane (`test_conformance_corpus.py:40-44`, ≈20s), stratified sample `cases[::37]` (`:56-60`), domain slices | None needed | Full — sorted ids, duplicate-id fail-fast assert (`conformance.py` `build_conformance_corpus`) |
| Anti-claim + workflow contracts (`app/evaluation/anti_claim.py`) | HARNESS (plan warnings) + HARNESS-compile (15-stage `compile_workflow`, obligations, blockers, verdict V2) | "No denominator ⇒ no equity claim", "hazard ≠ risk", criteria/weights disclosure; `WC-kriging-blocked-no-field` proves BLOCKED_BY_METHOD survives a synthetically perfect render (`anti_claim.py:299-313`); 147-recipe compile sweep (`:320-324`) | None needed | Full; profiles are inline dicts (`_PROFILE_*`, `anti_claim.py:106-119`) |
| Reliability corpus (`app/evaluation/reliability_corpus.py`, 19 categories) + `simulate_agent_loop` (`app/evaluation/replay.py:143`) | TOOLS (+runtime invariants): dedupe, alias folding, no-progress, tier-3 destructive refusal, trace state machine (`check_trace_invariants`, `replay.py:237`) | Runtime invariants only, never GIS semantics; executed against a small fake registry in `tests/unit/test_trace_replay_v2.py` | **Scripted model**: `ScriptedCall` list replaces the model entirely (`replay.py:119`) | Full; registry-agnostic scripts |
| Cartography closed-loop gate (`tests/cartography/test_cartography_closed_loop.py`, `@pytest.mark.cartography`, CI `cartography-smoke` per `tests/test_ci_local_gate_contract.py:89-92`) | DATA→CARTO→MAP→VERIFY: MapSpec transactions (rollback on invalid mutation, `:48-84`), ref resolution incl. cross-session leak & type mismatch (`:122-163`), validity ladder via real engine + `PiAgentHarness` (`:186-204`, `:404-428`), `evaluate_cartography_semantics` (`:211-305`), fault injection (`:313-401`), gate fails on missing evidence (`:459-471`) | Last-known-good preserved; `not_evaluated` ≠ fake pass; `HarnessEvaluator` overall_passed False without cartography evidence | None — harness is fed via `record_tool_call/record_tool_result` with real engine outputs | Full (in-memory session store via conftest `USE_REDIS=false`); only fault injection uses `AsyncMock` |
| Golden corpus 503 goldens (`tests/cartography/golden_corpus/`, `corpus.py:48 build_cases`, 9 case kinds) | CARTO only: ComponentResolver → ComponentComposer → composition validation → layout solver | Structured digest (selection/components/stats — `corpus.py:566-672`) equals committed JSON; double-run digest equality (`test_golden_corpus.py:49`); planned-model never receives thematic bindings (`:43-56`); refresh only via `GOLDEN_CORPUS_UPDATE=1` then fails to force review (`:163-172`) | None | Full — same registry state ⇒ same case list and digest |
| Map product runtime v3 e2e (`tests/unit/test_map_product_runtime_v3_e2e.py`, 10 scenarios) + finalization scenarios A/B/C (`tests/unit/test_map_product_finalization_scenarios.py`) | TOOLS→CARTO→MAP→VERIFY: chart attach/reposition, CAS user-beats-agent, finalize evidence & fingerprint readback, Scenario C failure-recovery (hide → detect → repair → re-verify → complete; unfixable missing source ⇒ honest `not complete`) | Completion/verdict transitions, component lifecycle, zombie source cleanup | None — real `init_tools` registry + `MapSpecLifecycleEngine` + session store | Full |
| Multiturn scenarios (`tests/unit/gis_harness/test_multiturn_scenarios.py`, chains A–D) | HARNESS→MAP + versioning: show → heatmap → district stats → equity (denominator warning → verdict downgrade `:59-72`) → version adjudication; kriging recompute decision; MCDA decision panel; style-only restore | Cross-turn state (SessionPlan progress rows, five-diff recompute), honest downgrade of verdict | None — real intent/planner/MapProductService/DecisionEngineV3 | Full, but **hand-coded Python per chain**, not data-driven |
| Planning v3 adversarial scenarios (`tests/unit/test_planning_v3_scenarios.py`, 10 scenarios) | CHAT→HARNESS→TOOLS (plan orchestrator, plan mode, execution engine, dispatch) | Concurrency structurally (rendezvous), ref resolution, plan follow-through, call logs | **Planner LLM mocked at `app.services.chat.planner.call_llm` only** (`:20-24`); inline fake tools with tier/domain metadata (`:52-115`) | Full (no timing asserts) |
| Pi bridge E2E (`tests/test_pi_e2e.py`) + V5 acceptance S1–S10 (`tests/unit/test_v5_acceptance_scenarios.py`) | CHAT (HTTP `/chat/stream` → PiBridge → SSE) + spot TOOLS→ALG (S8: EPSG:3857 → attribute_filter → central_feature CRS-correct; S9: EPSG:4490 no fake CRS warning) | SSE event mapping, tool_call→step_result flow, 502 on RPC error, feature-flag gating, lock-leak/ghost-data/IDOR | **Recorded-event fake**: mock RPC client seeded with event factories from `tests/fixtures/pi_mocks.py` (`test_pi_e2e.py:41-69`) | Full for event mapping; dispatch service mocked in S-scenarios |
| Science oracle replay (`tests/science_oracles/test_oracle_replay.py`, 1,084 parametrized cases over 12 frozen JSONs) | ALG only (+ CRS classification edge cases: `EPSG:9999`, `"garbagexyz"`, `None` in `data/edge_cases.json`) | Hardcoded expected values (exact/allclose/error); zero recomputation on replay (`tests/science_oracles/__init__.py:1-20`) | None | Full — JSON is frozen snapshot of `scripts/gen_science_oracles.py` |
| Scenario matrix index (`tests/unit/test_scenario_matrix.py`) | meta: anchors scenarios A–G to named existing tests so coverage can't silently vanish | Import/hasattr checks of anchor tests | n/a | Full |
| Chaos suites (`tests/test_runtime_chaos_{engine,pi,lifecycle,plan_mode,resume,store}.py`) | CHAT/TOOLS runtime fault injection | Keepalive, retry, resume, store corruption | `pi_mocks` + `call_llm` patches | Mostly deterministic |
| G1/G2 hand-built e2e (`tests/unit/gis_harness/test_golden_cases_v2.py`) | TOOLS→CARTO→MAP: POI product (heatmap+points+components) → chart attach → fingerprint; G2 component-only move ⇒ layer array byte-identical (`cartographic_fingerprint`, `:1-22`) | Mutation scope discipline (style-only mutations don't rerun data tools) | None (real registry) | Full |
| Runtime V4 scenarios (`tests/unit/gis_harness/test_runtime_v4_scenarios.py`) | TOOLS→CARTO: chart `selectionField` autogen, component lifecycle create/duplicate/rebind/remove w/ CAS, raster first-class product sweep | Idempotency + MapSpec consistency across component ops | None | Full |
| Product closure scenarios (`tests/unit/gis_harness/test_product_closure_scenarios.py`) | MAP→(SessionPlan projection): chart debt + statistics alive ⇒ minimal debt + reusable inputs shown to Pi | End-to-end repair-debt projection contract; A/B/C/E/F/G/H matrix lives in `test_runtime_repair.py` | None | Full |
| Render observation (`tests/unit/gis_harness/test_render_observation.py`, `test_map_verification.py`) | CARTO→VERIFY: observation ingestion → completion status → repair classification | Observation-driven state transitions offline | None (observation dicts are inputs) | Full |
| Kriging vertical slice (`tests/unit/gis_harness/test_kriging_vertical_slice.py`) | HARNESS→TOOLS→ALG: explicit "kriging" request must not swap algorithms; sparse-sample fallback with evidence | Algorithm-identity + honest-fallback contracts | None | Full |
| V5 acceptance S8/S9 (`tests/unit/test_v5_acceptance_scenarios.py`) | TOOLS→ALG w/ real CRS semantics: EPSG:3857 filtered → central_feature geometry lands in the right CRS; EPSG:4490 must not raise a false CRS warning | CRS correctness across a 3-tool chain | dispatch service mocked; real GIS libs | Full |

Determinism is a first-class contract in this repo: the conformance corpus asserts byte-identical query lists across builds (`test_conformance_corpus.py:31-37`), the planner has an opt-in double-run determinism check (`case.py:104-105`, `runner.py:402-414`), the golden corpus asserts double-run digest equality (`test_golden_corpus.py:49`), and the perf harness self-skips in unfiltered runs to protect baseline isolation (`pytest.ini` markers; #664). Offline honesty is likewise codified: unmeasured metrics are recorded as `None`, never fabricated (`runner.py:1-7`), and a gate with no evidence fails closed (`test_cartography_closed_loop.py:459-471`).

---

## 3. Evaluation corpus architecture (`app/evaluation/`)

Layered by design (ADR-0092 B1: "a case is data; the runner interprets it", `case.py:1-6`):

1. **Case schema** — `case.py`: `ScriptStep` (`:14`, incl. `expect_error_contains` failure-semantics), `NumericAssertion` (`:27`, sources `step_result|step_result_bytes|mapspec|fixture|quantity`, aggs `value|len|sum|first|mean`, ops incl. `approx+tol`), `GISBenchmarkCase` (`:48`) with plan-tier contract (`expected_task(s)`, `expected_capabilities/optional`, `allowed/forbidden_algorithms`, `expected_recipe(s)`, `expected_product_facets`, `max_tool_calls`, `expected/forbidden_methodology_warnings`, `expected/forbidden_warning_codes`) and opt-in V3 contract (`expected_ontology_task`, `qualification_profile` → `compile_workflow` re-eval + fallback tier, `check_determinism` double-run, `case.py:100-116`).
2. **Runner** — `runner.py`: plan tier (`:188-345`, precision/recall metrics, honesty gating, facet contract `:417-460`, ontology/qualification/determinism V3 tier `:347-415`); execute tier (`:464-595`): materialize fixtures into session store with `fixture:<alias>` arg rewrite (`:486-508`), dispatch through the **real** `ToolRegistry`, MapSpec component assertions (`:555-568`), session artifact types (`:569-575`, via `app.services.artifact_registry.list_artifacts`), interaction-semantics probes (`:630-711`), numeric assertions (`:580-594`). Metrics are honest-None when unmeasured (`render_verified = None`, `:760`).
3. **Fixtures** — `fixtures.py`: 7 seeded zero-arg builders registered in `FIXTURE_BUILDERS` (`:149-157`); raster golden `ndvi_pair()` returns `(grid, grid, expected)` consumed via runner-computed `quantities` (`runner.py:597-615`), never the session store.
4. **Corpora** — golden (33), matrix (≥300 total), conformance (20,088), anti-claim (7 plan + 12 contract), reliability (19 categories), all plain Python constructors; **no YAML/JSON corpus for scenarios** (only science oracles are JSON-frozen).
5. **Composition** — `scenarios.py` binds plan + execute + contract cases into `GISScenario` (`:26-32`); `run_scenario` fails unless all three tiers pass (`:196-217`). This is the closest existing thing to a "scenario spec".
6. **Replay** — `replay.py` 3 modes: tool replay gated by descriptor `replay_safe is True` (never auto-executes destructive/external, `:57-110`), scripted agent-loop simulation (`:143`), trace invariants (`:237`).
7. **Metrics** — `runtime_metrics.py`: 5 deterministic metric families (tool retrieval via `AlgorithmRegistry.capability_tool_map()` reverse lookup `:24-45`, model routing, agent execution, GIS correctness, context budget `context_metrics` `:292`) — **defined but not wired into any scenario assertion today**.
8. **Entry points in CI** — `tests/unit/gis_harness/test_benchmark_harness.py` (golden+matrix ≥300, default lane), `tests/unit/gis_harness/test_conformance_corpus.py` (full 20,088 default lane + anti-claim + contracts + scenarios + 147-recipe sweep), `pytest -m cartography` gate, `pytest -m perf` for perf harness, nightly `runtime-validator` lane with `REQUIRE_BROWSER=1` (`pytest.ini` markers; `tests/unit/test_runtime_validator.py:5-10`).

---

## 4. Fixture inventory

| Fixture | Location | Content / shape | Notes |
|---|---|---|---|
| Chengdu school POIs | `app/evaluation/fixtures.py:15-30` | 60-pt seeded GeoJSON, 6 districts | Used by G1, S1; size-asserted |
| Large POI | `fixtures.py:33-47` | 150k-pt GeoJSON | Boundedness contract (G4) |
| Chengdu district polygons | `fixtures.py:50-65` | 6 coarse polygons w/ `population` | Admin aggregation denominator |
| OD edge tables | `fixtures.py:68-100` | `od_table` rows, 2k and 50k | Distinct-pair grid guarantee |
| NDVI pair | `fixtures.py:103-111` | constant 8×8 red/NIR grids, expected mean 0.25 | Lib-level raster golden via `quantities` |
| PM2.5 stations | `fixtures.py:114-145` | 240-pt gaussian-bump field; sparse 6-pt variant (below kriging gate) | Fallback goldens G32/G33 |
| Runtime scenario fixtures | `tests/fixtures/runtime/{heatmap-basic,step-fill,interpolate-circle,symbol-label,match-line,mvt-basic,raster-overlay,fault-missing-source,fault-wrong-color}/` | `mapspec.json` + `probes.json` (probe DSL: `layer-exists`, `feature-count`, `pixel-color`); 1 `points.geojson`, 1 `raster.json` | Contract-locked by `tests/unit/test_runtime_fixture_contract.py:1-33`; consumed by headless runtime validator |
| Data-fabric fake transport | `tests/fixtures/data_fabric/fake_server.py` | `FakeFabricAdapter` (HTTPAdapter) with route table, canned JSON, 302→169.254.169.254 SSRF redirect fault | Real SSRF validation path exercised |
| CoW large payloads | `tests/fixtures/mapspec_cow_fixtures.py` | 100k point / 50k line / 10k polygon deterministic generators | Perf + regression shared |
| Pi event factories | `tests/fixtures/pi_mocks.py:63-145` | token/tool_call/execution/agent_end/agent_settled/auto_retry events mirroring vendor protocol | The only shared LLM-adjacent fake |
| Compiler parity spec | `tests/fixtures/compiler_parity_mapspec.json` | single frozen MapSpec | |
| Science oracle data | `tests/science_oracles/data/*.json` (12 domains, 1,084 cases) | frozen `{id,target,args,expect}` | Includes CRS edge cases |
| Inline literals | e.g. `tests/cartography/test_cartography_closed_loop.py:39-40`, `tests/data/test_ingest_pipeline.py:20-27` | 1–3 point GeoJSON built per test | Dominant pattern outside evaluation |

**Missing fixture classes** (confirmed by search): **no temporal dataset** (time exists only as `hasTimeField`/`temporalObservationCount` profile keys in `anti_claim.py:172-188` and conformance families — never a real dated FeatureCollection that flows through dispatch); **no invalid-geometry corpus** (bowtie/self-intersecting/sliver polygons — validity only exercised opportunistically in unit tests like `tests/unit/test_spatial_buffer.py`); **no bad-CRS execute-tier dataset** (CRS faults live in `crs_safety` oracle cases and `tests/test_data_parser_missing_crs.py`, i.e. below the tool layer); **no raster file fixture** (rasters only as constant arrays; `tests/data/` contains test code, not data files; only `tests/fixtures/runtime/mvt-basic/points.geojson` is a real data file).

Fixture design properties worth preserving in a ScenarioSpec fixture protocol:
- **Seeded + offline**: every builder documents "seeded and offline: no network, no LLM, no wall-clock input" (`fixtures.py:1-6`) — determinism comes from `random.Random(seed)` with hardcoded seeds (42, 7, 11, 23, 31).
- **Bounded materialization**: large fixtures go straight into the session store, never into an LLM-facing payload (`runner.py:484-486` comment; G4's byte-bound assertion backs it).
- **Dual-form raster goldens**: `ndvi_pair()` returns arrays + expected scalar, routed through runner-computed `quantities` instead of tool dispatch (`fixtures.py:103-111`, `runner.py:597-615`) — the pattern to generalize for other raster indices.
- **Size-tiered variants** (`chengdu_schools` vs `_large`, `pm25_stations` vs `_sparse`, `od_edges` vs `_50k`): the corpus expresses cost/robustness contracts by pairing the same task family with different fixture tiers rather than different code paths — exactly the axis a generated scenario corpus should inherit.
- **Probe fixtures carry expectations, not just data**: `probes.json` includes `"expect": "fail"` (`tests/fixtures/runtime/fault-missing-source/probes.json`), i.e. negative outcomes are fixture-declared. This is the right shape for `ScenarioSpec.observe`/`negative`.

---

## 5. Stage-transition gap matrix

`C` = covered by at least one deterministic test *pairing* both stages; `P` = covered only in isolation (one side); `—` = no deterministic pairing.

| Transition | CHAT | HARNESS | TOOLS | DATA | ALG | ART | CARTO | MAP | VERIFY |
|---|---|---|---|---|---|---|---|---|---|
| **CHAT** | — | P (test_chat_engine_planning H-1; planning_v3) | — | — | — | — | — | — | P (pi_e2e SSE w/ mocked dispatch) |
| **HARNESS** | | — | C (runner plan→tool names; golden execute) | C (fixtures→store) | P (allowed/forbidden algo ids only) | P (expected_artifact_types after script) | C (component_assertions) | C (S-scenarios, facets) | — |
| **TOOLS** | | | — | C (fixture refs) | C (dispatch→lib) | C (artifact registry) | C (layer_upsert evidence) | C (webgis_map_product) | P (harness recorded manually) |
| **DATA** | | | | — | C | C | C | C | — |
| **ALG** | | | | | — | C | C (converter tests) | C | — |
| **ART** | | | | | | — | P (converter covered alone in `test_thematic_convergence.py:418`) | P | — |
| **CARTO** | | | | | | | — | C (finalize scenarios) | P (semantic checks; real observation only in heavy browser lane) |
| **MAP** | | | | | | | | — | P (verdict vs **synthetic** completion face, `anti_claim.py:299-313`) |
| **VERIFY** | | | | | | | | | — |

Concrete gaps a ScenarioSpec corpus must close:

1. **CHAT→HARNESS inside a scenario.** `GISBenchmarkRunner` starts at `resolve_map_request_intent(query)` (`runner.py:193`) — the chat layer (ChatEngine `_maybe_plan`, SessionPlan application, Pi turn plumbing) is never on the path of any evaluation case; conversely chat tests never reach real tools. No test drives *one user utterance* end-to-end with the deterministic planner standing in for the LLM.
2. **ART→CARTO→VERIFY in one chain.** `convert_analysis_to_mapspec_layer` and `evaluate_cartography_semantics` are each well covered, but no case asserts "this analysis artifact ⇒ this legend_spec/layer ⇒ zero error-severity findings ⇒ verdict". Scenario execute tier stops at "MapSpec exists + component types present" (`runner.py:552-568`).
3. **MAP→VERIFY with real observation.** Offline verdicts are computed from a synthetic `MapCompletionResult(status="complete", render_status="not_applicable")` (`anti_claim.py:302-307`); `render_verified` is honestly `None` (`runner.py:760`). Real observation evidence exists only via probes.json + headless Chromium in the nightly lane (`tests/unit/test_runtime_validator.py:5-10`) — never wired to a planner-seeded scenario.
4. **Budget limits.** Only plan-time `max_tool_calls` (`case.py:87`, `runner.py:301-306`) and one-off `step_result_bytes` bounds (G4/G12). No end-of-scenario ceilings for total tool calls, total result bytes, context tokens (`context_metrics` exists unused, `runtime_metrics.py:292`), retries, or wall-clock-free cost proxies.
5. **Trace assertions in scenarios.** `check_trace_invariants` (`replay.py:237`) and `TurnTrace` are exercised only by the reliability corpus against a fake registry — never alongside GIS semantics ("expected tool classes dispatched, no fallback event, no no-progress" is not expressible in `GISBenchmarkCase`).
6. **Adversarial data at execute tier.** Warning-code/qualification contracts (bad CRS, sparse samples, missing field) are all asserted against *profiles* (`compile_workflow`), never against an actually badly-CRSed or corrupt GeoJSON dispatched through transform → algorithm → product. The runner's `expect_error_contains` (`case.py:21-23`) is the only negative-execute hook and is barely used.
7. **Reusable spec structure: partially exists, inconsistently used.** `GISBenchmarkCase`/`GISScenario` are data-driven; everything at CHAT (planning_v3, pi_e2e, chaos) and most of MAP/CARTO (multiturn, finalization, runtime_v3) is **hand-assembled Python** with per-file fixtures and event lists. No single artifact can express "utterance → expected plan → script → artifacts → MapSpec → verdict → observation → budgets" today.
8. **Corpus sharding/lane metadata.** Conformance cases group by `conformance-*` Literal groups enabling domain slicing (`case.py:53-65`, `build_conformance_corpus(domains=...)`); golden/execute cases have no lane/tag concept, so a generated scenario corpus cannot yet declare "run me in the cartography gate" or "nightly only" without new plumbing.
9. **Warning-code and capability vocabularies are load-bearing but scattered.** Codes like `EQUITY_MISSING_DENOMINATOR` / `KRIGING_PROJECTED_CRS_REQUIRED` are asserted in anti-claim, conformance families, and S-scenarios; a single registry of codes with a "every code must appear in ≥1 execute-tier scenario" test would prevent codes that are only ever checked at plan tier.
10. **Honesty-of-skips is asserted but not for scenarios.** Golden OD cases skip honestly when flow tooling is unregistered (`golden_cases.py` docstring; `runner.py:479-482` records `skipped`), and `test_benchmark_harness.py` locks that behavior — but a generated full-pipeline corpus needs the same contract (skip must be visible in the report, never a green pass) once scenarios span optional tooling.

---

## 6. Fake provider status

**There is no central fake LLM provider.** Four coexisting strategies:

1. **LLM-free by architecture (evaluation corpora, harness/map suites):** the planner used by all evaluation is deterministic rules (`intent.py:5-13` design contract: "deterministic / 非 prompt-only / LLM 可补充 via `merge_intent_hints`"). `MapProductPlanner`, `compile_workflow`, `DecisionEngineV3` need no model. This is the repo's strongest pattern and the one a scenario corpus should build on.
2. **Per-test monkeypatching of ChatEngine internals:** `patch.object(engine, "_call_llm", AsyncMock(...))` / `_call_llm_stream` (`tests/test_chat_engine.py:29-36, 64-91`), plus manual stubs of `_get_or_create_session`, `_save_msg_async`, `_maybe_plan`, `_generate_title` (`:74-82`; same fixture re-declared in `tests/test_chat_engine_planning.py:13-27`). 27 test files patch a `call_llm` symbol (grep count), each with its own response shapes.
3. **Planner seam with a documented footgun:** `app.services.chat.planner` re-exports `call_llm`; the v3 scenario suite codifies that patching `app.services.chat.llm_client.call_llm` instead "silently hits the network" (`tests/unit/test_planning_v3_scenarios.py:20-24`). This is the closest to a named seam but is enforced only by docstring.
4. **Recorded-event fakes for the Pi path:** `tests/fixtures/pi_mocks.py` event factories + per-test mock `PiRpcClient` seeding an `asyncio.Queue` (`test_pi_e2e.py:41-69`). Shared factories exist, but the RPC wrapper is rebuilt per file.

Consequence: an LLM-behavior change (or seam rename) fails 27 files locally in different ways; and no test can *combine* scripted model output with the real GIS harness, because the scripted-loop machinery (`ScriptedCall`) targets a toy registry while `GISBenchmarkRunner` targets the real one.

---

## 7. ScenarioSpec DSL recommendations

**Extend, don't replace.** `GISBenchmarkCase` already owns plan/script/assert vocabulary; add the missing stages around it:

```python
class ScenarioSpec(BaseModel):
    meta: ScenarioMeta            # id, family, domain, tags, lane(default|cartography|perf|nightly)
    entry: ScenarioEntry          # query/utterance (+ variants), entry_point: "intent"|"chat"|"pi"
    plan: PlanContract | None     # ← existing GISBenchmarkCase plan-tier fields, reused verbatim
    data: ScenarioData            # fixture_aliases → FIXTURE_BUILDERS; optional adhoc inline docs
    script: list[ScriptStep]      # ← existing; keep fixture:<alias> rewrite
    artifacts: ArtifactContract   # expected_artifact_types, expected contract fields (app.lib.data.artifact_contract)
    map: MapProductContract       # component_assertions, expected_product_facets, expected_verdict,
                                  #   forbidden_verdicts, expected_warning_codes on the finalize face
    observe: list[Probe]          # reuse probes.json DSL: layer-exists|feature-count|pixel-color (structural,
                                  #   offline evaluator; browser probe execution stays a nightly lane)
    trace: TraceContract          # expect_tools_dispatched, forbid_tools, forbid_events [fallback,no_progress],
                                  #   dispatch_paired (check_trace_invariants), max_retries
    budget: BudgetContract        # max_tool_calls (exists), max_total_result_bytes, max_context_tokens,
                                  #   max_llm_planning_calls (=0 offline), max_retries
    negative: NegativeContract    # expect_warning_codes / forbidden (exists), expect_error_contains per step (exists)
```

Execution contract — one runner, three depths (all existing code paths):
- **depth 0 `plan`**: current plan tier (conformance corpus reuse).
- **depth 1 `scripted`**: current execute tier + new artifact/map/observe/trace/budget checks; entry_point `intent`.
- **depth 2 `loop`**: CHAT entry — route the utterance through `ChatEngine._maybe_plan`/SessionPlan apply (or `simulate_agent_loop` bound to the **real** registry instead of the toy one) with the deterministic planner answering, then hand off to depths 1's post-conditions. This is the only genuinely new machinery; everything else is assertion wiring.

Worked example — today's S-schools-distribution (`scenarios.py:58-83`) rewritten as one ScenarioSpec at depth 2:

```yaml
id: SC-full-schools-distribution        # stable, sorted, uniqueness-asserted
family: edu-facility-distribution       # audited family (reuse CONFORMANCE_FAMILIES id)
entry:  {entry_point: chat, query: "分析成都各区小学的空间分布情况"}
plan:   {expected_task: distribution_overview, expected_recipe: poi_distribution_overview,
         expected_capabilities: [poi_query, admin_aggregation], max_tool_calls: 8}
data:   {fixtures: [chengdu_schools, admin_boundaries_chengdu]}
script:                                 # ScriptStep list, unchanged semantics
  - {tool: webgis_map_product, args: {primary_ref: "fixture:chengdu_schools", query: "..."}}
artifacts: {expected_artifact_types: [vector]}
map:    {component_assertions: [title, north_arrow, scale_bar, attribution],
         expected_product_facets: [chart], expected_verdict: READY}
trace:  {expect_tools_dispatched: [webgis_map_product], forbid_events: [fallback, no_progress]}
budget: {max_tool_calls: 8, max_total_result_bytes: 262144, max_planning_llm_calls: 0}
```

Every field above already has a producer or assertion site in the codebase except `trace`/`budget` (counters exist in `CaseResult.metrics` and `TraceRegistry`) — the DSL is a re-packaging, not a new engine.

Fixture protocol: keep the registry-of-zero-arg-seeded-callables pattern (`FIXTURE_BUILDERS`); add `temporal_series`, `bad_crs_points`, `invalid_geometry_polygons`, `mixed_geom_table`, and a `raster_grids` family parameterized like `pm25_stations(n, seed)`. Fixtures stay pure data; materialization into `session_data_manager` with aliasing stays the runner's job (`runner.py:486-499`). Register every builder in a `tests/unit/test_scenario_fixture_contract.py` that pins shapes (mirror `test_runtime_fixture_contract.py`).

Assertion vocabulary (single source, reused across suites): warning codes (already stable, `case.py:97-98`), verdicts (`derive_product_verdict`), facet kinds, probe types, trace events (`app/lib/runtime/trace.py` constants), numeric assertion mini-language, budget counters from `CaseResult.metrics`. Promote `runner.py:757-779`'s None-metrics into filled values wherever a contract declared them.

---

## 8. Corpus generation recommendations

Follow the two proven generator patterns rather than hand-written YAML:

1. **Frozen-JSON replay (science-oracles pattern).** `scripts/gen_science_oracles.py` computes expectations once with reference formulas, writes deterministic JSON (`tests/science_oracles/data/<domain>.json`), and the replay test re-computes nothing and hard-fails if the corpus is empty (`test_oracle_replay.py:19-21`). A `scripts/gen_scenarios.py` should emit `tests/scenarios/data/<domain>.json` (list of ScenarioSpec) with stable sorted ids; `tests/scenarios/test_scenario_replay.py` validates schema, id uniqueness (fail-fast like `build_conformance_corpus`), and runs cases at declared depths.
2. **Audited family × deterministic expansion (conformance pattern).** Model full-pipeline scenarios as `ScenarioFamily` (audited: task family × data condition {normal, sparse, large, bad-crs, invalid-geom, temporal, missing-field} × expected verdict class) × deterministic expansion (utterance variants — reuse `SCOPE_VARIANTS`/`UTTERANCE_VARIANTS`, `conformance.py:26-55`). Only the family table is hand-audited; the expansion is mechanical, guaranteeing the semantic-identity invariant generalizes to the execute tier.
3. **Drift/determinism tests, not golden screenshots:** double-run digest equality (`test_golden_corpus.py:49`), corpus-size floors (`:36`, `test_benchmark_harness.py:25`), domain-coverage completeness (`test_conformance_corpus.py::test_corpus_covers_all_pack_domains`), and scenario-anchoring index (`test_scenario_matrix.py`) so suites can't be silently deleted.
4. **Update guard for any stored digest:** copy the `GOLDEN_CORPUS_UPDATE=1` write-then-fail pattern (`test_golden_corpus.py:163-172`).
5. **Lane wiring:** default lane runs family-table spot scenarios + stratified sample (mirror `cases[::37]`); `-m cartography` gate runs MAP/CARTO/observe-heavy specs; nightly runs full expansion + `REQUIRE_BROWSER` probe execution — matching existing `pytest.ini` markers and `test_ci_local_gate_contract.py` assertions.
6. **Reuse the catalog derivative pattern for docs:** `gen_workflow_catalog.py --check` and `gen_science_catalog.py` prove the "registry is truth, docs are projections" contract; the scenario generator should likewise project from `RecipeRegistry` + `FIXTURE_BUILDERS` + warning-code tables so newly added recipes automatically get scenario skeletons (failing until a family is audited), exactly as `test_corpus_covers_all_pack_domains` forces family coverage today.
7. **Metric extraction stays offline:** `runtime_metrics.py` already computes retrieval/routing/execution/GIS/context metrics from case runs with truth derived from `expected_capabilities` ("不引入新的人工标注真相", `runtime_metrics.py:10-13`). The scenario generator should emit the same `CaseResult` shape so report aggregation (`app/evaluation/report.py`) works unchanged.
8. **Expansion math must stay legible:** the conformance corpus's 59 × 12 × 9 × 2 = 20,088 is documented in the test docstring (`test_conformance_corpus.py:6-7`) and the count is a floor assertion, not an exact one — a scenario corpus should do the same (floor + composition formula in the generator docstring) so reviewers can see growth without diffing thousands of cases.

### Priority order (highest leverage first)
1. Wire `convert_analysis_to_mapspec_layer` + `evaluate_cartography_semantics` + real `derive_product_verdict` into the execute tier (closes ART→CARTO→VERIFY, mostly existing pieces).
2. Add `entry_point="chat"` depth-2 execution using the deterministic planner (closes CHAT→HARNESS).
3. Add trace + budget contract sections and fill `runner.py` None-metrics.
4. Add the 4 missing fixture families; port 3–5 anti-claim contracts from profiles to real dispatched data.
5. Then generate the corpus (family table + expansion) on top.
