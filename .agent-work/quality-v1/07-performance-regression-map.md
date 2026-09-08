# 07 — Performance Regression Map

Repo audited (read-only): `/home/kevin/projects/webgis/webgis-ai-agent-quality-v1`
Scope: backend perf infrastructure (`tests/benchmarks/`, `tests/perf/`), CI lanes, frontend render-count tests, app-side metric emission.

---

## 1. Executive summary

- The repo has **two distinct perf philosophies already in tension**: (a) a wall-clock median-vs-baseline harness (`tests/benchmarks/test_perf_harness.py`, `test_transport_perf.py`) gated on an isolated CI lane, and (b) an emerging **structural / work-count** style (`tests/perf/test_runtime_v2_perf_contracts.py`, `test_perf_mapspec_mutation_cost.py`, `test_llm_http_pooling_perf.py`, `test_geobench_v3.py`, `tests/perf/test_mvt_cache_pressure_benchmark.py`) that asserts counters, bytes, and call counts. The structural style is explicitly blessed in-repo ("不用脆弱 wall-clock", `tests/perf/test_runtime_v2_perf_contracts.py:4`; "Wall-clock rows ... NEVER gate", `bench_geocompute_v4.py:2-4`) — a structural-metrics-first harness should formalize (b), not invent a new one.
- **Baseline mechanics**: `baselines.json` = `{workload: {median_ms, iterations, floor_ms?}}`; gate is `median ≤ max(floor, baseline×1.75)` warn / `×4.0` fail; missing baseline fails closed; refresh only via `PERF_UPDATE_BASELINES=1` (`tests/benchmarks/_baseline_policy.py`).
- **Isolation (#664)**: `pytest_collection_modifyitems` in `tests/conftest.py:128-154` appends a visible skip to every `perf`-marked item unless the literal token `perf` appears in `-m`. So adding `@pytest.mark.perf` to a new structural-gate file automatically opts it into the isolated `test-perf` CI lane without breaking the main lane.
- **Key gap**: several wall-clock files **without** the `perf` marker (`test_gis_registry_perf.py`, `test_template_registry_perf.py`, `test_planner_runtime_perf.py`, `test_pi_perf.py`, `test_data_runtime_v2_perf.py`, `test_geobench_v3.py`) run inside the **main PR lane under `--cov`**, where their ms ceilings are machine/coverage-sensitive by construction — exactly what the structural harness should absorb.
- CI: PR `test-perf` lane (`.github/workflows/production.yml:340-380`) runs 9 explicit perf files with `--no-cov`; nightly runs `-m "cartography or perf"` (`:531`); main lane runs `-m "not perf and ..."` (`:192`).

---

## 2. Perf harness architecture (end-to-end)

### 2.1 Harness v1 — `tests/benchmarks/test_perf_harness.py`

- **What it measures**: wall-clock `time.perf_counter()` ms, median of 7 iterations (`ITERATIONS = 7`, `:60`; `statistics.median(...)` at `:621`). 15 workloads registered in `WORKLOADS` dict (`:575-593`) covering: raster guard rejection, ref resolution batching (Redis RTT collapse), metrics enqueue, dispatch wrapper overhead, windowed reclassify, H3 binning 10k, artifact cache hit, raster tile streaming (256px PNG), network snapping (STRtree cache), geojson bbox 10k, `_estimate_json_bytes` 10k, quality-audit topology (cap), 2-opt ladder n=160, indexed barriers over 50k-edge grid, closest-facility 30×40.
- Each workload is a pure function returning ms; fixtures are synthetic (seeded `np.random.default_rng`) and self-cleaning; graph/grid builds are hoisted into module caches (`_perf_grid_112`, `:458-483`) so the measured span excludes equal-before/after setup.
- **Gate** (`test_perf_workload`, `:620-657`): `measured > fail_at` → hard fail; `measured > warn_at` → fail (warn band; TEST-01 comment `:643-655` documents why the old `pytest.skip` was replaced by a visible fail); else pass. `warn_at = max(floor_ms, baseline×1.75)`, `fail_at = max(floor_ms, baseline×4.0)` (`:632-635`).
- Docstring states gate semantics + usage including `PERF_UPDATE_BASELINES=1` and `ALLOW_MISSING_PERF_BASELINE=1` (`:9-23`).

### 2.2 Harness v2 — `tests/benchmarks/test_perf_harness_v2.py`

- **Event-loop lag monitor** (not baselined): `EventLoopLagMonitor` ticks every 5–10 ms and records `(elapsed − interval)` as lag; asserts `max_lag_ms < 25` for network-solver and temporal-engine offload (`:41-81`, `:115`, `:141`). This is a **structural-ish scheduling property**, not a baseline comparison.
- Plus behavior checks: data-fabric adapter routing (`:144-179`), telemetry-v2 fields on dispatch (`:183-197`), raster-tile LRU (second call `dur2 <= dur1 + 5 ms`, `:230` — a weak wall-clock assertion).
- Whole module `pytestmark = pytest.mark.perf` (`:35`) — with a comment (#564) explaining it must live in the perf lane because the lag assertion is flaky under coverage.

### 2.3 Transport harness — `tests/benchmarks/test_transport_perf.py`

- Same pattern as v1 (own `WARN_FACTOR=1.75 / FAIL_FACTOR=4.0`, `:47-48`; median of 7) but with **its own baseline file** `tests/benchmarks/transport_baselines.json` (5 workloads: `concurrent_stream_p95_8`, `pi_token_batch_coalesce_200`, `sse_batcher_coalesce_500`, `sse_serialization_10k`, `stream_first_event_pi`). SSE/Pi streaming hot path.

### 2.4 Isolation (#664)

- `tests/conftest.py:128-154`: `pytest_collection_modifyitems` inspects `config.option.markexpr`; if the token `perf` is absent, every item with `get_closest_marker("perf")` gets a **visible skip** ("perf 基线要求隔离运行… `pytest -m perf --no-cov`"). Rationale (comment `:129-140`): a full-suite run interleaves perf items mid-suite among ~4500 tests → median noise (measured 0/4/7 failures on clean master). Behavior is locked by subprocess two-state test `tests/unit/test_perf_isolation_wiring.py`.
- Deliberate token match (not `item.keywords`) so that **directory name `tests/perf/` does not trigger isolation** — unmarked functional tests in `tests/perf/` run in the main CI lane (`:150-152`).
- CI wiring: main lane `-m "not perf and not cartography and not real_services"` (`production.yml:192`); PR perf lane runs 9 listed files `-m perf --no-cov --timeout=180` (`:380`); nightly `-m "cartography or perf" --no-cov` (`:531`). The PR lane's explicit-file list (not bare `-m perf`) is the actual gate for what runs on PR.

---

## 3. Baseline policy & format

- **Format** (`tests/benchmarks/baselines.json`): flat dict; per workload `{"median_ms": <float>, "iterations": 7, "floor_ms": <optional float>}`. 15 entries; floors present only for workloads whose warn band would otherwise sit inside scheduler noise (e.g. `raster_tile_streaming` median 3.5 ms with `floor_ms: 15.0`, rationale in docstring `test_perf_harness.py:370-387`).
- **Policy** (`tests/benchmarks/_baseline_policy.py`): `decide_baseline_action(name, baselines)` → `"record"` if `PERF_UPDATE_BASELINES=1` or (missing and `ALLOW_MISSING_PERF_BASELINE=1`); `"compare"` otherwise; **missing baseline with no opt-in → `pytest.fail`** (fails closed, `:47-52`). Unit-locked by `tests/benchmarks/test_baseline_policy.py` (incl. JSON shape of the record path, `:62-64`).
- **Record flow**: harness writes `{median_ms: round(measured,3), iterations}` back into baselines.json and skips (`test_perf_harness.py:624-630`). Refresh flow is manual + documented (`PERF_UPDATE_BASELINES=1`, "only after a measured improvement", fail message `:640-642`).
- `tests/benchmarks/_master_context_baseline.py` is a different, one-off mechanism: runs in the pristine master checkout via `PYTHONPATH` to produce a JSON **context-bytes** baseline (registry size, per-turn selection count/chars/tokens/active domains, 4-turn/2-turn context block breakdown) consumed by `bench_planning_v3.py` — measurement, not a gate.

---

## 4. Existing structural metrics inventory

Structural (machine-independent) counters already asserted somewhere:

| Metric | Emitted/asserted at | Consumer |
|---|---|---|
| Full `get_map_state` read count / `get_state_field` count | monkeypatched counters in test (`tests/perf/test_runtime_v2_perf_contracts.py:62-88, 97-126`) | PC-1 (`1 <= full_reads <= 4`, batch revision `== rev0+1`), PC-2 (`full_reads <= 1`, `field_reads >= 2`) |
| SQL statement count (SQLAlchemy `before_execute` event) | `_count_queries` helper (`tests/perf/test_context_assembly_baseline.py:91-103`; `tests/perf/test_project_context_cache.py:136-148`) | project-block: `<= 25` queries / 5 rounds and `< 50` baseline (`:181-190`); `<= 40` and `< 100` for 10 rounds (`:218-227`); "no project_id → 0 queries" (`:400`) |
| Lineage-table SELECT count vs depth | event listener counting `artifact_lineages` selects (`tests/benchmarks/test_provenance_perf.py:125-146`) | `queries["n"] <= depth + 2` |
| JSON byte sizes written to disk | spy on `_atomic_write_json_sync` summing `len(json.dumps(...))` (`tests/benchmarks/test_perf_mapspec_mutation_cost.py:83-104`) | SetView writes `< 100 KB` (vs 12 MB inline); similar for upsert (`:125+`) and mutation copy (`:164+`) |
| LLM payload bytes after slimming | `len(slim.encode())` (`tests/benchmarks/test_data_runtime_v2_perf.py:112-122`) | 150k-feature FC slims to `< 64 000 B`, ref string present |
| `httpx.AsyncClient` construction count / TCP accepts | factory counter + local keep-alive server accept counter (`tests/benchmarks/test_llm_http_pooling_perf.py:49-86, 152+`) | 50 calls → exactly 1 client; N requests → 1 connection |
| `registry.dispatch` invocation count on reuse | counting wrapper (`tests/benchmarks/test_data_runtime_v2_perf.py:59-86`) | 2nd identical geocompute call → `calls["n"] == 1` |
| Ref-descriptor probe count | counting wrapper (`:88-109`) | `probes["n"] <= 8` |
| MVT cache ops: index builds, tile encodes, single-flight producers, retained geometry/bytes estimates | `BenchmarkInstrumenter` monkeypatch counters + `estimate_entry_memory` (`tests/perf/test_mvt_cache_pressure_benchmark.py:82-129, 117-135`) | 50 concurrent same-tile requests → `encode_count == 1` (`:208`); cache entry counts |
| Executor node status reuse / DAG determinism | `engine.get_node_output` + statuses dict (`tests/benchmarks/test_geobench_v3.py:362-375`) | all nodes `"reused"` on 2nd run |
| Query-plan estimated rows/bytes ratios, `len(plan.alternatives) <= MAX_ALTERNATIVES` | `test_planner_cost_monotonic_in_feature_count` (`test_geobench_v3.py:176-214`) | rows ratio 80–125×, bytes ratio 9–11× (page-window cap) |
| Tracemalloc peaks / zero full-array reads | `bench_raster_runtime_v4.py` (gate: windowed path under memory budget, `:1-10`); `test_geobench_v3.py:489` windowed raster memory ceiling | standalone scripts |
| Tool-schema selection size: count / serialized chars / approx tokens / active domains | `measure_selection` in `_master_context_baseline.py:40-57` and `bench_planning_v3.py` (count + bytes of `json.dumps(schemas)`) | informational benches (no gate) |
| Turn-evidence work counters (tool_calls, deduped/wasted calls, sse events, map actions) | `app/lib/runtime/evidence.py:119+` (`TurnEvidence.add_tool_call/inc_sse_event/add_deduped_tool_call`) | asserted in `test_runtime_observability_perf.py:27-60` (unmarked, main lane) |
| Telemetry v2 per dispatch: `requested_execution_policy`, `actual_execution_mode`, `compute_ms`, `arg_bytes`, `result_bytes`, `cache_hit` | emitted in `app/tools/registry.py:1035-1150` and `app/services/tool_metrics.py:195-303` | asserted functionally in v2 harness `:183-197`; **no gate on the counts themselves** |
| Event-loop lag distribution (max/p95/mean) | `EventLoopLagMonitor` (`test_perf_harness_v2.py:41-81`) | `< 25 ms` assertions |
| Cartography QA issue budget / truncation | `report.truncated`, `MAX_TOPOLOGY_ISSUES` (`test_perf_harness.py:423-430`) | functional assertion inside a wall-clock workload |

---

## 5. Bench script inventory (standalone, non-pytest)

| Script | Measures | Deterministic? | Gate? | Lane |
|---|---|---|---|---|
| `bench_geocompute_v3.py` | GeoCompute/DataFabric v3 at scale; prints wall clock as `[INFO timing]` | yes (seeded fixtures); gates are structural (row determinism, estimator ratios, tracemalloc peaks, row-group pruning) | exit-1 on structural invariants only (`:9-21`) | none (manual) |
| `bench_geocompute_v4.py` | same conventions for v4 federation/statistics/executor | yes | structural only ("timings NEVER gate", `:4`) | none |
| `bench_raster_runtime_v4.py` | windowed zonal/exec on 10000×10000 raster; tracemalloc + full-read guard refusal | yes | memory budget + guard (exit 1) | none |
| `bench_pi_bridge_pool_soak.py` | PiBridgePool soak: session affinity ordering, cross-session parallelism, lease-leak/crash recovery; random storm seeded `--seed 42` | seeded random | invariant violations → exit 1 | none |
| `bench_planning_v3.py` | planning-context bytes: per-turn schema selection count/chars/tokens, context block breakdown, plan save/load + validation mean/p95 ms over 200 iters, **vs measured master baseline** (`_master_context_baseline.py` subprocess) | yes (memory backend) | none — writes `.planning/perf-v3.md` | none |
| `bench_gis_perf_539_540.py` | before/after wall-clock repro for #539/#540 (any-revision worktree runs); writes `logs/gis_perf_539_540_{tag}.json` | yes fixtures, wall-clock output | none (deterministic gate lives in `test_perf_harness.py`, per docstring `:13-15`) | none |
| `test_perf_mapspec_e2e.py` (pytest, `perf`) | MapSpec upsert scaling 1k/10k/50k + event-loop lag monitor | medians recorded, floors not thresholds ("Record median ... informational + regression floor", `:155-157`) | loose | nightly only (`production.yml:372-376` comment) |
| `test_spatial_decision_v3_benchmarks.py` (no marker) | 12+ spatial-decision scenario correctness benches | functional | correctness only | main lane |
| `test_geobench_v3.py` (no marker) | GeoBench planner-cost monotonicity, spatial-join subquadratic ratio, DAG scaling/determinism, reuse, fail-fast budget, memory ceiling, cancel bounded | structural ratios + some wall clock | assert-based | main lane |
| `test_spatial_science_benchmarks.py` (`perf`) | scale-guard rejections + numeric reference grids (moran/kriging/IDW) | structural | assert-based | perf-selective |

Note: `bench_*` scripts are excluded from pytest collection by name only if not `test_*`; `bench_*` files are not collected (no `test_` prefix), so their structural gates never run in CI — a duplication gap with `test_geobench_v3.py` which does.

---

## 6. tests/perf inventory (4 files, 34 tests; none of these names is a `perf` keyword — only explicit markers count)

| File | Scenarios | Assertion style |
|---|---|---|
| `test_runtime_v2_perf_contracts.py` (5 tests, all `@pytest.mark.perf`) | PC-1 batch finalize cost independent of layer count; PC-2 guard ring single-field reads; PC-3 manifest O(1) dict lookups; PC-3b capability map == manifest; PC-4 chat admission tombstone single-field | **pure structural** (call counters, revision deltas, isinstance-of-list) |
| `test_project_context_cache.py` (20 tests, unmarked → main lane) | project-context cache: query counts per round, strict drop vs 100-query baseline, mutation invalidation, cross-project isolation, fingerprint drift detection, thread safety | **structural** (SQL counts) + functional |
| `test_context_assembly_baseline.py` (2 tests, unmarked) | BEFORE-state evidence: 7 queries/assemble × 60 rounds problem statement | measurement (`print`), conservative assertion `n > 1` |
| `test_mvt_cache_pressure_benchmark.py` (7 tests, unmarked) | 10 MVT cache/memory pressure scenarios (100k layer, concurrency single-flight, eviction, cancel) | **structural** (encode_count == 1, entry counts, estimated bytes) |

Only `test_runtime_v2_perf_contracts.py` is wired into the PR perf lane (`production.yml:380` includes it explicitly).

---

## 7. Scale / guard gates (`test_backend_scale_decisions.py`)

- Declared **module-marked `unit`** (`pytestmark = pytest.mark.unit`, `:17`) — runs in the main PR lane, docstring: "确定性 count/复杂度契约... 不用脆弱 wall-clock" (`:3-4`).
- Pattern 1 — **scale guards via monkeypatched constants**: shrink module constants (`SAR_EIGEN_MAX_N`, `GWR_FULL_SURFACE_MAX_N`, `_MAX_RIPLEY_OBSERVATIONS`, `_MAX_RIPLEY_PAIRS`, `_EXACT_EDGE_BETWEENNESS_EDGES`, `SPECKLE_SCALE_LIMIT_PIXELS`, `GLCM_OPS_LIMIT`, `PCA_SCALE_LIMIT_CELLS`, `MAX_HYDRO_CELLS`, `:39-134`) then assert typed rejection (`ResourceScaleMismatch`, code `RESOURCE_SCALE_MISMATCH`) or disclosed degradation (GWR `full_coefficient_surfaces is False`). Tests the **rejection boundary precisely** without large data.
- Pattern 2 — **declared-vs-implemented windows**: `select_backend(op, ScaleProfile(feature_count=n))` decision contract — purity (`:141-144`), exact window boundary 2000/2001 for exact-vs-sampled Brandes (`:146-153`), bounded rationale ≤160 chars (`:155-159`), and registry-declared `(min_features, max_features)` must equal implementation constants (`cen._EXACT_BETWEENNESS_NODES`, `:161-170`), closed backend vocabulary (`:182-189`).
- This is the repo's canonical "declare max rows → gate" mechanism: **constants in app modules + registry declarations + boundary tests**. A structural harness should reuse `ScaleProfile`/`ResourceScaleMismatch` rather than invent new vocabulary.

---

## 8. Frontend perf tests

- `frontend/test/map-render-work-count.test.tsx` — "Deterministic Work-Count Benchmark Harness ... Scenarios A-J" (`:21`): counts React renders, store viewport writes, MapLibre `getStyle`/`moveLayer`/`getLayer` calls, reconcile calls, `diffSpecs` calls via hoisted metric objects + mocked MapLibre surface. Structural assertions: 200 pan events → settled store writes `toBe(1)` (`:265`); zoom scenario; layer reconcile counts; bbox vs coordinate-scan (Scenario G `:422`); style invalidation, session-switch isolation, repair idempotency (H-J).
- `frontend/test/page.render-scope.test.tsx` — D-F8: renders real `Home` with leaf components replaced by render counters; asserts siblings re-render **0** times during N token batches while ContextPanel re-renders per batch (`:1-24`, counters `:27-34`).
- `frontend/components/sidebar/chat-tab.render-scope.test.tsx` — counts MiniMd (react-markdown) invocations while rendering the real component: prior M−1 messages must re-parse **0** times across N batches; uses React `<Profiler>` for honest timing (`:1-50`).
- Others touching render counts: `map-panel.test.tsx`, `map-panel.cartography-race.test.tsx`, `m5-chat-and-design-stress.test.tsx`, `chat-tab.scroll.test.tsx`.
- CI: frontend lane runs `pnpm run test:ci` (vitest) + `next build` (`production.yml:562,570`) — so these structural gates **do run on every PR**.

---

## 9. Gaps & machine-sensitive gates

### 9.1 Machine-sensitive strict-ms gates (candidates for structuralization)

| File / test | Ceiling | Problem |
|---|---|---|
| `test_template_registry_perf.py:55` | `median < 0.5 ms` for 1000 lookups; search bound `:75` | microsecond-scale wall clock; no marker → main lane **under `--cov`**; order-of-magnitude sensitive |
| `test_gis_registry_perf.py:24-52` | median of 1000 capability/algorithm/map-model/template lookups | same: unmarked, sub-ms medians |
| `test_planner_runtime_perf.py:128` | `med < 100 ms` plan at full memo; `:143` `build_plan_graph < 20 ms` | unmarked; planning hot path has **no structural gate** (memo-hit tests `:56-107` are structural and good — the wall-clock parts are not) |
| `test_pi_perf.py:80,106` | `prompt() < 100 ms`, first stream event `< 100 ms` with mocked RPC | unmarked; CI-runner scheduling can exceed 100 ms |
| `test_provenance_perf.py:49-52` | `fingerprint median < 5 ms` (perf-marked but strict-ms) | runs in PR perf lane on ubuntu-latest; 5 ms is tight |
| `test_dispatch_stall_perf.py:45-70` | `< 25 ms` medians at 10k/50k/100k + sub-linearity ratio | ratio test `:67-70` is structural-ish; absolute 25 ms is machine-sensitive |
| `test_perf_large_workspace.py:97-99,121-124` | 10 mutations at 100 layers `< 8 s`; semantics `< 12 s`; fingerprint `< 2 s` | includes real disk I/O; CI runner disks vary |
| `test_perf_harness_v2.py:115,141,230` | lag `< 25 ms`; LRU `dur2 <= dur1 + 5 ms` | nightly-only by design (#564) |
| `test_data_runtime_v2_perf.py:59-86` | `reuse_ms < max(first_ms, 5000)` | loose, tolerable, but redundant once `calls["n"] == 1` holds |
| `tests/perf/test_project_context_cache.py` | structural, but unmarked → runs under coverage in main lane | fine (counts are stable), but inconsistent with `runtime_v2_perf_contracts` which is perf-marked |

### 9.2 Hot paths with NO perf/structural gate

| Hot path | Current coverage | Gap |
|---|---|---|
| **Tool dispatch** | well covered (harness `dispatch_overhead`, `ref_resolution_batch`, `_estimate_json_bytes` workloads; PC-3/PC-4) | `argument_normalization.py` and schema-validation cost per dispatch have no count gate |
| **Planning (map-request intent → plan)** | memo-hit structural tests + unmarked ms ceilings (`test_planner_runtime_perf.py`) | no committed baseline / no structural "planner work per turn" (resolver call counts exist ad hoc `:56-98`); `bench_planning_v3.py` context-bytes results are not gated anywhere |
| **MapSpec compile** | `compile_runtime_manifest` O(1) lookups (PC-3); upsert byte gates | no gate on `compile_runtime_manifest()` itself (node count / rebuild count when tools register) |
| **Render (backend tiles)** | `raster_tile_streaming` wall clock + PNG validity; frontend work-count | no structural "bytes decoded per tile" gate (the full-raster-decode regression would only show as wall clock) |
| **Ingest / upload** (`app/tools/upload_tools.py`) | only functional tests | **no perf gate at all** — feature-count → payload bytes, chunking counts, validation cost unmeasured |
| **Artifact cache** | harness `artifact_cache_hit` (hit-path ms) | no gate on eviction count / key-collision behavior under load |
| **Geocompute dispatch** | reuse-count + probe-bound (`test_data_runtime_v2_perf.py`), GeoBench structural (`test_geobench_v3.py`, unmarked) | the structural half (`test_data_runtime_v2_perf.py`) is unmarked → runs under coverage in main lane where its loose ms guards are noisy |
| **SSE/streaming** | transport baselines (4 workloads) | `TurnEvidence` wasted-work counters (`deduped_tool_calls`) asserted only in an unmarked observability test, never gated |
| **Chat context assembly** | query-count contracts (tests/perf) + bytes benches | `estimated_tokens` / `tools_payload_chars` growth has **no committed ceiling gate** — only before/after measurement in `bench_planning_v3.py`/`_master_context_baseline.py` |

---

## 10. Recommendations

1. **Formalize a structural tier inside the existing marker policy.** Add a doc-stable convention: structural-gate tests use `@pytest.mark.perf` (isolated lane, `--no-cov`) — matching what `test_runtime_v2_perf_contracts.py` already does — and never read `time.perf_counter`. This is #664-safe by construction: `tests/conftest.py:143-154` keys off the marker, not the directory; structural tests are deterministic so running them isolated costs nothing and they would also pass unisolated.
2. **Extend the baseline JSON format with metric kinds instead of new files.** Generalize `baselines.json` entries to `{"kind": "wall_ms" | "count" | "bytes", "value": ..., "iterations": ...}` — keep `median_ms` reading back-compat. Structural metrics are exact integers, so the policy can be `count <= baseline` (no factors, no floors, no medians). Reuse `decide_baseline_action` unchanged (`_baseline_policy.py:25-53`) — the fail-closed missing-baseline behavior and `PERF_UPDATE_BASELINES=1` flow carry over verbatim.
3. **File layout that fits conventions**: `tests/benchmarks/test_structural_harness.py` (workload registry dict like `WORKLOADS` in `test_perf_harness.py:575`) + counters file `tests/benchmarks/_counters.py` (shared `counting_wrapper` / SQLAlchemy `_count_queries` / `spy_writes` helpers — today duplicated 3×: `test_context_assembly_baseline.py:91`, `test_project_context_cache.py:136`, `test_provenance_perf.py:131`). Name new per-domain files `test_<domain>_structural_perf.py`.
4. **Promote the CI perf lane list to marker-driven over time**: today `production.yml:380` enumerates 9 files; new structural files must be appended there explicitly (or switch the lane to bare `-m perf` once nightly/main coverage of unmarked perf-style files is cleaned up). Add to the PR lane only fast structural files; keep lag monitors (`v2`, `test_perf_mapspec_e2e.py`) nightly-only per #564 rationale (`production.yml:372-376`).
5. **Structuralize the worst machine-sensitive gates first** (§9.1): `test_template_registry_perf.py` and `test_gis_registry_perf.py` → assert `dict`-type O(1) (PC-3 style, `isinstance(caps, list)` after a type check / no registry scan) instead of sub-ms medians; `test_planner_runtime_perf.py` wall-clock parts → resolver/build call counts (the memo tests already model this); `test_pi_perf.py` → count event-loop yields or queue drains instead of 100 ms ceilings; keep `test_provenance_perf.py` 5 ms but add a `floor_ms` like `raster_tile_streaming` did (`baselines.json:52-56`).
6. **Close the ingest gap**: add a structural workload asserting upload/ingest emits bytes-bounded payloads and O(1)-per-batch writes (reuse the `_atomic_write_json_sync` byte-spy pattern from `test_perf_mapspec_mutation_cost.py:83`).
7. **Gate context-size structurally**: turn `bench_planning_v3.py`'s measured selection `count/chars/tokens` into a committed structural baseline (schema-count ceiling + bytes ceiling per turn) so prompt-bloat regressions fail the lane instead of landing in a markdown report.
8. **Do not** add new bare wall-clock files to the main lane; the existing unmarked offenders (`test_geobench_v3.py`, `test_data_runtime_v2_perf.py`) should either gain `@pytest.mark.perf` (moving their cheap structural asserts into the PR perf lane) or drop their ms assertions — currently they run under `--cov` where even loose ceilings are load-sensitive (the exact #564 failure mode).

---

## Appendix A — Key files

- Harness: `tests/benchmarks/test_perf_harness.py` (658 lines, 15 workloads), `test_perf_harness_v2.py` (234), `test_transport_perf.py` + `transport_baselines.json`
- Policy: `tests/benchmarks/_baseline_policy.py`, `test_baseline_policy.py`, `baselines.json`
- Isolation: `tests/conftest.py:128-154`; wiring lock `tests/unit/test_perf_isolation_wiring.py`
- Scale contracts: `tests/benchmarks/test_backend_scale_decisions.py` (unit-marked)
- Structural exemplars: `tests/perf/test_runtime_v2_perf_contracts.py`, `tests/benchmarks/test_perf_mapspec_mutation_cost.py`, `test_llm_http_pooling_perf.py`, `tests/perf/test_mvt_cache_pressure_benchmark.py`, `test_geobench_v3.py`
- CI: `.github/workflows/production.yml` — main `:192`, perf PR lane `:340-380` (run cmd `:380`), nightly `:515-531`, frontend `:546-570`
- App-side emitters: `app/tools/registry.py:1035-1150` (telemetry v2, `_estimate_json_bytes`), `app/services/tool_metrics.py:195-303` (arg/result bytes, cache_hit), `app/lib/runtime/evidence.py:119` (TurnEvidence work counters), `app/lib/gis/backend_selection.py` (`ScaleProfile`), `app/services/session_data.py:339` (`get_state_field`)
- ADR: `docs/adr/0046-performance-regression-harness.md` (original 4-workload rationale; factors since made strict-fail in warn band per TEST-01)
