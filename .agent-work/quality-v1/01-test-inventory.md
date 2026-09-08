# Test Inventory — webgis-ai-agent-quality-v1

Audit date: 2026-09-08. Method: `pytest --collect-only -q` full collection (10,329 tests in ~37s), per-marker collects, file/grep analysis. All paths relative to repo root `/home/kevin/projects/webgis/webgis-ai-agent-quality-v1`.

## Executive summary

- **Backend**: 793 test files / **10,329 collected tests** across 9 test dirs. Grouped by 4 pytest markers (`pytest.ini:22-27`): `cartography` (682), `perf` (93), `heavy` (65), `real_services` (6); everything else runs in the default coverage lane (`-m "not perf and not cartography and not real_services"`, `--cov-fail-under=75`).
- **Lane shape**: default lane ≈ 9,483 tests; the cartography marker is itself a release-blocking deterministic gate (`-m cartography`, no Node/LLM/network); perf is self-skipping unless explicitly selected (conftest hook, `tests/conftest.py:128-154`).
- **Frontend**: only **15 vitest files** (~4,400 LOC) in two roots (`frontend/test/`, `frontend/tests/`), gated by vitest coverage thresholds (75 lines / 70 functions / 60 branches) + dual `tsc --noEmit` + `next build`.
- **Generated assets are test assets**: 3 generators produce registry-derived artifacts — science algorithm catalog (docs), numerical oracle corpus (1,084 parametrized replay tests), workflow catalog (docs) — each with a drift-detection test that byte-compares generator output against the committed file.
- **Deep conformance/golden culture**: a 509-file structured golden corpus for cartography, a ≥20,088-case generated semantic conformance corpus (offline, default lane), golden e2e product cases (G1/G2), and CI gate-drift contract tests that pin `ci-local.sh` to the workflow verbatim.
- **Broken today**: full `pytest` collection **errors** — `tests/unit/test_descriptor_coverage_gate.py:9` imports `check_tool_descriptor_coverage` (per its docstring, `scripts/check_tool_descriptor_coverage.py`), but that script does not exist in this snapshot → `ModuleNotFoundError`, collection aborts (exit 2). All subdir/targeted lanes are unaffected.

## Backend test map

Counts from `pytest --collect-only -q` (full tree). Markers as defined in `pytest.ini:22-27`.

| Area | Dirs | Files | Approx tests | Markers / lane |
|---|---|---|---|---|
| Root service/API/security tests | `tests/test_*.py` | 223 | 2,018 | default lane; a few `heavy` (spatial tools), `real_services` (2 files) |
| Unit: top-level (cross-cutting) | `tests/unit/test_*.py` | 370 | 4,063 | default lane; some `heavy` |
| Unit: data_fabric, geocompute, chat-context, cartography libs | (inside `tests/unit/` file names) | — | (in 4,063) | default |
| Unit: GIS science algorithms | `tests/unit/lib/` | 58 | 651 | default; kriging/variogram/SAR/terrain files partly `heavy` |
| Unit: GIS harness / workflow engine | `tests/unit/gis_harness/` | 43 | 727 | default; includes conformance corpus + golden product cases |
| Unit: GIS core registries/contracts | `tests/unit/gis/` | 10 | 205 | default |
| Cartography gate suite | `tests/cartography/` | 32 | 356 | `cartography` |
| Cartography golden corpus | `tests/cartography/golden_corpus/` | 1 (+509 goldens) | 512 | `cartography` |
| Science oracle replay | `tests/science_oracles/` | 1 | 1,084 | default (offline, fast) |
| Data subsystem (artifact/catalog/lineage) | `tests/data/` | 19 | 230 | default |
| Jobs / durable celery | `tests/jobs/` | 9 | 243 | default (celery eager mode) |
| Perf harness + benchmarks | `tests/benchmarks/` | 21 | 175 | `perf` (93 perf-marked suite-wide; rest are marker-free perf-support tests) |
| Perf contracts (functional) | `tests/perf/` | 4 | 34 | default (not perf-marked — run in coverage lane) |
| Integration | `tests/integration/` | 3 | 31 | default (SQLite, real routes) |
| **Total** | | **793** | **10,329** (+1 collect error) | |

### What each region covers

- **Root `tests/test_*.py`** (2,018): chat engine/SSE streaming (`chat_*`, `sse_*`, `adversarial_sse_chaos`), auth & security hardening (`auth*`, `path_traversal_security`, `svg_sanitize`, `ws_auth`, `sec08_*`), uploads/session lifecycle (`upload_*`, `session_*`), tool dispatch/cache/metrics (`tool_*`, `slim_tool_result_*`), PI bridge & runtime chaos (`pi_*`, `runtime_chaos_*`), cartography service/tools (`cartography_service/tools/turn_injection`), deploy/k8s contracts (`k8s_*`, `docker_security`, `env_template_parity`), CI contracts (`ci_*`), real services (`real_services_*`, gated `REAL_SERVICES=1`).
- **`tests/unit/`**: largest block. Themes include ~30 `data_fabric_*` files, ~12 `geocompute_*`, chat context budget/assembler, cartography templates/planner, adapter/provider contracts, audit-round regression files (`audit*`, `backend_audit836_839`, `gis_audit*`), env hygiene (`test_env_hygiene.py` locks conftest pinning), perf-isolation wiring (`test_perf_isolation_wiring.py`).
- **`tests/unit/lib/`** (651): the numerical GIS core — kriging (35 tests), remote sensing (`rs_v3` 29), variogram (22), terrain science, IDW/TIN interpolation, spatial stats/regression, density, geometry ops, plus `test_spatial_stats_conformance.py`.
- **`tests/unit/gis_harness/`** (727): planner, intent, workflow compiler/families/guards, template catalog, conformance corpus, golden orchestration cases, semantic QA, runtime repair/trace, product graph/verdict/lineage.
- **`tests/jobs/`** (243): durable job store/worker/cancellation/migration; `test_job_celery_e2e.py` is honest worker-simulation (celery eager in-process; real broker covered only by real-services lane).
- **`tests/integration/`** (31): `test_cross_tenant_isolation.py` (real SQLite + real routes, cross-tenant access must 404), session API, skill API.

### Marker counts (collected)

| Marker | Tests | Lane |
|---|---|---|
| (default, no marker exclusion) | ~9,483 | PR `test-backend`: `pytest --cov=app --cov-fail-under=75 -m "not perf and not cartography and not real_services"` |
| `cartography` | 682 | PR `cartography-smoke`: `pytest -m cartography --no-cov --timeout=120` |
| `perf` | 93 | PR `test-perf`: 8 named files only; rest nightly (`nightly-matrix -m "cartography or perf"`) |
| `heavy` | 65 | inside default/heavy-capable lanes; browser-needing ones self-skip unless `REQUIRE_BROWSER=1` (nightly `runtime-validator`) |
| `real_services` | 6 | CI `real-services-smoke` with service containers, `REAL_SERVICES=1` |

### pytest.ini essentials (`pytest.ini`)

- `testpaths = tests`, `asyncio_mode = auto`, `timeout = 60` (thread), `addopts = --ignore=tests/smoke-test-buffer.py --ignore=tests/smoke_deep_enhancement.py --cov=app --cov-report=term-missing` (lines 1-17).
- Marker docstrings in `pytest.ini:22-27` are the authoritative lane contract; perf baselines assume isolated execution (#664); `real_services` never arms from leaked env (#661).

## Frontend test map

Framework: Vitest + Testing Library, jsdom, globals. Config `frontend/vitest.config.ts` (`include: ['**/*.{test,spec}.{ts,tsx}']`, so both test roots run; coverage thresholds lines 75 / functions 70 / statements 75 / branches 60 — a deliberate ratchet, comment #564).

| File | Area covered |
|---|---|
| `frontend/test/challenge/m5-challenger-hud-grid-popover.stress.test.tsx` (951 LOC) | HUD, grid, popover stress rounds |
| `frontend/test/challenge/m5-chat-and-design-stress.test.tsx` (414) | chat + design-system stress |
| `frontend/test/design-system/contrast.test.ts` (237) | WCAG contrast of tokens |
| `frontend/test/design-system/tokens.contract.test.ts` (235) | design-token contract |
| `frontend/test/design-system/visual-system.contract.test.tsx` (597) | visual system contract |
| `frontend/test/results/{normalize,results-ui,resultsSlice,use-result-descriptor}.test.ts(x)` (~780) | results slice of `useHudStore`, result descriptors, normalization |
| `frontend/test/map-render-work-count.test.tsx` (522) | map render workload counting (perf guard) |
| `frontend/test/maplibre-mock-surface.test.ts` (130) | maplibre mock surface |
| `frontend/test/page.render-scope.test.tsx` (237) | page render scope (SSR/hydration hygiene) |
| `frontend/test/ui-round-883-891.test.tsx` (112) | UI regression round 883-891 |
| `frontend/tests/components/explorer/tabular-data-grid.test.tsx` (90) | `components/explorer/tabular-data-grid` |
| `frontend/tests/components/explorer/preview-modal.test.tsx` (71) | explorer preview modal |

Coverage areas vs source: `frontend/components/` (agent, chat, drawers, explorer, hud, map, panel, sidebar, upload, ui), `frontend/lib/` (store slices incl. `resultsSlice`, cartography, map-exporter, agent-runtime — the latter two have stray colocated tests `components/theme-tokens.test.ts`, `components/tweaks-panel.fake-controls.test.tsx`, `lib/agent-runtime.test.ts`, `lib/basemap-apply.test.ts` — note: colocated tests exist beyond the 15 counted above; vitest picks them up too).

npm scripts (`frontend/package.json`): `test` = `vitest run`; `test:watch`; `test:coverage` = `vitest run --coverage`; `test:ci` = `vitest run --coverage --reporter=default --reporter=junit --outputFile=test-results.junit.xml`; `typecheck` = `tsc --noEmit && tsc -p tsconfig.test.json --noEmit`; `lint` = `eslint . --max-warnings 0`; `build` = `next build`. CI `test-frontend` job runs `pnpm run test:ci` + `typecheck` + `build` and uploads codecov (`.github/workflows/production.yml:533-585`).

## Generated catalogs & oracles

| Generator | Output | Consumer / drift gate |
|---|---|---|
| `scripts/gen_science_catalog.py` | `docs/science/ALGORITHM_CATALOG.md` — deterministic projection of CapabilityRegistry + AlgorithmRegistry + parameter contracts (capability-major, (priority,id) sort). Byte-stable by design (docstring line 11). | `tests/unit/gis/test_foundation_v2_infra.py:178-185` imports `generate()` and byte-compares to the committed md → fails with "run `python scripts/gen_science_catalog.py`" |
| `scripts/gen_science_oracles.py` (4,189 lines) | `tests/science_oracles/data/<domain>.json` — 12 domains: point_pattern, spectral, crs_units, statistics_global, statistics_local, geodetector, regression, terrain, network, geostat, sar, edge_cases. Each case = `{id, target:"module:function", args, kwargs, expect}` with values from independent numpy/scipy references or `anchor=True` implementation-regression anchors. | `tests/science_oracles/test_oracle_replay.py` replays all cases via frozen runner `tests/science_oracles/__init__.py` (select mini-language `"L.3"`, `"mean:array"`; kinds exact/allclose/error) → **1,084 parametrized tests**, zero recomputation. Empty corpus → hard RuntimeError. |
| `scripts/gen_workflow_catalog.py` | `docs/workflows/workflow-catalog.md` (+ `--check` mode exit 1 on staleness) — from RecipeRegistry (V1 seeds + domain packs) + workflow family/composite/scenario layers, with content fingerprints. | `tests/unit/gis_harness/test_workflow_guards.py:363-365` imports `render_catalog()` and compares to committed file |
| (bonus) `python -m app.lib.cartography.catalog_docs --check` | `docs/cartography/{map-model,component,composition-template,theme-palette,renderer-parity}-catalog.md` | `tests/cartography/test_catalog_docs.py` (`cartography` marker): freshness + non-empty + design docs present |

Not run in CI workflows directly — the drift tests above are the enforcement mechanism (they run in default/cartography lanes).

## Conformance/golden suites

1. **Cartography closed-loop gate** — `tests/cartography/test_cartography_closed_loop.py` (docstring lines 1-8): real closed loop GIS ref → MapSpec mutation → semantic validation → transaction semantics (invalid mutation rejected, last-known-good preserved) → ref resolution → cartographic semantic checks → fault injection. Deterministic, no Node/Chromium/LLM/network. Part of the 682 `cartography`-marked tests run by the CI `cartography-smoke` job.
2. **Golden Corpus V3** — `tests/cartography/golden_corpus/` (`corpus.py`, `test_golden_corpus.py`, 509 committed JSON goldens, 512 tests, ADR-0101 D8). Asserts per-case **structured digest** (model selection, component instances, composition validation, layout solve — explicitly not pixel), double-run determinism, ≥500-case matrix, planned-model thematic-binding leakage gate. Refresh only with `GOLDEN_CORPUS_UPDATE=1`, and the test **fails after writing** to force human diff review (header lines 3-9).
3. **Science oracle replay** — `tests/science_oracles/test_oracle_replay.py`: 1,084 numerical/error-expectation replays (see above).
4. **Harness semantic conformance corpus** — `tests/unit/gis_harness/test_conformance_corpus.py`: ≥20,000 generated cases (59 semantic families × language × 12 scopes × 9 phrasings), semantic-identity invariants (same family ⇒ same task/recipe), anti-claim contracts, workflow contract cases, all 147 V2 recipes compile, 7 end-to-end scenarios. Runs in the **default lane** (~20s, offline, no LLM).
5. **Golden product cases** — `tests/unit/gis_harness/test_golden_cases_v2.py` (G1 Chengdu schools POI→MapProduct→chart attach; G2 component-only mutation must leave layer array/fingerprint byte-identical) and `test_golden_cases_orchestration.py`.
6. **CI gate-drift contracts** — `tests/test_ci_local_gate_contract.py` (ci-local.sh must contain each production.yml gate command verbatim), `tests/test_ci_perf_coverage_contract.py` (PR perf file list locked; nightly-only perf files enumerated; coverage gates must be non-decorative), plus `test_ci_rollback_preview_contract.py`, `test_ci_playwright_lane.py`, `test_ci_prometheus_transport.py`.
7. **Registry/tool contracts** — `tests/test_tool_meta_contract.py` (live `ToolRegistry`: every advertised domain returns ≥1 tool; every Pi-extension example tool name resolves — name-integrity gate), `tests/unit/test_tool_catalog*.py`, `test_capability_registry_parity.py`, `test_adapter_contract_v2.py`, `test_provider_contract_v2.py`, `test_session_store_contract.py`, `tests/data/test_artifact_contract.py` (ArtifactContract), `tests/perf/test_runtime_v2_perf_contracts.py`. 45 test files exercise the live registry via `init_tools()`.
8. **Scientific contracts** — `tests/unit/gis/test_scientific_contracts_vnext.py`, `test_golden_gis_numerics.py`, `tests/unit/lib/test_spatial_stats_conformance.py`, `test_nearest_contract.py`, `tests/unit/gis/test_data_contracts_v2.py`.
9. **Infra/deploy contracts** — `test_k8s_images_contract.py`, `test_k8s_secret_contract.py`, `test_k8s_security.py`, `test_env_template_parity.py`, `test_alembic_metadata.py`.

## Reusable fixtures/infra

**`tests/conftest.py`** (only conftest in tree):
- `_ENV_BASELINE` (lines 18-104): setdefault-pins ~100 env keys (SQLite DATABASE_URL, `USE_REDIS=false` → in-memory session store, `CELERY_BROKER_URL=memory://` + `cache+memory://` eager celery, dummy `LLM_API_KEY`, all map-provider tokens empty) — "dirty machine == clean machine"; integrity locked by `tests/unit/test_env_hygiene.py`.
- `_pin_auth_bypass_off` (autouse, 109-125): forces `AUTH_DISABLED=False`, `LOCAL_QUERY_FIRST=False`.
- `pytest_collection_modifyitems` (128-154): perf tests get a visible skip unless `-m perf` selects them (#664).
- `_offline_embedding_model` (autouse, 157-194): makes real SentenceTransformer load fail fast (unbounded network guard).

**Shared fixture modules** (`tests/fixtures/`):
- `pi_mocks.py`: `make_mock_process` (mocked Pi subprocess stdin/stdout with latency simulation), Pi event factories mirroring the vendor AgentSessionEvent protocol (`make_token_event`, `make_tool_call_event`, ...).
- `mapspec_cow_fixtures.py`: cached deterministic 100k-point / 50k-line / 10k-polygon FeatureCollections shared by CoW regression + perf benchmarks (lazy build to keep skip behavior).
- `data_fabric/fake_server.py`: `FakeFabricAdapter` — a `requests` HTTPAdapter subclassing the real `SSRFSafeHTTPAdapter`, canned routes by path prefix, redirect/fault injection (302→169.254.169.254 exercises real revalidation).
- `runtime/<scenario>/{mapspec.json,probes.json}`: 9 scenarios (heatmap-basic, mvt-basic, raster-overlay, step-fill, symbol-label, match-line, interpolate-circle, fault-missing-source, fault-wrong-color). Probe DSL {layer-exists, feature-count, pixel-color}; structure locked by `tests/unit/test_runtime_fixture_contract.py`; consumed by runtime validator tests (browser lane).
- `compiler_parity_mapspec.json`: compiler-parity golden input.

**Other infra**:
- `tests/benchmarks/_ctx_fixture.py`: synthetic planning session shared between worktree bench and pristine-master baseline (byte-comparable context assembly).
- `tests/benchmarks/baselines.json` (15 workloads, median-of-7 ms) + `transport_baselines.json`; policy in `_baseline_policy.py` (fail-closed on missing baseline unless `ALLOW_MISSING_PERF_BASELINE=1`; refresh via `PERF_UPDATE_BASELINES=1`).
- `tests/real_services_celery_app.py`: celery app for the real-services smoke lane.
- Frontend: `frontend/test/setup.ts` (localStorage mock, `Blob.arrayBuffer` polyfill for jsPDF byte assertions, canvas 2d stub), `frontend/test/__mocks__/` (framer-motion ts+js, maplibre-map), `frontend/test/test-utils.tsx` (`createMockLayer`, `createMockStoreState` for `useHudStore`), `frontend/test/visual/capture.mjs` (visual capture script).
- **No central fake LLM provider**: chat/planner tests monkeypatch engine methods inline (e.g. `tests/test_chat_engine_planning.py:15-23` fakes `_get_or_create_session`/`make_plan`); the offline guarantee comes from env pinning, not a shared stub. No hypothesis/property-based testing anywhere.

## CI-local lanes

`scripts/ci-local.sh` (only local CI entrypoint; **no Makefile or justfile anywhere** — checked root and frontend):
1. `ruff check` (repo-wide: app/ tests/ main.py manage.py)
2. `eslint . --max-warnings 0` (frontend)
3. `pnpm run typecheck`
4. `pnpm run test:ci` (vitest + coverage)
5. **Contract tier** (`--fast` also runs this): `pytest tests/test_tool_meta_contract.py tests/test_subagent_context_isolation_436.py tests/test_ci_local_gate_contract.py tests/test_ci_perf_coverage_contract.py --no-cov -q` (lines 52-58)
6. `pnpm run build` (next build)
7. Backend lane: `pytest --cov-fail-under=75 -m "not perf and not cartography and not real_services" -q`
8. Perf lane: 8 named benchmark files, `-m perf --no-cov --timeout=180`
9. Cartography smoke: `pytest -m cartography --no-cov --timeout=120`

`--fast` = steps 1-5 only. Deliberately NOT reproduced locally (need containers): db-migrations, real-services-smoke, deploy-config, runtime-validator (header lines 14-16). Fidelity is enforced by `tests/test_ci_local_gate_contract.py`.

Companion tooling: `scripts/flip-red` — mutation-verification wrapper (refuses dirty tree → `git checkout <base> -- app/ frontend/` → pytest → restore; #700 process hardening). `.github/workflows/production.yml` jobs: lint, test-backend, db-migrations, real-services-smoke, test-perf, cartography-smoke, runtime-validator, nightly-matrix, test-frontend, deploy-config, security, dependency-audit, release-gate, build, preview, deploy-prod, rollback.

## Notable observations & gaps

1. **Collection is broken in this snapshot**: `tests/unit/test_descriptor_coverage_gate.py:9` imports `check_tool_descriptor_coverage` (docstring says it gates `scripts/check_tool_descriptor_coverage.py`, ADR-0103) — the script is absent → `ModuleNotFoundError`, and full-suite `pytest` aborts collection (exit 2). Any platform work needs this module restored or the test ignored.
2. **Marker hygiene is unusually good**: markers are documented as contracts in `pytest.ini`, perf self-skips outside isolated runs, real_services needs explicit `REAL_SERVICES=1` (immune to env leaks), and both behaviors have meta-tests (`test_perf_isolation_wiring.py`, `test_real_services_ci_wiring.py`).
3. **Backend-heavy / frontend-light**: 10.3k backend vs 15 (≈18 incl. colocated) frontend files; frontend e2e only exists as the nightly Playwright `runtime-validator` lane (`REQUIRE_BROWSER=1`); `frontend/test/visual/capture.mjs` suggests manual visual capture, not automated visual diff.
4. **Two frontend test roots** (`test/` + `tests/`) plus colocated `*.test.ts` inside `components/` and `lib/` — works via the vitest glob but is inconsistent.
5. **Golden refresh is intentionally adversarial**: `GOLDEN_CORPUS_UPDATE=1` writes then fails, forcing human review — good anti-snapshot-drift discipline; oracle corpus has no equivalent refresh gate in CI (replay only; generator must be run manually).
6. **`tests/data` is a test suite for `app/lib/data`**, not fixture data; reusable fixture data lives in `tests/fixtures/`. Naming can mislead.
7. **Two perf locations**: `tests/benchmarks/` (perf-marked, baselines, CI perf lane uses only 8 of 21 files; `bench_*.py` are standalone scripts, not pytest) vs `tests/perf/` (functional perf contracts, default lane).
8. **Conftest is global and order-sensitive by design**: env pinning happens before any app import; lazy settings imports are mandated in fixtures (see `_pin_auth_bypass_off` docstring).
9. Heavy-dependency tests (numpy/rasterio/geopandas) are woven through default-lane files with `heavy` markers rather than isolated in a directory — 65 marked tests across ≥10 scattered files.
10. No Makefile/justfile/nox/tox: `scripts/ci-local.sh` + `pytest.ini` + `pyproject.toml` are the entire task runner surface.

## Key facts for platform builders

- 10,329 backend tests / 793 files; full collect ≈ 37s; **1 collection error today** (`test_descriptor_coverage_gate.py` → missing `check_tool_descriptor_coverage` module).
- 4 markers: `cartography` 682, `perf` 93, `heavy` 65, `real_services` 6; default lane = everything else with `--cov-fail-under=75`.
- PR CI lanes: backend (cov 75%), perf (8 named files), cartography-smoke (`-m cartography`), frontend (vitest cov + typecheck + next build); nightly: runtime-validator (browser) and `-m "cartography or perf"` matrix.
- `pytest -m cartography` is a deterministic, dependency-light release gate — the fastest high-value lane to replicate.
- Science oracles = 1,084 parametrized JSON-replay tests across 12 domains; regenerate with `scripts/gen_science_oracles.py`, drift-enforced only by review (replay never recomputes).
- Golden cartography corpus: 509 structured digests, refresh requires `GOLDEN_CORPUS_UPDATE=1` and intentionally fails after write.
- Generated docs (science catalog, workflow catalog, cartography catalogs) all have byte-diff drift tests in normal lanes.
- Harness semantic conformance corpus (≥20k generated cases, offline, ~20s) runs in the default lane — a cheap regression net for product semantics.
- `tests/conftest.py` pins ~100 env keys (SQLite, in-memory session store, memory:// celery, dummy LLM key) — new env keys must be added to `_ENV_BASELINE` or `test_env_hygiene.py` fails.
- No shared fake LLM provider: tests monkeypatch engine methods; keep that pattern or build one centrally.
- Reusable fakes: `tests/fixtures/pi_mocks.py` (Pi subprocess), `tests/fixtures/data_fabric/fake_server.py` (SSRF-validating fault-injection HTTP adapter), `mapspec_cow_fixtures.py` (100k-feature payloads), `tests/fixtures/runtime/` (mapspec+probe scenarios).
- Perf gate: median-of-7 vs `tests/benchmarks/baselines.json`; fail-closed on missing baseline; skip unless `-m perf`; refresh with `PERF_UPDATE_BASELINES=1`.
- Frontend: vitest jsdom, coverage ratchet 75/70/75/60, dual tsc typecheck, maplibre/framer-motion mocks in `test/__mocks__`.
- `scripts/ci-local.sh` mirrors production.yml verbatim (contract-locked); `--fast` ≈ lint+typecheck+vitest+contract tier; no Makefile/justfile exists.
- Real Postgres/Redis/celery coverage exists only via `REAL_SERVICES=1` lane (6 tests); celery in default tests is eager/in-process by design.
