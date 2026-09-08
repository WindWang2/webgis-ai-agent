# 02 — Coverage Risk Map (capability-risk based, NOT line coverage)

Repo audited (read-only): `/home/kevin/projects/webgis/webgis-ai-agent-quality-v1`
Date: 2026-09-08 · Method: static enumeration of frozen central contracts + rg cross-reference against `tests/`. No tests were executed.

---

## 1. Executive summary

This suite is unusually well-hardened for its size (~793 test files, ~10.3k tests; 1,084 frozen numerical oracle cases; 512-file cartography golden corpus; structural conformance tests that fail when a registered tool/algorithm/capability/recipe is orphaned). The **science core** (kriging, spatial stats, terrain, network, SAR, point-pattern, regression) is protected by exact-value oracles plus estimate-before-allocate resource guards, and **authz is enforced by an AST-walking meta-test** (`tests/test_api_auth_required.py:318`) that fails CI if any mutating route is not auth-enforcing or explicitly allowlisted with a reason.

The residual risk is therefore concentrated **not in the science or the routes, but in the tool-wrapper and dispatch seam** — the layer the LLM actually drives:

1. **29 of 267 registered tools have zero direct test references**, including state-mutating tools (`apply_layer_style`, `webgis_checkpoint`/`webgis_rollback`, `webgis_validate`/`webgis_compile_maplibre`), the geocompute execution tools (`execute_execution_plan`, `get_execution_run`), and the entire data-lineage/discovery tool family (`get_lineage`, `describe_artifact`, `find_artifacts_by_role`, `search_datasets`). Underlying services are tested; the wrappers (arg models, session handling, error shapes the LLM consumes) are not.
2. **Capability vocabulary is broader than the tested surface**: 47 of 112 capability ids are never named in any test. Most map to oracle-covered implementations, but a capability with a broken `tool_candidates` binding resolves to a tool nobody tests directly (parity tests only check *existence*, not behavior).
3. **Data-fabric SQL compiler** (`app/services/data_fabric/query/compilers.py`, 486 L: `compile_predicate_sql`/`compile_temporal_sql`/`compile_spatial_sql`) has **zero direct tests**; only the legacy `_parse_safe_where` path is tested. It is parameterized-by-construction (injection risk low) but NULL/LIKE/temporal semantics can silently drift.
4. **Cooperative cancellation is inconsistently wired in the science layer**: `cancellable()` checkpoints exist in kriging/statistics/spatial-regression, but `point_pattern.py` permutation/envelope loops (7 sites, up to 499 perms × O(n²) at 20k points) and the SAR/raster-spectral families never check — a cancelled job keeps burning CPU for minutes with no test asserting cancellation latency.
5. Frontend is dense (237 test files) but the **results normalization mirror** (`frontend/lib/results/normalize.ts` 334 L + `families.ts` 243 L, 0 tests) can silently diverge from backend result contracts.

No CRITICAL "silent wrong result" gap was found in the numerical core itself; the top risks are silent degradation and unreproducibility at the LLM→tool seam.

---

## 2. Method

1. **ToolRegistry**: extracted every `@tool(...)` / `registry.tool(...)` registration site across `app/tools/` and `app/services/gis_harness/` (two regex passes to catch `name=` in non-first position) → 267 distinct tool names. For each, `rg -w <name> tests/` (exact word match).
2. **AlgorithmRegistry / CapabilityRegistry**: extracted `id="..."` from `app/lib/gis/algorithms/*.py` (107) and `app/lib/gis/capabilities/*.py` (112); same word-match against `tests/`; cross-checked against the frozen oracle corpus (`tests/science_oracles/data/*.json`, targets extracted and counted per module/function).
3. **Routes**: enumerated all `@router.(get|post|...)` in `app/api/routes/` (139 endpoints / 26 files); mapped auth dependency usage (`Depends(get_current_user|require_owned_session|verify_bridge_secret|require_admin)`); reviewed the allowlist meta-test and per-file test imports.
4. **Map/exporter**: enumerated `app/services/mapspec*`, `mapspec_to_svg.py`, `mvt.py`, `analysis_cartography_converter.py`, `raster_cartography_converter.py` + frontend `lib/mapspec-compiler`; counted dedicated test files and golden corpus entries.
5. **Artifacts**: enumerated `SEED_ARTIFACT_TYPES` (21 types, `app/lib/gis/artifacts.py`), `ArtifactContract` bridges (`app/lib/data/artifact_contract.py`), `ArtifactRecord.to_dict/from_dict` (`app/services/artifact_registry.py:108/117`); reviewed round-trip tests.
6. **Harness**: per-module test-reference scan of `app/lib/harness` and `app/services/gis_harness` (79 modules) including subpackages `recipe_packs/` (24 domain packs) and `completion/validators/` (7 validators); verified apparent gaps against indirect function-name coverage.
7. **Heavy algorithms**: grepped `app/lib/geo_analysis/` for `cancellable()`, `raster_guard`/`RasterResourceGuard`, `_MAX_*` estimate-before-allocate constants; cross-checked guard tests and `tests/jobs/test_job_cancellation.py`.
8. **Frontend**: counted 253 `*.test.ts(x)` files; grouped by directory; diffed against source modules for `lib/` and `components/`.

"Tested" here means *direct name-level reference somewhere in `tests/`* — weaker than a dedicated behavioral test; numbers are therefore an **upper bound** on coverage and gaps are a **lower bound**.

---

## 3. Registry coverage table

| Registry / surface | #Registered | #Tested (name-ref) | Gaps (zero or wrapper-only coverage) |
|---|---|---|---|
| ToolRegistry (`app/tools/registry.py` + 46 `register_*` modules) | 267 | 238 | **29 zero-ref tools** — see §3.1 |
| AlgorithmRegistry (`app/lib/gis/algorithms/`, 13 domain packs) | 107 | 45 by algo-id | Most algo ids referenced only via tool tests; science functions oracle-backed (see below). Wrapper-level gaps match tool gaps |
| CapabilityRegistry (`app/lib/gis/capabilities/`, 11 domain packs) | 112 | 65 by cap-id | 47 unnamed in tests (incl. `geometry_*`, `terrain_*` families, `sar_analysis`, `od_flow_mapping`, `external_route_planning`, `traffic_status`, `transit_routing`). Parity test `tests/unit/test_capability_registry_parity.py:29-117` guarantees resolution, not behavior |
| Numerical oracle corpus (`tests/science_oracles/data/*.json`) | **1,084 cases** / 12 domains / 26 target modules | — | Covers `geo_analysis.{kriging 142, terrain 216, statistics 118, network? 177*, spatial_regression 82, geostat 253, sar 101, point_pattern 30, geodetector 43, spectral 10, crs_safety 62, trend_surface 44}` (*network total incl. `app/services/network/*` 162). No oracle gap found for oracle-backed families |
| SessionPlan (`app/services/session_plan.py`) | — | 232 test files reference | No gap found |
| ExecutionPlan (`app/services/geocompute/plan.py`) | — | 120 test files reference | Route-level (HTTP) tests thin; service-level strong (`tests/unit/test_geocompute_execution.py`, `test_geocompute_executor_v4.py`) |
| Artifact types (`app/lib/gis/artifacts.py`) | 21 | contract + registry tested | `ArtifactRecord.to_dict/from_dict` round-trip tested (`tests/unit/test_artifact_registry.py:66`) |
| MapSpec intents (`app/services/mapspec/lifecycle_engine.py`, ~20 intent types) | — | dedicated tests + goldens | Wrapper tools for checkpoint/rollback untested (see §3.1) |
| Recipe packs (`app/services/gis_harness/recipe_packs/`) | 24 domains, ≥140 V2 recipes | compile-coverage test (`test_conformance_corpus.py:129-148`) | `disaster.py`, `public_health.py` have no domain-specific scenario tests (compile-only) |
| ToolRegistry meta-contract | — | `test_tool_meta_contract.py`, `test_tool_surface.py`, `test_prompt_tool_name_integrity_438.py` | Strong: descriptor kwargs fail loud at registration (registry.py:441-462) |

### 3.1 The 29 tools with zero direct test references

Grouped by risk (file:line = registration site):

| Group | Tools | Risk note |
|---|---|---|
| State mutation (map/session) | `apply_layer_style` (`app/tools/cartography.py:99`, `side_effect="state_mutation"`), `webgis_validate`/`webgis_compile_maplibre`/`webgis_checkpoint`/`webgis_rollback` (`app/tools/cartography_tools.py:715/734/756/780`, all `state_mutation`, `data_mutations=("session_state",...)`) | Service layer (`mapspec_store`) is tested; wrappers own session_id plumbing + arg models — untested |
| Geocompute execution | `execute_execution_plan` (`app/tools/geocompute_tools.py:71`), `get_execution_run` (`:139`) | The primary "run the plan" entry the LLM calls; only service level tested |
| Data lineage / discovery | `search_datasets` (`app/tools/data_discovery.py:60`), `describe_artifact` (`:139`), `find_artifacts_by_role` (`:172`), `get_lineage` (`:202`) | Fabric/canal service tests exist; tool surface (arg coercion, error shape) untested |
| Chart / misc | `control_floating_chart` (`app/tools/cartography.py:578`), `repair_spatial_dataset` (`app/tools/project_tools.py:335`) | `repair_spatial_dataset` mutates session datasets — highest-value single gap |
| Science wrappers with oracle-covered cores | `geary_c`, `quadrat_analysis`, `mantel_test_analysis`, `cross_pcf_analysis`, `space_time_k_analysis` (`app/tools/spatial_stats.py`, `point_pattern_tools.py`); `ica_transform`, `mnf_transform`, `sar_temporal_stats`, `sar_vh_ratio` (`app/tools/remote_sensing.py`); `mgwr_regression` (`app/tools/spatial_stats.py`); `detect_change_cva`, `detect_ratio_change` (`app/tools/change_detection.py`); `interpolation_model_compare` (`app/tools/advanced_spatial.py`); `temporal_changepoint` (`app/tools/temporal_tools.py`) | Core math oracle-tested (`quadrat_test`, `mantel_test`, `vh_ratio`, `temporal_stack_statistics`, MGWR in `tests/unit/lib/test_spatial_stats_v3.py`, CVA in `tests/unit/lib/test_change_detection_science.py`); only the tool wrapper (args model / narrated output) is unexercised |

---

## 4. Route / authz gaps

Enforcement is structural and strong:
- Meta-test walks every route AST: mutating routes must carry an auth-enforcing `Depends` or appear in `PUBLIC_MUTATING_ALLOWLIST` with a non-empty reason; unused allowlist entries fail (`tests/test_api_auth_required.py:57-65, 318-347`). Allowlist has exactly 5 entries (auth register/login/refresh + stateless `geocompute.validate_execution_plan`).
- Cross-tenant/ownership: `tests/test_zero_review_authz.py` (project visibility, `authorize_session_write`), `tests/integration/test_cross_tenant_isolation.py`, `test_map_export_download_auth.py` (export owner binding), `test_upload_ownership_matrix_1109.py`, `test_sec08_*`, `test_ws_auth.py`, `test_api_path_traversal.py`, `test_nginx_security.py`.
- `pi_tools` uses bridge secret + turn token (`app/api/routes/pi_tools.py:36-64`, `app/core/bridge_secret.py`), with fail-closed tests (`test_pi_status_fail_closed.py`, `test_pi_bridge_lock.py`).

Gaps (all MEDIUM unless noted):
1. **Read-route authz is convention, not meta-tested.** The AST test only covers *mutating* verbs. GET data-bearing routes rely on per-route `require_owned_session` (e.g. `app/api/routes/layer.py:56,119,261,409,492`, `analysis_graph.py:17`); a new GET endpoint added without ownership dependency would not fail CI. Evidence of the class being real: it took explicit fix rounds (`test_round2_review_findings.py:35-100` project visibility).
2. **`app/api/routes/geocompute.py`, `analysis_graph.py`, `metrics.py`, `mapspec_mutations.py` have no dedicated HTTP-level test file** (service-level tests exist: `test_geocompute_execution.py`, `test_analysis_graph.py`, `test_metrics_api.py`, `test_chat_session_plan_route.py`). Contract drift (status codes, response models) on 5+6+1+1 endpoints is only caught indirectly.
3. `chat_resume.py` resume stream is auth via session key resolution; covered only through `test_sse_resume.py` happy paths — no anonymous/foreign-session resume denial test found by name.

---

## 5. Map / exporter gaps

Well covered overall:
- `mvt.py` (1,744 L): `tests/unit/test_mvt_encoder.py`, whitelist test (`test_mvt_whitelist_668.py`), pole/NaN vertex edge tests (`test_round2_review_findings.py:138,155`), 64-bit int props, antimeridian, perf cache benchmark.
- `mapspec_to_svg.py` (745 L): `tests/unit/test_mapspec_to_svg.py` (12 tests) + frontend parity test `mapspec-compiler/mapspec-to-svg.parity.test.ts` (TS↔Python dual-implementation drift guard — notable strength).
- Cartography: `tests/cartography/` 34 files + `golden_corpus/` 509 files (512-golden release gate, `-m cartography`), closed-loop and concurrency tests.
- Converters: `test_analysis_cartography_converter.py`, `test_raster_cartography_converter.py`, `test_mapspec_source.py`.
- Lifecycle engine (1,964 L, ~20 intents): `tests/unit/test_mapspec_lifecycle_engine.py`, `tests/cartography/test_mapspec_mutation_revision.py`, `test_cartography_closed_loop.py`.

Gaps:
1. **Checkpoint/rollback TOOL wrappers untested** (`webgis_checkpoint`, `webgis_rollback`, `cartography_tools.py:756/780`) while `mapspec_store.checkpoint/rollback` and workspace snapshot/clone/rebind are tested (`tests/data/test_workspace_snapshot.py`). The wrapper is the only untested hop in the "save my map / undo" user journey. **HIGH** (silent loss of user map state on arg-model regression).
2. `mapspec_checkpoint_store.py` compatibility re-export: zero refs (trivial, MEDIUM→LOW).
3. SVG font/glyph paths rely on the golden corpus; `Glyph missing` warnings are filter-suppressed in `pytest.ini` — a silently missing CJK glyph in a new component would not fail unless a golden pins it. MEDIUM.

## 6. Artifact gaps

- `ArtifactContract` (`app/lib/data/artifact_contract.py`): 20+ tests incl. bounded collections, summary stability, category vocabulary enforcement (`tests/data/test_artifact_contract.py`). All 4 `from_*` bridges exercised.
- Type registry: 21 seed types; duplicate-registration rejection; capability parity test validates every capability artifact reference exists (`capability_registry.py:95-106` + `test_capability_registry_parity.py:99`).
- `ArtifactRecord` to_dict/from_dict round-trip tested (`tests/unit/test_artifact_registry.py:66`); ledger lineage/dispatch-input capture tested (`tests/data/test_dispatch_lineage_inputs.py`, `test_lineage_query.py`, `test_cache_lineage_v4.py`).

Gap: **the lineage/discovery TOOL surface** (`get_lineage`, `find_artifacts_by_role`, `describe_artifact`, `search_datasets` — §3.1) is the untested consumer of this well-tested contract; the artifact *data plane* is fine, the artifact *query language for the LLM* is not. HIGH (wrong lineage answer ⇒ unreproducible analysis reuse).

## 7. Heavy-algorithm guard gaps

Present (strong):
- `RasterResourceGuard` (`app/lib/geo_analysis/raster_guard.py:65-72`): 250M px / 1 GiB / 10,000× upscale pre-checks with suggested resolutions + typed error; wired into interpolation, raster_math, raster_windowed, raster_change; tested (`tests/unit/test_raster_resource_guard.py`).
- Estimate-before-allocate constants everywhere: `point_pattern.py:46-56` (20k obs, 50M pairs, 499 envelopes), `kriging.py:144-147` (500k input / 2k fit / 200k pairs / 24 neighbors), `density.py:47-68` (grid cells, KDE points, eval products), `terrain.py:78-87` (window, hydro cells, radii). Explicit `correction_hint` strings are oracle-pinned in edge_cases domain (87 cases).
- Cancellation: `app/lib/cancellation.py` `cancellable()` checkpoints in kriging/statistics/spatial_regression; token semantics tested (`tests/jobs/test_job_cancellation.py`); Celery chain limits (`app/tasks/explorer/task_chain.py`: `soft_time_limit=30, time_limit=30, max_retries=2`).
- Seed discipline enforced at registry level (`algorithm_registry.py:375-421`: deterministic⇔seed-policy consistency validation).

Gaps:
1. **Permutation loops in `point_pattern.py` have no cancellation checkpoints** (`for i in range(permutations)` at lines 212, 334, 1012, 1179, 1373, 1481, 1657). Worst case: Ripley envelopes 499 perms × O(n²) at the 20k-point cap inside a THREAD-policy tool — cancellation and the 300 s wall-clock budget (`registry.py:174`) cannot preempt it (to_thread workers are unkillable; leaked-thread counter at `registry.py:179` exists but no test covers cancellation-during-heavy-tool). HIGH (stuck worker slots, degraded service, cancelled-yet-running jobs).
2. SAR/rs/terrain families (26 modules) lack `cancellable()` entirely (raster loops are numpy-vectorized, which is defensible, but `rs_v3.py`, `sar_*` per-tile Python loops are not). MEDIUM.
3. **`interpolation_compare.py` (backend of `interpolation_model_compare`) has neither cancellation nor an explicit top-level cost budget** — it fans out to multiple interpolators sequentially; a large input multiplies the heaviest interpolator cost by N models. MEDIUM-HIGH.
4. Data-fabric SQL compiler (`query/compilers.py:105-268`): zero direct tests for `compile_predicate_sql` / `compile_temporal_sql` / `compile_spatial_sql` / `quote_ident` / `geojson_to_wkt`; only the legacy parser (`postgis_adapter._parse_safe_where`, `test_data_fabric_postgis_where_431.py`) is tested, while the v2 AST path is what actually runs (`postgis_adapter.py:1000,1200`). Parameterized by construction (injection low) but NULL-handling, LIKE-wildcard semantics, and `allowed_fields` enforcement can regress silently. **HIGH** (silent wrong query results across all PostGIS fabric sources).
5. External-API capabilities (`external_route_planning`, `transit_routing`, `traffic_status` → tools `plan_route`, `search_transit_route`, `get_traffic_status`) are honestly marked `deterministic=False` but have no contract test for API-failure shaping (timeouts, quota errors) the way geocoding has (`test_geocode_stage.py`, `test_osm_nominatim_fix.py`). MEDIUM.

## 8. Harness / workflow gaps

- Strong: 45 test files under `tests/unit/gis_harness/` incl. conformance corpus, golden cases orchestration, runtime v4 scenarios, workflow guards/compilers/families, semantic QA, product verdict/lineage; chaos suites at top level (`test_runtime_chaos_*.py`, `test_adversarial_sse_chaos.py`); recipe compilation sweep ≥140 recipes.
- Gaps:
  1. `chat/turn_recovery.py` (75 L) and `chat/formatters.py` (117 L): zero direct tests (consumed by `execution_engine.py`/`prompt.py`). Recovery-after-crash mid-turn is only covered via generic resume tests. MEDIUM-HIGH.
  2. `geocompute/_async_bridge.py` (38 L): zero refs. MEDIUM.
  3. `recipe_packs/disaster.py` (174 L) + `public_health.py` (148 L): compile-coverage only; no behavioral scenario asserts the produced plan is scientifically sensible (other packs like density/transport have 30-56 refs). MEDIUM.
  4. (`completion/validators/viewport_export.py` initially flagged zero-ref; its functions `assess_export_parity`/`derive_result_bbox` ARE exercised via `tests/unit/gis_harness/test_map_completion.py:860-883` — resolved, no gap.)

## 9. Frontend gaps

Density is good: 253 test files; `components/map` 39, `lib/map-kit` 20, `components/sidebar` 18, `lib/hooks` 17, `lib/map-commands` 15, `components/chat` 15, `mapspec-compiler` 12 (incl. TS↔Python SVG parity), `lib/api` 12, store slices mostly covered (`caps`, `reorder`, `dockSlice`, `hud`, `persist-hydration`, `resultsSlice` via `test/results/resultsSlice.test.ts`).

Gaps:
1. **`lib/results/normalize.ts` (334 L) + `families.ts` (243 L) + `suggested-actions.ts` (51 L): zero tests.** This is the client-side mirror of backend result normalization/family semantics; drift renders wrong result groups/suggested actions to the user with no failure signal. HIGH (silent wrong UI semantics).
2. `lib/session/session-plan-delta.ts` (162 L): zero tests — plan-progress sync to UI. MEDIUM.
3. `lib/store/slices/{layersSlice 215L, uiSlice 219L, settingsSlice, taskSlice}`: no dedicated files (partial incidental coverage via `uiSlice.viewport.test.ts`, `slices.test.ts`). MEDIUM.
4. `components/panel/rag-independent-panel.tsx`, `components/providers/*` (client-providers, system-message-bridge): zero tests. MEDIUM.
5. `lib/auth/use-auth-user.ts` (companion to tested `tokenStore.ts`) and `lib/auth` 1-test/2-src: token refresh edge paths lean on backend tests. MEDIUM.
6. `frontend/tests/` tree holds only 2 explorer component tests; explorer panels (2 src in `components/explorer`) rely on the backend harness suites. LOW-MEDIUM.

---

## 10. Top-20 prioritized risk list

| # | Risk | Area | Evidence | Why it matters |
|---|---|---|---|---|
| 1 | `execute_execution_plan` / `get_execution_run` tools have zero tests | Geocompute tool surface | `app/tools/geocompute_tools.py:71,139`; 0 name-refs in `tests/` | Primary LLM entry to run plans; arg-model regression silently breaks or mis-routes every analysis execution. HIGH |
| 2 | Data-fabric SQL compiler untested (`compile_predicate_sql` et al.) | Fabric query | `app/services/data_fabric/query/compilers.py:98-268`; 0 direct test refs; v2 path used at `postgis_adapter.py:1000,1200` | Silent wrong query results / NULL & LIKE semantics drift across all PostGIS sources. HIGH |
| 3 | Lineage/discovery tool family untested | Artifacts/discovery | `app/tools/data_discovery.py:60,139,172,202` | LLM gets wrong lineage/artifact answers ⇒ unreproducible analysis reuse; contract below is tested, surface is not. HIGH |
| 4 | `webgis_checkpoint` / `webgis_rollback` wrappers untested | MapSpec persistence | `app/tools/cartography_tools.py:756,780` (`state_mutation`) | "Save/undo my map" journey loses user state on wrapper regression; service layer can't catch arg-model bugs. HIGH |
| 5 | No cancellation checkpoints in permutation/envelope loops | Point-pattern science | `app/lib/geo_analysis/point_pattern.py:212,334,1012,1179,1373,1481,1657`; caps at `:46-56` | 499-perm × O(n²) compute is un-interruptible; cancelled jobs keep burning THREAD-pool slots; no test asserts cancel latency. HIGH |
| 6 | `apply_layer_style` (state_mutation) zero tests | Cartography tool | `app/tools/cartography.py:99` | Style mutations are the most frequent map mutation; silent breakage degrades every styling turn. HIGH |
| 7 | `repair_spatial_dataset` zero tests | Data repair | `app/tools/project_tools.py:335` | Mutating "fix my data" tool; wrong behavior corrupts session datasets with user trust in the fix. HIGH |
| 8 | Frontend results normalization mirror untested | Frontend results | `frontend/lib/results/normalize.ts` (334 L), `families.ts` (243 L) | Client/server semantics drift renders wrong result families/actions — silent wrong UI. HIGH |
| 9 | `interpolation_model_compare` has no cost budget/cancellation | Heavy algorithms | `app/lib/geo_analysis/interpolation_compare.py`; tool `app/tools/advanced_spatial.py` | N-model fan-out multiplies worst-case cost; resource exhaustion on large inputs. MEDIUM-HIGH |
| 10 | 47/112 capability ids never named in tests | Capability registry | `rg -w <cap-id> tests/` → 47 misses (incl. `sar_analysis`, `od_flow_mapping`, `geometry_*`, `terrain_*`) | Parity tests guarantee resolution, not behavior; a capability can silently bind to a broken/weak tool candidate. MEDIUM |
| 11 | Read-route ownership is convention-only (meta-test covers mutations) | API authz | `tests/test_api_auth_required.py:318` (mutating only); GET ownership via per-route `require_owned_session` (`layer.py:56...`) | Future GET data endpoint without ownership dep ships green — cross-session data exposure class. MEDIUM-HIGH (latent security) |
| 12 | `chat/turn_recovery.py` + `formatters.py` untested | Chat engine | 75 L + 117 L, 0 refs | Mid-turn crash recovery path only exercised indirectly; broken recovery = lost user turns. MEDIUM-HIGH |
| 13 | External routing tools lack failure-shaping tests | Network/external API | `capabilities/network.py:129-160`; tools `plan_route`/`search_transit_route`/`get_traffic_status` | Timeout/quota/error shapes unseen ⇒ LLM hallucinates results from malformed tool output. MEDIUM |
| 14 | Geocompute/analysis-graph/metrics routes lack HTTP-level tests | API layer | 0 route-importing test files for `geocompute.py`, `analysis_graph.py`, `metrics.py`, `mapspec_mutations.py` (service tests exist) | Status-code/response-model drift undetected at the HTTP boundary. MEDIUM |
| 15 | SAR/raster-spectral families lack cancellation (vectorization-dependent) | Heavy algorithms | 26 modules without `cancellable()` (incl. `rs_v3.py`, `sar_*.py`, `terrain.py` Python loops) | Same stuck-slot failure mode as #5, lower frequency. MEDIUM |
| 16 | `webgis_validate` / `webgis_compile_maplibre` wrappers untested | Map validation | `app/tools/cartography_tools.py:715,734` | LLM-side validation verdicts unverified; silent "valid" on broken maps. MEDIUM |
| 17 | `disaster` / `public_health` recipe packs compile-only | Harness recipes | `recipe_packs/disaster.py` (174 L), `public_health.py` (148 L); only `test_v2_recipe_compilation_coverage` touches them | A plan that compiles but plans wrong science for these domains ships unnoticed. MEDIUM |
| 18 | Store slices `layersSlice`/`uiSlice`/`taskSlice` without dedicated tests | Frontend state | `frontend/lib/store/slices/` (215/219/78 L) | Layer-list and HUD regressions (the app's control surface) only partially caught. MEDIUM |
| 19 | `session-plan-delta.ts` untested | Frontend session | `frontend/lib/session/session-plan-delta.ts` (162 L) | Plan progress UI desyncs silently from backend plan state. MEDIUM |
| 20 | SVG glyph-suppression may mask CJK glyph loss in non-golden components | Cartography | `pytest.ini filterwarnings` ignores `Glyph .* missing`; goldens cover 512 cases but new components add labels freely | Silent blank-tofu labels in exports for uncovered components. MEDIUM |

**Bottom line:** add wrapper-level dispatch tests for the 29 zero-ref tools (prioritize #1, #3, #4, #6, #7), direct unit tests for the fabric SQL compiler, `cancellable()` in the permutation loops, and a frontend test for `lib/results` — those five moves close most of the identified capability risk without touching the already-excellent science-oracle and authz infrastructure.
