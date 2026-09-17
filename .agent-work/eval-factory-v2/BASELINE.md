# BASELINE — GIS Agent Evaluation System at origin/master faa453a8

- **Baseline SHA**: `faa453a8935101378c23eb6694a42c3616d9c670` (2026-09-15 20:46 +0800, `feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (Direction 04) (#1329)`)
- **Recorded**: 2026-09-16
- **Worktree**: `C:\Users\wangj.KEVIN\projects\webgis-wt-eval-factory-v2` (branch `eval/gis-agent-benchmark-factory-v2`)
- **Recently merged into baseline**: #1320 Durable GIS Mission Runtime, #1321 cartography feedback evaluation (visual + template/codegen + GIS axes), #1326 fail-closed durability fixes, #1327 Production GIS Skill Policy, #1328 Spatial Evidence/Claim/Provenance Graph, #1329 Hot-path Convergence.

## What the evaluation system is at baseline

The evaluation stack is a **deterministic-first, offline, zero-LLM benchmark factory** (ADR-0092 B1–B5, extended by ADR-0101/0103/0104/0118/0119/0159/0183/0197). Its central contract is `GISBenchmarkCase` (`app/evaluation/case.py`): a declarative pydantic case that a single runner (`app/evaluation/runner.py::GISBenchmarkRunner`) interprets. Every verdict comes from schema assertions, planner evidence, tool traces, MapSpec state, or numeric goldens — the runner docstring states verdicts are *never* from an LLM judge, and unknown measurements are recorded as `None` ("honest n/a") rather than fabricated.

Around that core there are 10+ specialized corpora, each with its own case dataclass + deterministic builder + a dedicated evaluation entry point (plan-tier contracts, workflow-contract compilation, goal satisfaction, failure taxonomy, chaos invariants, closed-loop verdicts, reliability scripts, tool retrieval, methodology semantics, runtime situations, composite multi-turn scenarios). A second, independent replay stack (`app/lib/harness/replay/**`, CLI `scripts/replay_bench.py`) packages production 18-stage evidence chains into versioned `ReplayTrace` v1 envelopes and replays recorded ops offline with a deterministic judge.

**No single manifest enumerates all cases.** Counts are implicit in builders and pinned by regression-lock tests (e.g. `tests/quality/test_quality_scenario_corpus.py` asserts >=5000).

## Module inventory (all under `app/evaluation/`)

| Module | Lines | Purpose (one line) |
|---|---|---|
| `case.py` | 155 | `GISBenchmarkCase` / `ScriptStep` / `NumericAssertion` pydantic contract (ADR-0092 B1) |
| `runner.py` | 865 | `GISBenchmarkRunner`: plan tier (intent+planner) + execute tier (real registry dispatch) → `CaseResult` |
| `report.py` | 83 | Markdown report over `CaseResult.metrics` (fixed `_METRIC_ORDER`, pass/fail/skipped + failures) |
| `fixtures.py` | 157 | Seeded offline fixture builders (chengdu_schools, 150k POI, admin boundaries, OD edges, ndvi_pair) |
| `golden_cases.py` | 486 | 33 hand-written `GOLDEN_CASES` + `get_all_cases()` = 33 golden + 273 matrix = **306** |
| `case_matrix.py` | 622 | `build_matrix_cases()` = **273** generated cases (16 families × utterances + negative/form/scope/decision/compound/style) |
| `conformance.py` | 683 | 59 audited `ConformanceFamily` × 12 scopes × 9 utterances × zh/en → **20,088** plan-tier cases |
| `quality_corpus.py` | 1350 | Quality scenario corpus: conformance families × 12 data-state profiles + cartography/agent families → **6,406** cases |
| `anti_claim.py` | 326 | 7 NL anti-claim plan cases + 11 `WorkflowContractCase` (compile-level obligations/blockers/verdicts) + 147-recipe sweep list |
| `goal_satisfaction_corpus.py` | 1164 | **104** `GoalSatisfactionCase` over `evaluate_goal_satisfaction` (false_pass_rate must be 0) |
| `failure_corpus.py` | 260 | **17** `FailureCase` across 8 fault categories → `classify_harness_failure` + `remediation_for` budget ladder |
| `chaos_corpus.py` | 152 | **17** `ChaosScenario` metadata rows, each pinned to a *real collectable* pytest node id |
| `closed_loop_corpus.py` | 199 | **204** six-segment closed-loop scenarios (17 map types × failure × repair × verdict) |
| `reliability_corpus.py` | 140 | **19** scripted `ReliabilityCase` (registry-agnostic `ScriptedCall` sequences) |
| `methodology_corpus.py` | 240 | **20** audited bilingual methodology cases (semantic-plan expectations, hard negatives, anti-leakage red line) |
| `scenarios.py` | 221 | 7 end-to-end `GISScenario` (plan + execute + contract cases per scenario) |
| `scenario_corpus.py` | 421 | Template×slot pack expansion → **2,316** default cases + `coverage_report` + `evaluate_scenarios` |
| `runtime_corpus.py` | 544 | **3,456** `RuntimeCase` (18 situations × 8 families × 3 scopes × 2 langs × 4 utterances) + **126** `CompositeScenario` (2-turn) + **60** `RuntimeExecutionCase` (real dispatch) |
| `retrieval_corpus.py` | 169 | **3,028** `ToolRetrievalCase` (intent + paraphrase + conformance-derived), `get_retrieval_sample(800)` |
| `retrieval_eval_corpus.py` | 1456 | **598** human-gold `RetrievalEvalCase` (direct/near_duplicate/hard_negative/ambiguous/paraphrase/out_of_scope) + `RetrievalEvalReport` (p@1, r@5, r@10, invalid_selection_rate, fallback_rate, tier3_leak, ECE, abstention) |
| `runtime_metrics.py` | 434 | Metric library: retrieval r@k/p@k/irrelevant, routing, execution, GIS correctness, context overflow/schema ratio |
| `replay.py` | 396 | 3 replay modes (`replay_tools` replay_safe-only, `simulate_agent_loop` scripted loop w/ invariants, `check_trace_invariants`) + A/B comparators + `chain_completeness_report` |
| `chain_gate.py` | 120 | Evidence-chain completeness gate (>=0.95 or `expected_stages` subset) over session JSONL (`trace_store.read_chains`) |
| `conformance.py` gate? | — | (same file as corpus; the *gate* aspect is the corpus itself + `tests/unit/gis_harness/test_conformance_corpus.py`) |

### Independent replay/bench stack: `app/lib/harness/replay/**` (2,968 lines)

`schema.py` (`ReplayTrace` v1: 18-stage chain + turn envelope + verdict, 512 KiB budget, behavior digest, additive-only), `replayer.py` (`Scenario`/`TurnSpec`/`ScenarioOp`, canned production-receipt-shaped ops, sandboxed mutation store, offline judge env, `OfflineReplayer`), `scenarios.py` (140-scenario matrix: 104 core single/double-turn × 8 variants + 36 multi-turn 3–8 turns; suite tags incl. `multi-turn`, `faults`), `bench.py` + `scripts/replay_bench.py` (CLI: `--suite all|core|multi-turn|faults`, `--seed`, `--baseline` diff, `--only-failed`, resume, JSON/CSV/MD), `ratchet.py` (tolerant ratchet rows), `determinism.py` (canonical json + behavior digest), `recorder.py`, `faults.py`, `triage.py`, `explain.py`, `sanitize.py`.

### Entry points

- `GISBenchmarkRunner.run_case/run` (`app/evaluation/runner.py:778,858`)
- `run_workflow_contract_case` (`app/evaluation/anti_claim.py:230`), `build_v2_recipe_coverage_sweep` (147 recipes)
- `evaluate_goal_satisfaction` + corpus driver (`app/services/gis_harness/goal_satisfaction/`)
- `evaluate_failure_case` (`app/evaluation/failure_corpus.py:218`)
- `simulate_agent_loop` / `replay_tools` / `check_trace_invariants` (`app/evaluation/replay.py`)
- `retrieval_eval_report` (`app/evaluation/retrieval_eval_corpus.py:1311`), `retrieval_metrics`/`surface_retrieval_report` (`runtime_metrics.py`)
- `run_chain_gate_for_session` (`app/evaluation/chain_gate.py:99`)
- CLI: `scripts/replay_bench.py`, `scripts/ads_gen_retrieval_eval.py`, `scripts/ads_gen_time_eval.py`, `scripts/gen_science_benchmark_manifest.py`
- Consumption: `tests/quality/**` (433 tests collected), `tests/harness_replay/**` (72), `tests/unit/gis_harness/test_conformance_corpus.py`, `test_goal_satisfaction_corpus.py`, `test_runtime_corpus_v4.py`, `test_closed_loop_corpus_v6.py`, `test_skill_policy_v1.py`, `tests/cartography/**`

## Case counts (concretely measured by importing builders, 2026-09-16)

| Corpus | Count | Deterministic? |
|---|---|---|
| golden_cases | 33 (+273 matrix = 306 via `get_all_cases`) | yes, id-ordered |
| conformance | 20,088 (59 families) | yes, id-sorted, dup-id assert |
| quality_corpus | 6,406 | yes, id-sorted, dup assert |
| scenario_corpus (default) | 2,316 | yes (template×slot cartesian) |
| runtime_corpus | 3,456 + 126 e2e + 60 exec | yes |
| retrieval_corpus / retrieval_eval | 3,028 / 598 | yes, sorted |
| goal_satisfaction | 104 | yes |
| closed_loop | 204 | yes |
| anti_claim | 7 plan + 11 contract | yes |
| failure | 17 | yes |
| chaos | 17 | yes |
| reliability | 19 | yes |
| methodology | 20 | yes |
| scenarios (GISScenario) | 7 | yes |
| **Total benchmark-style cases** | **~36,900** | all offline/zero-LLM |

## Test paths & conventions (detail in TEST_MATRIX.md)

- `tests/quality/` — 433 tests collected in 85s (collect-only, 2026-09-16, no errors). Shared conftest: tmp sqlite dual-engine for cartography quality facts.
- `tests/harness_replay/` — 72 tests collected in 79s (no errors). 7 files, 63 test functions; `pytestmark = pytest.mark.cartography` on bench/corpus/ratchet files.
- pytest.ini: `asyncio_mode=auto`, `timeout=60` (thread), markers `heavy|perf|cartography|real_services`, addopts include `--cov=app`.
- Suite markers per file: corpus regression locks live in `tests/unit/gis_harness/test_*_corpus.py` and shard by `scenario_kind` prefix / family to stay under the 60s timeout.

## Open-PR overlap at baseline (detail in PARALLEL_OWNERSHIP.md)

- **#1335** (fail-closed claim/tenant/mission): touches `evidence_claim/{grounding,verify,census}.py`, `hotpath_convergence/{claim_ingest,pi_card,session_ctx}.py`, `mission_runtime/{service,store}.py`, `completion/pipeline.py` + 2 unit tests. **Zero overlap** with `app/evaluation/**`, `tests/quality/**`, `tests/harness_replay/**` — but it is the production code a V2 evidence/mission evaluator would drive.
- **#1336** (GeoAI promptable platform): touches `app/lib/modelops/**`, `app/api/routes/geoai.py`, `tests/unit/modelops/**`, `tests/integration/modelops/**`, `tests/unit/gis_harness/test_capability_graph_v8.py`, **`tests/conftest.py`**, `CHANGELOG.md`. **Zero overlap** with the three eval test dirs; root conftest is the one shared file.
