# TEST MATRIX — milestone → test files → regression suites → invocation

## Repo invocation conventions (verified)

- Config: `pytest.ini` — `testpaths=tests`, `pythonpath=.`, `asyncio_mode=auto`, `timeout=60` (thread), addopts force `--cov=app`.
- Targeted/fast run (repo convention, see `review/PRODUCTION-SKILL-POLICY-REVIEW.md`):
  - `pytest tests/unit/lib/test_rs_cube_descriptor_v1.py -o addopts=` (strip coverage)
  - or `pytest <paths> --no-cov -q` (CI style, `.github/workflows/quality-e2e.yml:179`)
- Lanes: `pytest -m cartography` (release gate), `pytest -m heavy`, `pytest -m perf` (isolated; self-skips in full runs), `-m real_services` needs `REAL_SERVICES=1`.
- Full suite with coverage is the default `pytest` (slow); use `-o addopts=` locally.
- No xdist `-n` convention in CI.

## Milestone → new test files

| Milestone | New test file(s) | What to assert | Reuse/echo of existing patterns |
|---|---|---|---|
| M1 Cube descriptor (refs-only, additive contract, bounded) | `tests/unit/lib/test_rs_cube_descriptor_v1.py` | descriptor validates refs/times/roles/polarization vocab (`SAR_POLARIZATIONS`); rejects payload-bearing fields; version additive-compat (mirrors `tests/unit/test_data_fabric_ads_contracts.py` drift-guard style) | `test_temporal_cube_v5.py` inline numpy; contracts tests |
| M2 Optical/SAR acquisition alignment | `tests/unit/lib/test_rs_alignment_v1.py` | nearest-time pairing within tolerance; unmatched → GapCode `missing_acquisition`; grid/CRS mismatch → typed refusal listing grids (`require_aligned` semantics); cross-year + different-cadence synthetic cases | `cube_schema` validators; `test_lakehouse_rs_cube_v7.py` grid discipline |
| M3 Typed gap model | `tests/unit/lib/test_rs_gaps_v1.py` | gap mask from nodata/cloud/quality/layover inputs; ratio report bounded; no silent interpolation; disclosures rendered from typed report | `gap_lengths`/`slice_spacing_report` tests in `test_temporal_cube_v5.py` |
| M4 Preprocessing plan | `tests/unit/lib/test_rs_preprocess_plan_v1.py` | plan fingerprint stable; each step delegates to real impl (speckle/calibrate/cloud_qc); gap_policy=keep_nan default; refusal on unknown step | `test_sar_filters_v2.py`, `test_sar_calibration_v2.py` |
| M5 Temporal features (yearly/seasonal) | `tests/unit/lib/test_rs_temporal_features_v1.py` | per-year median/percentile composites; amplitude/slope; changepoint delegation (CUSUM); phenology proxy hand cases; bounded summary output | `test_phenology_v5.py`, `test_rs_v3.py::temporal_features`, oracle `tests/science_oracles/data/science_v5.json` |
| M6 SAR×optical joint fusion stack | `tests/unit/lib/test_rs_joint_fusion_v1.py` | VV/VH + NDVI/EVI stack shapes; per-feature validity from GapMask; late/feature-level evidence blocks attached; honest refusal when band semantics unconfirmed (RS_BAND_SEMANTICS_REQUIRED echo) | `test_spectral_science_vnext.py`, `vh_ratio` tests |
| M7 Sampling + spatial-block split | `tests/unit/lib/test_rs_sample_split_v1.py` | polygon attach → feature table ref; block folds never split a block across folds (spatial-block property test); temporal forward split option; leakage disclosure present | `test_spatial_sampling_v6.py`; `cv.spatial_block_folds` |
| M8 Disclosure/coverage card | `tests/unit/lib/test_rs_coverage_disclosure_v1.py` | aggregates gap ratio, cloud/shadow %, layover %, registration residual, source_version; bounded dict; fail-closed when evidence missing | `SkillPolicyDecision.to_bounded_dict` shape precedent |
| M9 Tools surface | `tests/unit/lib/test_rs_temporal_tools_v1.py` (science tools live in `tests/unit/lib/`) | each new tool: happy path (tiny arrays), inline-array scale cap → `ResourceScaleMismatch`, evidence block attached, ref-channel output for full rasters | `tests/unit/lib/test_science_temporal_tools.py` |
| M10 Capability/algorithm/recipe/skill registration | `tests/unit/gis_harness/test_rs_cube_capabilities_v1.py` (NEW file — do NOT extend `test_capability_graph_v8.py`, #1336-owned) | new CapabilityDescriptors valid & resolvable (`capability_registry` parity — cf. `test_capability_registry_parity.py`); AlgorithmDescriptors carry conformance_tests; recipe pack registers + obligation codes fire; skill YAML loads + resolver selects (cf. `test_skill_*_v1.py` style) | `test_capability_registry_parity.py`, `test_capability_resolution.py`, skill library tests |
| M11 MapProduct wiring | `tests/unit/test_rs_map_product_v1.py` (or fold into M10 file if small) | recipe_id + chart/table artifact refs land in MapProductVersion artifact set; `compute_plan`/`diff_summary` populated; no schema migration needed | map product service tests (search `tests/unit` for `map_product`) |
| M12 Storage round-trip (optional, zarr) | `tests/data/test_rs_cube_zarr_roundtrip_v1.py` | descriptor → aligned labeled cube (roles incl. polarization) → labeled selection read → features; self-skip when `zarr_available()` False | `tests/data/test_lakehouse_rs_cube_v7.py` |

## Synthetic scenario matrix (required by goal)

Cover inside M2/M5/M6/M7 fixtures:
- **cross-year**: 2 years × 12 monthly slices, known seasonal signal (sin), assert seasonal composite + amplitude.
- **missing acquisitions**: drop 3 of 24 slices → `missing_acquisition` gaps; yearly median still computed with `n_valid` disclosure.
- **misalignment**: 1 SAR slice with shifted transform → typed refusal (never silent resample).
- **different cadence**: optical monthly vs SAR 12-day → pairing within tolerance, unmatched windows disclosed.

## Existing regression suites to re-run after implementation

```bash
# science layer (fast)
pytest tests/unit/lib/test_temporal_cube_v5.py tests/unit/lib/test_phenology_v5.py \
       tests/unit/lib/test_sar_filters_v2.py tests/unit/lib/test_sar_v3.py \
       tests/unit/lib/test_sar_calibration_v2.py tests/unit/lib/test_sar_science_fixes_v3.py \
       tests/unit/lib/test_medoid_composite.py tests/unit/lib/test_rs_v3.py \
       tests/unit/lib/test_spectral_science_vnext.py tests/unit/lib/test_change_detection_science.py \
       tests/unit/lib/test_spatial_sampling_v6.py tests/unit/lib/test_science_temporal_tools.py \
       tests/unit/test_spectral_engine.py -o addopts=

# cube/lakehouse (self-skips without zarr)
pytest tests/data/test_lakehouse_rs_cube_v7.py tests/data/test_lakehouse_cube_schema_v7.py -o addopts=

# registry parity + resolver (capability/algorithm additions)
pytest tests/unit/test_capability_registry_parity.py tests/unit/gis_harness/test_capability_resolution.py -o addopts=

# harness surfaces touched indirectly (recipes/skills/tools)
pytest tests/unit/gis_harness -k "skill or recipe or capability" -o addopts=

# release gate lane (after wiring recipes/products)
pytest -m cartography
```

Evidence blocks in final PR should quote `pytest ... -o addopts= → N passed` lines (review-file convention, `review/*.md`).
