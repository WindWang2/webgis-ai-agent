# Deepen SpatialAnalyzer as Unified Spatial & Raster Analysis Domain Engine

**Status:** accepted

We deepen `SpatialAnalyzer` (`app/services/spatial_analyzer.py`) by absorbing vector topological operations (`spatial_join`), raster analytical operations (`zonal_stats`, `raster_reclassify`, `raster_calculator`, `raster_resample`), and network operations (`isochrone_network`).

## Context

Previously, several spatial tools in `app/tools/advanced_spatial.py` implemented inline raster calculation logic (such as opening `rasterio.Env`, handling GDAL VFS sandbox settings, and parsing paths) directly within tool definitions. Furthermore, `advanced_spatial.py:239` attempted to call `SpatialAnalyzer.spatial_join(...)`, but the method was missing on `SpatialAnalyzer`, creating an implicit `AttributeError` risk.

## Decision

1. **Complete Operator Interface**: Implement `spatial_join`, `zonal_stats`, `raster_reclassify`, `raster_calculator`, `raster_resample`, and `isochrone_network` as concrete classmethods on `SpatialAnalyzer` (`app/services/spatial_analyzer.py`).
2. **Encapsulated Path Security & VFS Sandbox**: `SpatialAnalyzer` encapsulates `validate_data_path` to prevent path traversal / VFS abuse and executes GDAL operations within `rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="TRUE", GDAL_HTTP_TIMEOUT=5, GDAL_HTTP_MAX_RETRY=0)`. All methods return standardized `GeoAnalysisResult` objects.
3. **Thin Tool Wrappers**: Tool wrappers in `app/tools/advanced_spatial.py` are refactored to thin delegates that parse inputs and call `SpatialAnalyzer.<operator>(...).to_llm_response()`.

## Consequences

- **Robustness**: Fixes the missing `SpatialAnalyzer.spatial_join` operator bug and guarantees all spatial calculations go through security path validation.
- **Locality & Testability**: Vector and raster spatial logic is centralized in `SpatialAnalyzer` and can be unit-tested without instantiating LLM tool registries.
