# 37. Spatial Tool Consolidation Cleanup

Date: 2026-08-02

## Status

Accepted

## Context

An architecture review identified shallow-module and duplication smells across the spatial tool layer. Critically, the review's original framing — "build a `SpatialExecutionEngine` dispatch facade" — **contradicts ADR-0013**, which already removed a dynamic `execute()`/`OPERATOR_MAP` name-dispatch seam as dead code (zero callers, swapped parameter orders). The de-facto execution engine already exists: `SpatialAnalyzer` (568 LOC, 25+ classmethods) delegates to the `geo_analysis/` package (~2000 LOC of real algorithmic depth), and the 32 thin tool facades across `advanced_spatial.py`/`spatial.py`/`spatial_stats.py` are *intentionally* shallow — each needs its own LLM-facing schema (description / Pydantic model / tier / domains), and the registry's one-tool-one-function design depends on that boundary.

So this decision is **consolidation/cleanup, not a new engine**. Five concrete wins surfaced under the rejected framing.

## Decisions

1. **Delete dead `_run_raster_tool`** (`advanced_spatial.py`). Grep-confirmed zero callers — its docstring claimed to "eliminate duplicated try/except skeleton across raster tools" but the 4 raster tools delegate to `SpatialAnalyzer.raster_*` instead. Also dropped the now-unused `GeoAnalysisResult` re-import.

2. **Extract the 4×-duplicated `rasterio.Env(...)` block** into a single `rasterio_env()` context manager in `geo_analysis/raster_math.py`. The 4 `SpatialAnalyzer` raster methods (`zonal_stats`, `raster_reclassify`, `raster_calculator`, `raster_resample`) had identical `GDAL_DISABLE_READDIR_ON_OPEN`/`GDAL_HTTP_TIMEOUT`/`GDAL_HTTP_MAX_RETRY` blocks inlined; now they use `with rasterio_env():`. Single source of truth for GDAL tuning.

3. **Relocate `_generate_heatmap`** (~125 LOC of matplotlib/scipy density-rendering logic) from the tool-adapter layer (`app/tools/spatial.py`) into its canonical home `app/lib/geo_analysis/density.py`, renamed `generate_heatmap_raster`. Same precedent as the `kde_surface`/`kde_contours` extraction documented in `density.py`'s header (architecture-review F2). Heavy imports stay lazy to avoid cycles.

4. **Extract `resolve_palette_colors()`** into `app/lib/cartography/palettes.py` (the module owning `COLOR_PALETTES`). Both legend-building sites — `heatmap_data`'s `_build_legend_spec` (continuous legend) and `h3_binning`'s legend block (graduated legend) — now call it instead of re-deriving "palette name → `COLOR_PALETTES` lookup → list" inline.

5. **Unify `to_feature_collection`** by relocating it from `spatial_analyzer.py` into `geo_processor/core.py` (alongside `safe_parse`), renaming from `_to_feature_collection` (private) to `to_feature_collection` (public). Its string-parsing branch now delegates to `safe_parse`, gaining truncated-JSON repair for free (previously it silently returned an empty FC on a truncated string — a latent inconsistency with `safe_parse`). A `_to_feature_collection = to_feature_collection` alias is kept in `spatial_analyzer.py` for backward compatibility with the existing test import.

Additionally, a pre-existing import bug was fixed as a prerequisite: `density.py` imported a removed `_extract_numeric_values` from `statistics.py` (the symbol had been inlined into `_filter_numeric_gdf` but the import was never updated), so `density.py` failed to import entirely — blocking Win 3. Added a thin `_extract_numeric_values` adapter in `density.py`.

## Consequences

- **Locality**: GDAL env tuning lives in `raster_math.py`; density algorithms (including heatmap raster) live in `density.py`; palette resolution lives in `palettes.py`; GeoJSON parse/normalize lives in `geo_processor/core.py`. Each concern now has one home.
- **No new abstraction**: consistent with ADR-0013, no dispatch seam or wrapper class was introduced. The 32 tool facades remain intentionally shallow.
- **Behavior preservation**: all changes are relocations/extractions verified by the existing spatial test suite (34 tests) plus new focused tests (`test_raster_env`, `test_palette_resolution`, `test_geo_processor_core`).
- **Latent bug fixed**: `to_feature_collection` now repairs truncated GeoJSON strings via `safe_parse` (was silently dropping them); `density.py` now imports cleanly (was broken).
- **Out of scope / pre-existing**: `tests/unit/test_raster_tools.py` has a pre-existing collection error (imports a removed symbol in its own fixtures) unrelated to this refactor; not fixed here.
