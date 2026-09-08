# Algorithm Registry / Domain Packs / Scientific Algorithm System

## 1. File inventory

| Path | Purpose |
|---|---|
| `app/lib/gis/algorithm_registry.py` | `AlgorithmDescriptor`/`BackendVariant` models, `AlgorithmRegistry` (by-id/by-capability indexes, register/validate), singleton accessor (algorithm_registry.py:1-8 states it is a semantic catalog, NOT an execution engine) |
| `app/lib/gis/capability_registry.py` | `CapabilityDescriptor`, `CapabilityRegistry` — the stable "what capability is needed" vocabulary consumed by plans/recipes |
| `app/lib/gis/algorithm_resolver.py` | `AlgorithmResolver` — deterministic capability→algorithm→tool adjudication with scientific gates, cost model, fallback trails (algorithm_resolver.py:1-9) |
| `app/lib/gis/algorithms/` | 13 domain packs (`data_access, geometry, aggregation, density, statistics, point_pattern, interpolation, network, terrain, raster, remote_sensing, temporal, decision`), 171 descriptors total; each exports `ALGORITHMS` + optional `PARAMETER_CONTRACTS` (algorithms/__init__.py:33-49) |
| `app/lib/gis/capabilities/` | 11 capability domain packs exporting `CAPABILITIES` (capabilities/__init__.py:11-19) |
| `app/lib/gis/backend_selection.py` | `select_backend(algorithm_id, ScaleProfile)` — pure-function variant choice from `backend_variants` scale windows (backend_selection.py:1-8,91) |
| `app/lib/gis/parameter_contracts.py` | `ParameterContract`/`ParameterSpec` + validation; aggregates domain contract packs (parameter_contracts.py:334-338) |
| `app/lib/gis/cost_model.py` | `infer_execution_policy`, `score_algorithm` used by resolver |
| `app/lib/gis/crs_safety.py`, `scientific_preconditions.py`, `uncertainty.py`, `method_references.py`, `scientific_evidence.py` | Vocabularies consumed by registry `validate()` and resolver gates; evidence-block builder `build_evidence` (scientific_evidence.py:233) |
| `app/lib/gis/artifacts.py` | `ArtifactTypeRegistry` (artifact_types like `density_surface`, `raster_surface`; artifacts.py:158-172) |
| `app/lib/gis/runtime_manifest.py` | Projects all registries into a manifest; cross-registry integrity (`algorithm_dangling_capability`, `planned_algorithm_missing_tools`) (runtime_manifest.py:222-291) |
| `app/services/gis_harness/registry_validation.py` | `validate_gis_library` — runs all registry `validate()`s + `validate_algorithm_tool_parameter_parity` (registry_validation.py:43-80,238) |
| `app/services/gis_harness/planner.py` | Harness planner; delegates selection to AlgorithmResolver (planner.py:74-78,300-315,851) |
| `app/services/geocompute/` | Plan-graph execution engine (`plan.py` ExecutionPlan/ExecutionNode, `executor.py` GeoExecutionEngine, `ops.py` operators, `graph.py` topo/order) exposed via `app/api/routes/geocompute.py` + `app/tools/geocompute_tools.py` |
| `app/tools/registry.py` | ToolRegistry: tool registration + dispatch; derives tool→capability/algorithms backfill from AlgorithmRegistry (registry.py:40-75,880-935) |
| `app/tools/advanced_spatial.py`, `network_tools.py`, `point_pattern_tools.py`, `terrain_analysis.py`, `remote_sensing.py`, `change_detection.py`, `flow_tools.py`, `spatial_stats.py`, `temporal_tools.py` | Tool implementations bound to algorithm ids via `tool_candidates`; call `select_backend` and emit evidence diagnostics |
| `tests/unit/lib/test_spatial_stats_conformance.py` etc. | Conformance suites referenced by descriptor `conformance_tests` pytest node ids |

## 2. AlgorithmDescriptor full fields (algorithm_registry.py:91-139)

`id, name, capabilities:List[str], category, subcategory, tags, input_artifact_types, output_artifact_type, geometry_requirements, required_fields, optional_fields, min_features, max_features_hint, crs_requirements, unit_requirements, parameter_contract_ref, deterministic=True, approximate=False, complexity, cpu_cost/memory_cost/io_cost (CostLevel low|medium|high), preferred_execution_policy, tool_candidates:List[str], runtime_status ("native"|"planned"|"unavailable", :18), compatible_map_models, fallback_algorithms, priority=50, version="1.0", contract_version=1` plus VNext/ADR-0099 scientific block: `algorithm_family, method_references, assumptions, limitations, crs_class (CRSSpatialClass closed vocab :27-30), scientific_preconditions, uncertainty_outputs, random_seed_policy (closed vocab :37-39), numerical_tolerance, scientific_status (""|EXPERIMENTAL|VALIDATED|PRODUCTION|DEPRECATED :36), conformance_tests (pytest node ids :136), backend_variants:List[BackendVariant] (max 4, :168-176), fallback_semantics:Dict[str, FallbackSemanticsClass] (:33-35)`.

`BackendVariant` (algorithm_registry.py:54-88): `id, backend (BACKEND_VOCABULARY closed set :41-45), tool, deterministic, notes (≤160 chars), min_features/max_features` (scale envelope, validated lo≤hi). Field-level bounds: assumptions/limitations ≤8 items ×160 chars (:141-144); conformance_tests ≤8 ×220 chars (:151-156); variant ids unique (:173-175); `algorithm_family` must be identifier-shaped (:178-183).

Scale envelope = `min_features` (hard gate in resolver, algorithm_resolver.py:144-150), `max_features_hint` (render cap → `over_render_cap` rejection triggering fallback, :154-160), and per-variant `min_features/max_features` windows (backend_selection.py:81-88).

## 3. Registration flow, collision policy, namespaces

- Domain packs are the single source of truth: each `app/lib/gis/algorithms/<domain>.py` module owns its `ALGORITHMS` list; central file "only aggregates and validates" (algorithms/density.py:1-7). Adding a domain = new module + entry in `_ALL_MODULES` tuple (algorithms/__init__.py:26-30); `iter_domain_packs()` yields deterministically ordered packs (algorithms/__init__.py:33-36).
- `get_algorithm_registry()` builds the singleton once, calling `load_builtins()` → `register()` per descriptor (algorithm_registry.py:503-514,208-212). There is NO runtime re-registration path in app code; `reset_algorithm_registry()` exists for tests (algorithm_registry.py:511-513).
- Collision policy: duplicate algorithm id → `ValueError("duplicate algorithm id")` (algorithm_registry.py:214-216). Same for capability ids (capability_registry.py:67-69) and artifact types (artifacts.py:169-172). Per-capability candidate lists are deduped and stably sorted by `(priority, id)` (algorithm_registry.py:220-227).
- Namespace conventions: algorithm ids are dotted `domain.family[.variant]` — e.g. `density.visual.heatmap`, `interpolation.kriging` (algorithms/density.py:28, algorithms/remote_sensing.py:37), `remote.ndvi`, `remote.change.raster`; capability ids are snake_case verbs/nouns (`kde_density`, `raster_change_detection`); tool names are snake_case functions registered in ToolRegistry. `category` is a free string (`remote_sensing`, `density`, ...) — the old `ALGORITHM_TAXONOMY` dict was deleted as dead metadata (algorithm_registry.py:49-51).

## 4. CapabilityRegistry role and relation

CapabilityRegistry is the demand-side vocabulary ("what is needed"), deliberately unbound from implementations (capability_registry.py:1-6); `CapabilityDescriptor` fields at capability_registry.py:18-41 (inputs/outputs artifact refs, geometry/field constraints, domain/category, `preferred_execution`, `supports_large_data`, `fallback_capabilities`, `status`, `purpose_template` for plan text). AlgorithmRegistry is the supply side; the resolver joins them: `cap.status=="native"` → `algorithms_for_capability(capability)` → per-algorithm gates (algorithm_resolver.py:247-291). `AlgorithmRegistry.validate()` enforces the contract both ways: every capability must have ≥1 algorithm (algorithm_registry.py:370-372) and an algorithm's `output_artifact_type` must be a member of its capability's declared outputs (algorithm_registry.py:331-341). ToolRegistry backfills `ToolDescriptor.capabilities/algorithms` from AlgorithmRegistry via `tool_to_capability()`/`tool_to_algorithms()` derived indexes (registry.py:54-75,897-929; algorithm_registry.py:256-303), cached and invalidated on `register`.

## 5. Conformance / corpus machinery

- Descriptors list `conformance_tests` as pytest node ids (`tests/...::func`). Registry validation checks file existence and node-level existence via deterministic AST parsing (zero import) when a `tests/` dir is present (algorithm_registry.py:464-497) — node renaming without descriptor update fails validation.
- Maturity gating makes conformance mandatory: `scientific_status="PRODUCTION"` requires native runtime, a registered non-empty `parameter_contract_ref`, method references, and conformance tests; `"VALIDATED"` requires conformance tests; `"DEPRECATED"` requires a fallback (algorithm_registry.py:450-463).
- All backend variants "must pass the same conformance suite" (BackendVariant docstring, algorithm_registry.py:55-58).
- Validation entrypoint `validate_gis_library` aggregates artifact/capability/algorithm validates plus `validate_algorithm_tool_parameter_parity` (tool schema must contain contract-required params; registry_validation.py:52,77,238-285) and optional `available_tools` existence check (algorithm_registry.py:350-354). Runtime manifest performs the same cross-registry audit at manifest build (runtime_manifest.py:281-291).
- ~178 `conformance_tests` declarations across 13 domain packs (counts per file: remote_sensing 34, statistics 26, terrain 26, network 16, interpolation 15, point_pattern 12, ...).

## 6. Execution path

Resolution and execution are strictly separated (algorithm_registry.py:1-8). Flow:

1. Harness planner calls `AlgorithmResolver.resolve(capability, profile=dataset_profile, available_tools, policy_hint, export, algorithm_hint)` — pure function, no I/O/LLM/data loading (algorithm_resolver.py:87-89,228-262). Output `AlgorithmResolution` carries chosen algorithm/tool, rejection reasons (bounded 8), fallback trail with `FallbackStep.semantics` (equivalent/approximation/proxy/degraded), cost score/breakdown, scientific warnings, required transformations (algorithm_resolver.py:27-58,364-372).
2. Selection order: hard gates (native, tool registered, geometry, min/max features, required fields, CRS class gate with reproject hint, scientific preconditions 5-verdict evaluation) → cost model `score_algorithm` under inferred `ExecutionPolicy` → `(priority, score, id)` sort → explicit `algorithm_hint` promoted to front if it passed gates, else compensated with a recorded substitution trail (algorithm_resolver.py:101-211,293-362).
3. The resolved **tool** runs through `ToolRegistry.dispatch` with `ToolExecutionPolicy` INLINE/ASYNC/THREAD/CELERY (registry.py:130-134); THREAD wraps in `asyncio.to_thread` under semaphores; heavy tools submit durable Celery jobs via `submit_durable_job` (jobs/submit.py:42; example change_detection.py:85-110) with idempotency keys, progress/cancel/results in the task center.
4. Tool bodies call `app/lib/geo_analysis/*` implementations, run `select_backend(algorithm_id, ScaleProfile(feature_count=n))` and record the variant decision as an evidence `Diagnostic` (point_pattern_tools.py:86-101; advanced_spatial.py:281-291); outputs carry `ScientificEvidence` blocks from `build_evidence` (scientific_evidence.py:233) including uncertainty blocks.
5. Alternative path: geocompute plan graphs. `ExecutionPlan`/`ExecutionNode` (plan.py:150-247) validated (graph.py:56-164), executed by `GeoExecutionEngine.execute_plan` — in-process synchronous core, REST offloads via `to_thread` (executor.py:15), per-node deadline (`remaining_seconds`, ops.py:58-63), `CancellationToken` + `cancel_run` writing cancel requests to the job row (executor.py:219-354), admission check against `ResourceBudget` (executor.py:371), wave scheduling with ancestor-failure sweep and artifact_register/materialize operators (ops.py:558-623); exposed at `app/api/routes/geocompute.py` and tool `geocompute_tools.py:106-126`.

## 7. Gaps / risks for a third-party Algorithm Extension SDK

1. **No runtime registration API**: registration happens only in `_load_seed_algorithms()` at first singleton access (algorithm_registry.py:190-196,503-508); a plugin SDK would need either a process-startup hook or a new registry API — currently nothing calls `AlgorithmRegistry.register` outside builtins/tests.
2. **Domain packs are hard-coded imports**: `_ALL_MODULES` static tuple (algorithms/__init__.py:26-30) — third parties cannot add a domain pack without editing core; no entry-point/plugin discovery.
3. **Closed vocabularies as hard gates**: `crs_class`, `random_seed_policy`, `FALLBACK_SEMANTICS`, `BACKEND_VOCABULARY`, `_UNIT_VOCABULARY`, uncertainty vocabulary are frozensets/Literals validated at registration (algorithm_registry.py:23-45,363-411); new backend or unit families require core edits ("新增需同步 validate 消费方", :40).
4. **Conformance validation is repo-layout bound**: node existence check requires `tests/` under CWD and `tests/`-relative paths (algorithm_registry.py:471-479) — external packages' tests can never validate; PRODUCTION/VALIDATED maturity is unreachable for out-of-repo algorithms.
5. **Tool binding is by name string**: `tool_candidates` must exist in the in-process ToolRegistry and dispatch channel (resolver `tool_unavailable`, algorithm_resolver.py:117-120); there is no remote/external tool binding, so third-party algorithms must also register tools in-process (tiering, semaphore, durable-job plumbing all internal).
6. **Singletons + process caches**: module-level singletons with `reset_*` test-only hooks and `_DERIVED_CAPABILITY_CACHE` (registry.py:48-50) assume immutable seeds; hot unloading/reloading extension packs is unsupported.
7. **Bounded metadata**: field validators truncate/limit scientific text (≤8 items, 160 chars) and ≤4 backend variants (algorithm_registry.py:141-176) — fine for curated packs, restrictive for rich third-party docs.
8. **No versioned API/ABI for descriptors**: `contract_version=1` exists on descriptors/tools but there is no compatibility negotiation or schema-publishing surface for external SDK consumers; manifest (`runtime_manifest.py`) is read-only introspection.
9. **Execution isolation gap**: resolver is safe (pure), but heavy algorithms rely on per-tool `timeout` metadata and Celery budgets; geocompute has budgets/deadlines/cancellation, while plain tool dispatch timeout enforcement is per-policy and not uniformly applied to THREAD tools.
