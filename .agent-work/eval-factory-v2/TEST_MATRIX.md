# TEST MATRIX — proposed targeted test paths & commands for V2 work

## Baseline collection counts (measured 2026-09-16, faa453a8, Windows/Git Bash, anaconda python 3.13)

| Path | Collected | Time | Errors |
|---|---|---|---|
| `tests/quality` | **433 tests** | 84.99s collect-only | none |
| `tests/harness_replay` | **72 tests** | 78.54s collect-only | none |

Collect commands used (record verbatim):
```
python -m pytest tests/quality --collect-only -q | tail -5        # → "433 tests collected in 84.99s"
python -m pytest tests/harness_replay --collect-only -q | tail -3 # → "72 tests collected in 78.54s"
```
Note: pytest.ini `addopts` includes `--cov=app`, which slows collection noticeably; add `--no-cov` for local iteration. Full suites were NOT run (per mission constraints) — no baseline run-failure data recorded; collection is clean on both paths.

## pytest conventions that bind new tests

- `pytest.ini`: `testpaths=tests`, `pythonpath=.`, `asyncio_mode=auto` (async tests need no `@pytest.mark.asyncio` but existing files still use it — harmless), `asyncio_default_fixture_loop_scope=function`, `timeout=60` (`timeout_method=thread`), markers: `heavy`, `perf` (self-skips in unfiltered runs), `cartography` (deterministic release-blocking gate, no Node/LLM/network — used by all `tests/harness_replay` corpus files), `real_services` (needs REAL_SERVICES=1).
- Corpus tests stay fast by **sharding**: `tests/quality/test_quality_scenario_corpus.py::SCENARIO_PREFIXES = ("basemap_","science_","cartography_","data_","agent_")` slices `build_quality_scenario_corpus()` per test; each slice documented ≪60s. Same for conformance (`tests/unit/gis_harness/test_conformance_corpus.py` runs by domain/family shards).
- Hermetic DB pattern: `tests/quality/conftest.py` builds tmp sqlite (sync engine + aiosqlite async_sessionmaker patched into `app.core.database.AsyncSessionLocal`) — reuse for mission/evidence corpora needing persistence.
- Parallel safety (`-n 2` max): corpus builders are per-process pure functions; runner cases create unique `bench-<uuid10>` sessions; mapspec/session stores are keyed by session id → xdist-safe in principle. Caveats: (a) `runtime_metrics.retrieval_metrics` mutates a shared `DynamicToolSurface` per call (fine, constructed locally); (b) any test that flips env kill-switches (`GIS_SKILL_POLICY`, `GIS_MISSION_HOTPATH`) must use monkeypatch (xdist-safe per worker); (c) `perf` marker tests assume isolated execution and self-skip in unfiltered runs — keep them out of V2 default commands.

## Proposed V2 test files (new; all in free zones per PARALLEL_OWNERSHIP.md)

| New file | Covers | What it asserts (shape) |
|---|---|---|
| `tests/quality/test_v2_case_manifest.py` | D3 index | manifest counts match per-corpus `len()`; id uniqueness across corpora; stable ordering; expectation-table hash stability |
| `tests/quality/test_v2_hard_negative_corpus.py` | D9 | full-corpus replay sharded by family prefix; wrong-AOI/no-capability honest-degradation codes present; point-vs-choropleth recipe disjointness |
| `tests/quality/test_v2_skill_policy_corpus.py` | D5/D7 | all 6 modes covered by corpus rows; kill-switch row → `none`; determinism (double resolve); `execute_guided` at least one row; corpus replay ≤ shard budget |
| `tests/quality/test_v2_evidence_corpus.py` | D8 | unsupported-by-default narrative claim; stale propagation to descendants; contradiction pair; positive-proof acceptance; cross-tenant rejection; grounding projection bounded shape. **Consumer of ClaimStore only; no edits to #1335 files** |
| `tests/quality/test_v2_security_corpus.py` | D5 | injection payloads: no forbidden algorithms/network tools selected; no fabricated warning codes; honest refusal/degradation codes present |
| `tests/quality/test_v2_cartography_axes_corpus.py` | D10 | overlap/offscreen fixture → layout findings fire; CVD-unsafe palette → finding; template/codegen axis scores on fixture MapSpecs; visual axis `not_evaluated` when no facts (honesty) |
| `tests/quality/test_v2_report_diff.py` | D6 | `aggregate_by_group`, `render_json` stability, `diff_against_baseline` new-failures/fixed buckets, ratchet rows tolerant-band behavior |
| `tests/harness_replay/test_mission_replay_v2.py` (marker `cartography`) | D4 | mission create→turns→interrupt(timeout sig)→resume→artifact reuse→cancel; deterministic double-run behavior digest equality; sqlite MissionStore tmp factory; frozen `_utcnow` |
| `tests/harness_replay/test_multiturn_turns_v2.py` | D1/D4 | `GISBenchmarkCase.turns` executed with session continuity; coreference turn-2 re-binding expectation codes actually evaluated |
| `tests/unit/gis_harness/test_v2_runner_tiers.py` | D5 | opt-in tier units: undeclared fields ⇒ metrics None (honesty), declared ⇒ asserted failures messages |

## Commands (xdist capped at `-n 2` per mission constraint)

```bash
# fast inner loop (no coverage, one shard)
python -m pytest tests/quality/test_v2_skill_policy_corpus.py --no-cov -q

# corpus regression locks (V2 additions)
python -m pytest tests/quality -k "v2" --no-cov -q -n 2

# mission/multiturn determinism (replay dir; cartography marker included by default filters)
python -m pytest tests/harness_replay --no-cov -q -n 2

# existing baseline gates that must stay green while V2 lands
python -m pytest tests/quality/test_quality_scenario_corpus.py tests/quality/test_quality_report.py --no-cov -q -n 2
python -m pytest tests/unit/gis_harness/test_conformance_corpus.py tests/unit/gis_harness/test_goal_satisfaction_corpus.py --no-cov -q -n 2
python -m pytest tests/harness_replay/test_replay_corpus.py tests/harness_replay/test_replay_ratchet.py tests/harness_replay/test_replay_bench_cli.py --no-cov -q

# schema/contract sanity after case.py edits (additive-only proof)
python -m pytest tests/unit/test_quality_cases_matrix.py tests/quality/test_quality_scenario_corpus.py -k "schema or field or count" --no-cov -q

# collection check before pushing
python -m pytest tests/quality tests/harness_replay --collect-only -q | tail -3
```

## Existing targeted paths that consume what V2 touches (regression watchlist)

- `app/evaluation/case.py` edits → `tests/unit/test_quality_cases_matrix.py`, `tests/quality/test_quality_scenario_corpus.py`, `tests/unit/gis_harness/test_conformance_corpus.py`, `tests/unit/gis_harness/test_runtime_corpus_v4.py`
- `runner.py` edits → the corpus suites above + `tests/unit/gis_harness/test_goal_satisfaction_corpus.py` (independent driver but same honesty conventions)
- `report.py` edits → `tests/quality/test_quality_report.py` (different report — confirm no import coupling), any test importing `render_markdown` (grep before signature changes)
- mission driver → `tests/unit/gis_harness/test_resume_anchor_v5.py`, `test_recovery_ledger_v6.py`, `test_durable_context_continuation_v6.py` are the pinned chaos nodes (read-only: V2 must not weaken them; chaos corpus gate asserts their collectability)
- policy tier → `tests/unit/gis_harness/test_skill_policy_v1.py` (existing unit truth)
- evidence tier → `tests/unit/gis_harness/test_evidence_claim_graph_v1.py` (#1335-owned — run it, never edit it)
