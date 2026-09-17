# CURRENT ARCHITECTURE — plug-in surfaces for the temporal cube / SAR×optical runtime

All paths relative to repo root; line numbers verified at baseline `faa453a8`.

## 1. app/services/data_fabric — refs / tickets / materialization

- **contracts.py** (`app/services/data_fabric/contracts.py`): ads-v1 frozen contracts. `D1DatasetDescriptor(DatasetDescriptor)` (L91) extends `app.schemas.data_fabric_schema.DatasetDescriptor` with `TemporalCoverage` (L43, `start/end/granularity/declared_only`), `QualitySignals` (L64: `declared_crs/declared_completeness/known_issues/verified`), `CostHint` (L79); `AcquisitionPlan` (L163), `FallbackDecision` (L203, `comparable=False` forces non-comparable downstream), `AcquisitionFact` (L228). `CONTRACTS_VERSION="1.0"` (L33). **This is the canonical "descriptor as refs-only, additive optional fields" pattern.**
- **registry.py**: `AdapterSpec` (L32), `AdapterRegistry` (L56, `register/unregister`, no silent dup alias), `_build_registry` (L118), `resolve_adapter_spec` (L218), `build_adapter(profile)` (L223).
- **adapters/**: `stac_adapter.py` (STAC V2, ADR-0094 Wave F: normalize → plan → POST /search; result modes descriptor/statistics/sample/features; `safe_json_get` with byte caps; typed errors from `app/services/data_fabric/errors.py`). `cog_adapter.py` (ads-v1 DS1, ADR-0171): `COGAdapter` — **metadata-only raster seam**: reads real header (bounds/CRS/resolution/band count/dtypes) via rasterio; vector query raises typed `QueryUnsupportedError` ("raster retrieval is a materialization concern"); `_resolve_raster` (L24) with safe-local-path resolution.
- **materialization_service.py**: `MaterializationService` (L113). Refs-only contract: FEATURES/MATERIALIZE → FeatureCollection → SessionStore ref with prefix `ref:fabric-parquet/` (L40 `GEOPARQUET_REF_PREFIX`); STATISTICS/DESCRIPTOR/SAMPLE return bounded data with `query_evidence`, no ref. "ref 存在 ⟺ payload 可取回；失败 = typed MATERIALIZATION_FAILED + ref_id=None, 绝不伪造". Comment at L40-41 explicitly cites the raster precedent `ref:raster/<id>`.
- **spatial_catalog.py**: `SpatialCatalogService` (L26) — bbox intersect catalog helpers.
- **source_registry.py** + `config/sources/*.yaml`: adding a source is a YAML file (CHANGELOG DS1); capability claims are linted vs real adapter flags (`scripts/check_source_registry.py`).

**How big payloads stay out of context**: opaque refs (`ref:raster/<id>`, `ref:fabric-parquet/<id>`) + bounded result modes (descriptor/statistics/sample) + `limits.py enforce_result_bounds` + typed errors. This is the pattern the cube descriptor must follow.

## 2. app/services/geocompute — execution graph

- **plan.py**: `NodeCategory` (L30), `ExecutionPolicyKind` (L54), `PayloadKind` (L84), `ResourceClass` (L99), `ResourceBudget` (L130), `CrsExpectation` (L143), `PartitionSpec` (L150), `ExecutionNode` (L181), `ExecutionPlan` (L256), `NodeEvidence` (L288), `ExecutionRun` (L331).
- **ops.py**: operator functions per category — `_op_query` (L244), `_op_raster_window_operation` (L449, raster arrives as ref string resolved via `_raster_from_inputs` L512), `_op_materialize` (L559), `_op_artifact_register` (L590), `_op_source_scan` (L624); dispatcher `execute_node` (L684); `has_operator`/`wired_categories` (L676/L680). Row budgets via `_check_row_budget` (L220). **A new op = a `_op_*` function + a NodeCategory wiring — this is where a cube-build/fuse op would plug.**
- **executor.py**: `NodeResultStore` (L238), `GeoExecutionEngine` (L422), charge ledger `_RunChargeLedger` (L112), output fingerprints (L139).
- **graph.py**: `validate_plan` (L56), `_validate_node_contract` (L83), `_validate_edge_contracts` (L149), `topo_wave_order` (L188), `invalidation_set` (L232), reuse keys (L247/L258).
- **budgets.py**: `BudgetLimits` (L43), `ResourceGovernor` (L79), `ScopeUsage` (L56).
- **run_evidence.py**: `build_snapshot` (L79) with `_evidence_full`/`_evidence_compact` size gating, `save_snapshot`/`load_snapshot` (L170/L258).
- **api.py**: `build_plan_from_json` (L66), `run_plan_sync` (L175).

## 3. Raster reading/writing stack

- **app/lib/geo_raster/** — the raster IO foundation (NOT services):
  - `source.py`: `RasterSource` (L40) hierarchy — `LocalFileRasterSource` (L155), `RemoteRasterSource` (L163), `COGRasterSource` (L173), `SessionRefRasterSource` (L185, consumes `ref:raster/...` / `ref:geojson` opaque refs), `ProjectArtifactRasterSource` (L223). `_RASTER_REF_PREFIXES = ("ref:raster/",)` (L27).
  - `reader.py`: `RasterMetadata` (L40), `RasterReader` (L80).
  - `cog.py`: `write_cog` (L50), `validate_cog` (L110), `range_read_probe` (L147), `to_cog` (L178), `ensure_cog` (L212).
  - `windowed.py`: `AlgorithmProfile` (L43), `WindowResult` (L54), `execute_windowed` (L66) — windowed raster compute with budgets; `overview_statistics` (L271).
  - `chunk.py`: `RasterChunkDescriptor` (L78), `build_chunk_descriptor` (L227), `iter_chunk_descriptors` (L296), `chunk_digest` (L332).
  - `zarr.py`: **probe-gated zarr** (zarr is NOT a repo dependency; honest typed `ZarrUnavailable` degrade, `raster_runtime_capabilities()` reports `zarr: False`). `write_zarr_cube` (L321) — "temporal stack materialization: one zarr array `(time, y, x)` with per-time chunk descriptors, time axis in attrs".
  - `remote.py`: `RemoteReadPolicy`/`RemoteReadSession` (L63/L77), `remote_read_window` (L104), `RemoteReadBudgetExceeded` (L39).
- **app/services/raster_store.py**: session PNG store; `ref:raster/<id>` opaque cursor convention (L65, L77-79).
- **app/services/rs/**: `band_math.py` (`compute_index_array` L140, DN→reflectance L127), `spectral_engine.py` (`SpectralRasterEngine` L25), `stac_client.py` (`StacClientPrimitive` L134, masked decimated reads L77).

## 4. Lakehouse — the existing labeled RS cube (closest prior art)

- `app/services/lakehouse/rs_cube.py` (ADR-0119 Scope B): **aligned optical/SAR/mask labeled cube** production. `RS_ROLES = ("optical","sar","cloud_mask","quality_mask")` (L40); variables `reflectance(time,band,y,x)`, `sigma0(time,polarization,y,x)`, masks `(time,y,x)` (L46-51); grid-identity discipline: all sources must pass header-projection equality, mismatch = typed rejection, **"绝不静默重采样（对齐是上游职责）"**; full-time-axis enforcement; `RS_MAX_SOURCES=512` (L43), `RS_MAX_CELLS=64_000_000` (L45); `RSCubeError` typed (L50); input fingerprint (L407). Writes go through `write_labeled_cube` as the single writer.
- `app/services/lakehouse/cube_schema.py`: `validate_grid_coords` (L143), `check_crs` (L156), `validate_labeled_schema` (L186), `grid_coords_equal` (L386), `require_aligned` (L442). Label axes whitelist includes `time, band, polarization, vertical, model, scenario` (`labeled_selection.py` L41).
- `app/services/lakehouse/labeled_selection.py`: label/bbox → bounded index+chunk plan; `DEFAULT_MAX_CELLS = 8_000_000` (L37); unknown labels typed-rejected; empty result typed-rejected.
- `app/services/lakehouse/cube_service.py`: `_resolve_time_source` (L44), `_cube_input_fingerprint` (L234), `_read_window_bounded` (L295).
- `app/services/lakehouse/data_object.py`: `DataObject` — content-addressed manifest (id = sha256 of canonical manifest), kinds `vector_parquet | cog_raster | zarr_cube` (see UBIQUITOUS_LANGUAGE.md "Lakehouse V6" section).
- `catalog_stac.py` exposes a STAC catalog view.

## 5. app/lib/gis — capability/algorithm declaration & composition

- **capability_registry.py**: `CapabilityDescriptor` (L25) fields incl. `input_artifact_types/output_artifact_types/geometry_requirements/domain/category/purpose_template/status("native"|"planned"|"unavailable")/offline_capable/incompatible_with` (ADR-0181); dynamic induced-capability hook (`DYNAMIC_CAPABILITY_PREFIX="induced."`, L27-34). `CapabilityRegistry` (L74) — O(1) by id, no silent duplicates. Seeds loaded from domain packs via `app/lib/gis/capabilities/__init__.py::iter_capability_packs` (L21) — packs: data_access, geometry, aggregation, density, statistics, interpolation, network, terrain, **raster**, **temporal**, decision, platform, sampling, ecology, modelops.
- **capabilities/raster.py**: `CAPABILITIES: List[CapabilityDescriptor]` — `raster_source`, `terrain_slope/aspect/hillshade`, `ndvi`, `band_math`, … ("新能力在各自域模块注册，勿回填中央文件").
- **algorithm_registry.py**: `AlgorithmDescriptor` (L242) — id, `backend_variants` (`BackendVariant` L201 with min/max_features), `capabilities`, `input_artifact_types`, `output_artifact_type`, `tool_candidates`, cost trio, `preferred_execution_policy`, `algorithm_family`, `method_references`, `assumptions`, `limitations`, `crs_class`, `scientific_status`, `conformance_tests` (test-nodeid strings!). Registry class L378, `get_algorithm_registry` (L812). Domain packs under `app/lib/gis/algorithms/` (remote_sensing.py, temporal.py, raster.py, …), aggregated by `_load_seed_algorithms` (L369).
- **algorithms/remote_sensing.py**: descriptors `remote.ndvi` (L37) etc., with science metadata and `conformance_tests` pointing at `tests/unit/test_spectral_engine.py::...` and `tests/unit/lib/test_spectral_science_vnext.py::...`. Implementations live in `app/lib/geo_analysis/{spectral,raster_change,sar_temporal,sar_filter,sar_calibration,glcm,raster_pca,tasseled_cap}.py`; tool layer = "validate → 调实现 → 挂证据块" (module docstring L1-22).
- **algorithms/temporal.py**: `temporal.profile/.aggregate/.trend/.changepoint(CUSUM)/.seasonal_decompose/.change/.hotspot/.emerging_hotspot/.raster_ts/.cube_stats/.phenology/.anomaly/.smooth_gapfill` (L32-412) + named analysis recipes (L467+).
- **algorithm_resolver.py**: `AlgorithmResolution` (L39), `AlgorithmResolver` (L116), singleton `get_algorithm_resolver` (L485). Resolves capability → algorithm → tool with backend evidence.
- **artifacts.py**: `ArtifactTypeDescriptor` (L24); seed types include `raster_surface` (L118), `remote_sensing_index` (L129); `ArtifactTypeRegistry` (L158), `get_artifact_type_registry` (L200); `ArtifactDescriptor` (L232), `artifact_from_profile` (L282).
- **analysis_patterns.py**: pattern vocabulary (change_detection etc.) used by planner/pattern projection.
- **scientific_errors.py**: `DegenerateData`, `ResourceScaleMismatch` (ValueError subclasses with `estimated/limit/correction_hint`) — the repo's typed-refusal idiom.
- **scientific_evidence.py**: `Diagnostic`, `build_evidence` — evidence block attached by tools.
- **parameter_contracts.py**: `ParameterContract`/`ParameterSpec` — per-algorithm parameter validation.

## 6. Science layer (app/lib/geo_analysis) — existing implementations

(All exist at baseline; see GAP_ANALYSIS for reuse vs gaps.)

- `temporal_cube.py` (191 L): `TemporalCube` dataclass (L36: `stack (T,H,W)` float NaN-invalid, `times_sec`, `nodata`, `quality (T,H,W)∈[0,1]`, `label`, `disclosures`); `valid_mask()` (L56), `gap_lengths()` (L63, per-pixel longest invalid run), `slice_spacing_report()` (L76, missing-slice detection 1.5×median), `climatology()` (L92, nan-aware mean/std/n_valid); `build_cube()` (L103, all validation: shape/time axis/sortedness/CUBE_MAX_SLICES=512 L27/CUBE_MAX_ELEMENTS=8M L32/quality range); `from_sar_stack` (L168, unit-consistency disclosure), `from_optical_stack` (L179, cloud_mask → quality).
- `phenology.py`: `phenology_features` (L114, double-harmonic fit → SOS/EOS/peak proxies), `temporal_anomaly` (L245), array variants (L315/L333), bounded gap-fill `_fill_short_gaps` (L36, counted).
- `sar_temporal.py`: `SARAcquisitionMeta` (L44, pydantic: orbit/incidence/polarization/mode...), `temporal_stack_statistics` (L148, percentiles), `temporal_composite` (L265), `medoid_composite` (L344), `vh_ratio` (L434), `temporal_log_ratio_change` (L467), `acquisition_comparability` (L485), `stack_comparability_warnings` (L504).
- `sar_filter.py`: `speckle_filter` (L444: lee/refined-lee/frost/kuan/gamma-map), `gamma_map_filter` (L554), `kuan_filter` (L568); ENL resolution `_resolve_enl` (L101).
- `sar_calibration.py`: `calibrate_sar` (L91, σ⁰/γ⁰ + incidence plane), `remove_thermal_noise` (L287), `sar_log_scale` (L402), `calibration_evidence_facts` (L236).
- `sar_v3.py`: `multitemporal_speckle` (L197), `coherence_estimate` (L384), `radiometric_terrain_correction` (L490), **`layover_shadow_mask` (L577)**, `enl_map` (L685)/`enl_confidence_interval` (L656).
- `rs_v3.py`: `mnf/ica/spectral_angle_mapper/sid/matched_filter/rx_anomaly/mad_change/segment_image/extract_endmembers_vca/fcls_unmix`; **`temporal_features` (L1482: min/max/mean/std/amplitude/first_last_diff/single-harmonic amp+phase)**; `robust_normalize` (L1587); **`cloud_qc_basic` (L1701)**.
- `raster_change.py`: `detect_raster_change` (L65), `change_vector_analysis` (L293), `ratio_change` (L390), `threshold_change` (L472).
- `spectral.py`: `BandRole` (L43), `SpectralIndexSpec` (L109), `validate_band_map` (L258), `compute_spectral_index` (L290) — role-mapped (B04/B08 style), safe-division NaN semantics.
- `tasseled_cap.py`: `resolve_tasseled_roles` (L75), `tasseled_cap` (L95).
- `cv.py`: **`spatial_block_folds` (L49)**, **`temporal_forward_folds` (L76)**, `CVReport` (L132), `run_cross_validation` (L175).
- `spatial_sampling.py`: `random_points_in_polygons` (L175), `systematic_grid_points` (L236), `stratified_points_in_polygons` (L338) — point sampling only.
- `raster_windowed.py`, `raster_math.py`, `raster_mosaic.py`, `raster_pca.py`, `glcm.py`, `evidence.py` (`build_quality_evidence` L20).
- `app/lib/modelops/temporal.py`: `TemporalStackSpec` (L24; ISO times, `missing_policy`, `quality_masks`, `modalities`), `SAR_POLARIZATIONS = {"VV","VH","HH","HV"}` (L20), `output_time_semantics` ("last|mean|sequence").
- `app/lib/modelops/preprocess.py`: `PreprocessPlan` (L21), `build_plan` (L45), `preprocess_window` (L87), `preprocess_batch` (L165) — window/chip preprocessing with normalization + masked mean.
- `app/lib/modelops/evaluation.py`: **`spatial_blocked_split` (L265; pixel-grid block → fold, deterministic hash, anti-leakage)**, `BlockedSplit`-type structure L244.

## 7. Tool layer (LLM surface)

- `app/tools/remote_sensing.py`: `register_rs_tools` (L111) — spectral index tools; `_bands_to_arrays` (L23) with `_TOOL_ARRAY_MAX_VALUES = 4_000_000` inline cap and typed `ResourceScaleMismatch` redirect to "栅格工件路径（raster_calculator/窗口化底座）" (L38-45); `_attach_science_evidence` (L52, descriptor evidence block via `app/lib/gis/scientific_evidence.py`).
- `app/tools/science_temporal_tools.py` (science-v5 W8): `register_science_temporal_tools` — tools `temporal_cube_stats` (L50), `phenology_features` (L103), `temporal_anomaly` (L154), `ts_smooth_gapfill` (L200). Docstring records the honesty contract: inline (T,H,W) JSON arrays; outputs are bounded summaries (quantiles), "完整特征面建议走 artifact/ref 通道——不搬运完整栅格".
- `app/tools/temporal_tools.py`: profile/trend/change-point (CUSUM)/hotspot tools.
- `app/tools/raster_tools_cog.py`, `app/tools/advanced_spatial.py`.
- Registration: `app/tools/__init__.py` `_TOOL_MODULES` list — `("app.tools.remote_sensing", "register_rs_tools")` at L17; **PR #1336 also touches this file (adds geoai_tools)** → avoid editing; extend an existing module's `register_*` instead.

## 8. Harness: SkillPolicy / CapabilityGraph / recipes / skills / MapProduct

- **SkillPolicy**: `app/services/gis_harness/skills/policy.py` — `SkillPolicyDecision` (L66, bounded `to_bounded_dict`/`pi_context_card`), `SkillPolicy` (L167; `trusted_resolver` + optional `shadow_resolver` + `quarantine_ids`; `resolve(facts)` deterministic). Kill-switch env `GIS_SKILL_POLICY` (L59, `policy_enabled()` L158: `0/false/off/no` → mode none). Trust tiers core/candidate/experimental/quarantined/deprecated (L47); `TRUSTED_PACKS=("core",)` (L53). Tools: `app/tools/skill_library_tools.py` (`SkillPolicyArgs` L59).
- **Skill library**: `app/services/gis_harness/skills/library/core/*.yaml` — declarative GIS Skills (ADR-0182): `remote_sensing.yaml` (`landcover_result_mapping`, ...), `temporal.yaml` (`temporal_comparison_workflow` with `baseline`/`target_time` roles, capability_requirements with `criticality`/`hard_gate`, procedure steps with `evidence_requirements`, fallbacks), `raster_terrain.yaml`, `data_preparation.yaml`. Loader `skills/loader.py`; selection `skills/resolver.py`; composition `skills/composition.py`; procedure IR `skills/procedure_ir.py`.
- **CapabilityGraph**: `app/services/gis_harness/capability_graph.py` — `CapabilityGraph` (L155, immutable projection; `node/has/nodes_by_kind/neighbors`, `models_for_capability` L214); built from CapabilityRegistry + AlgorithmRegistry + modelops descriptors. **Touched by PR #1336 — treat as frozen; new capabilities flow in via the domain packs, not by editing the graph.**
- **Recipes**: `app/services/gis_harness/recipes.py` (`CartographyRecipe`, `RecipeFallback`) + `recipe_packs/*.py` (`_kit.py` helpers `role/obl/fb/wf/standard_completion/auto_fallback`). Existing packs directly relevant: `remote_sensing.py` (`ndvi_vegetation_monitor` L44+, band-semantics obligation `RS_BAND_SEMANTICS_REQUIRED`), `sar.py` (`sar_backscatter_overview`; **red line: calibration/speckle capabilities were declared planned-honest at pack creation; ADR-0099 Foundation V2 flipped `sar.speckle_filter`/`sar.radiometric_calibration` to native** — pack text at L3-9 still documents the calibration-evidence obligation `SAR_CALIBRATION_EVIDENCE_REQUIRED` L28), `change_detection.py`, `temporal.py` (`temporal_profile_station` — vector time series, chart components `MAP_COMPONENTS_CHART`).
- **MapProduct**: `app/models/project.py` `MapProductVersion` (L452, table `map_products`): `product_fingerprint`, `input_dataset_fingerprints`, `compute_plan` (bounded step/capability/algorithm/tool + args JSON), `output_fingerprints`, `artifact_ids` (JSON), `diff_summary` `{data/algorithm/parameter/style/output_changed, vs_version_no}`, `mapspec_snapshot`, `lineage_kind`. Service: `app/services/map_product_service.py` `MapProductService` (L87), `compute_product_fingerprint` (L70). Charts/tables attach as artifacts (`chart_spec`, `stats_table` artifact types) referenced from `artifact_ids` + recipe `export_profile={"chart": True}`.
- **Mission/hot-path** (PR #1335 territory — do not touch): `app/services/gis_harness/hotpath_convergence/`, `evidence_claim/`, `completion/`, `app/services/mission_runtime/`.
- **harness_kernel**: `app/services/harness_kernel/{runtime,models,projection,legacy_adapter,metrics}.py`.

## 9. Existing bitemporal / change-detection surfaces

- `app/lib/geo_analysis/raster_change.py` (pixel CVA/ratio/threshold, bitemporal), `rs_v3.py::mad_change` (L869, MAD/multivariate image differencing), `sar_temporal.py::temporal_log_ratio_change` (L467).
- Harness capability vocabulary: `change_detection` task type + `change_set` artifact type; recipe pack `recipe_packs/change_detection.py`; GIS skill `temporal_comparison_workflow` (two-epoch); `analysis_patterns.py` references change_detection patterns; capability pack `capabilities/temporal.py`.

## 10. Test conventions

- Layout: `tests/unit/lib/` = science modules (`test_temporal_cube_v5.py`, `test_phenology_v5.py`, `test_sar_filters_v2.py`, `test_sar_v3.py`, `test_sar_calibration_v2.py`, `test_sar_science_fixes_v3.py`, `test_medoid_composite.py`, `test_rs_v3.py`, `test_spectral_science_vnext.py`, `test_change_detection_science.py`, `test_spatial_sampling_v6.py`, `test_science_temporal_tools.py`); `tests/data/` = lakehouse/fabric (`test_lakehouse_rs_cube_v7.py`, `test_lakehouse_cube_schema_v7.py`, `test_data_fabric_ads_contracts.py`); `tests/unit/gis_harness/` = harness (incl. `test_capability_graph_v8.py`, `test_skill_policy_v1.py`); `tests/unit/modelops/`; `tests/science_oracles/data/{science_v5,sar}.json` golden oracles; `tests/benchmarks/` perf.
- Fixture style (from `test_temporal_cube_v5.py`): pure-numpy synthetic arrays inline (`np.ones((10,3,3))`, epoch-seconds via `np.arange(10.0)*86400.0`), class-grouped tests, Chinese docstrings, no rasterio required for cube-level tests. Lakehouse cube tests in `tests/data/` build zarr stores with the probe-gated writer (skip when zarr missing — honest self-skip).
- conformance hooks: `AlgorithmDescriptor.conformance_tests` lists pytest node-ids — new algorithms should reference their tests there (`algorithms/remote_sensing.py` L60-64 precedent).
- Invocation: targeted `pytest tests/unit/lib/test_x.py -o addopts=` (or `--no-cov -q`); lane runs `pytest -m cartography`, `pytest -m heavy`, `pytest -m perf`; contract lanes as in `.github/workflows/contract.yml`.
