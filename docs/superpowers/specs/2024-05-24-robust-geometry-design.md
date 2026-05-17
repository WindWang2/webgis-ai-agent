# Design Spec: Robust Geometric Operations (Precision Focused)

**Date:** 2024-05-24
**Topic:** Task 2 - Robust Geometric Operations
**Status:** Approved (Assumed for autonomous execution)

## 1. Goal
Implement a suite of precision-aware geometric operations (`buffer_smart`, `clip_smart`, `overlay_smart`) that handle CRS management and provide descriptive summaries for LLM consumption.

## 2. Architecture
The operations will reside in `app/lib/geoprocessing/geometry.py` and utilize `geopandas` for heavy lifting and `app.lib.geoprocessing.interface.GeoAnalysisResult` for output.

### 2.1 Component: `buffer_smart(geojson, distance, unit)`
- **Logic:**
  - Convert input to `GeoDataFrame`.
  - Default CRS: EPSG:4326 if not provided.
  - If CRS is geographic and unit is metric ('m', 'km'):
    - Use `gdf.estimate_utm_crs()` to find local UTM.
    - Reproject -> Buffer -> Reproject back.
  - Returns `GeoAnalysisResult`.

### 2.2 Component: `clip_smart(target_layer, mask_layer)`
- **Logic:**
  - Align CRSs (project mask to target).
  - Perform `geopandas.clip`.
  - Returns `GeoAnalysisResult` with feature counts.

### 2.3 Component: `overlay_smart(layer_a, layer_b, how)`
- **Logic:**
  - Align CRSs (project B to A).
  - Perform `geopandas.overlay`.
  - Returns `GeoAnalysisResult` with descriptive summary.

## 3. Data Flow
1. LLM/Agent calls `smart_*` function.
2. Function handles CRS logic and validation.
3. Function executes geometric operation.
4. Function constructs `GeoAnalysisResult` with metadata summary.
5. Result is returned to caller.

## 4. Error Handling
- Invalid GeoJSON: Return `GeoAnalysisResult(success=False, ...)` with `error_type="InvalidGeoJSON"`.
- CRS Mismatch (unfixable): Return `GeoAnalysisResult(success=False, ...)` with `error_type="CRSError"`.

## 5. Testing Strategy
- **Unit Tests:** `tests/unit/geoprocessing/test_geometry.py`
  - Test `buffer_smart` with WGS84 and metric units (verify UTM projection).
  - Test `clip_smart` with different CRSs.
  - Test `overlay_smart` with all supported `how` parameters.
