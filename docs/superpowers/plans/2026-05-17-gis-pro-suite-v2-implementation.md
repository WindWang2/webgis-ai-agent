# GIS Pro Suite v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement professional Raster-Vector synergy and interactive UI intelligence modules.

**Architecture:** 
- **Backend:** Add `rasterio` and `rasterstats`. Create `interpolation.py` and `raster_ops.py` in `geo_analysis`.
- **Frontend:** Add `queryFeaturesAt`, `setLayerFilter`, and `measure` to the `map-kit` toolkit.

**Tech Stack:** Python, rasterio, rasterstats, GeoPandas, MapLibre GL JS, Turf.js.

---

### Task 1: Backend Raster-Vector Synergy

**Files:**
- Create: `app/lib/geo_analysis/interpolation.py`
- Create: `app/lib/geo_analysis/raster_ops.py`
- Modify: `requirements.txt`
- Test: `tests/unit/lib/test_geo_analysis_pro.py`

- [ ] **Step 1: Add raster dependencies**
Add `rasterio>=1.3.10` and `rasterstats>=0.19.0` to `requirements.txt`.
- [ ] **Step 2: Implement Zonal Statistics**
Create `app/lib/geo_analysis/raster_ops.py`. Implement `zonal_statistics(polygons_geojson, raster_path, stats)`. Ensure it handles CRS reprojection using `geo_processor.core`.
- [ ] **Step 3: Implement IDW Interpolation**
Create `app/lib/geo_analysis/interpolation.py`. Implement `idw_interpolation(points_geojson, value_field, resolution=8)`. Output should be an H3 hexagonal grid.
- [ ] **Step 4: Write tests**
Verify zonal stats on a mock raster and IDW on sample points.
- [ ] **Step 5: Commit**
`git add requirements.txt app/lib/geo_analysis/ tests/unit/lib/test_geo_analysis_pro.py && git commit -m "feat: implement backend raster-vector synergy modules"`

### Task 2: Frontend UI Data Intelligence

**Files:**
- Modify: `frontend/lib/map-kit/state.ts`
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/navigation.ts`
- Test: `frontend/lib/map-kit/intelligence.test.ts`

- [ ] **Step 1: Implement Feature Probing**
In `map-kit/state.ts`, implement `queryFeaturesAt(map, point, layers?)`. Wrap `map.queryRenderedFeatures` to return a standardized GeoJSON Feature.
- [ ] **Step 2: Implement Client-Side Filtering**
In `map-kit/renderer.ts`, implement `setLayerFilter(map, layerId, filterExp)`. Support MapLibre filter expressions.
- [ ] **Step 3: Implement Measurement**
In `map-kit/navigation.ts`, implement `measure(map, coordinates, type='distance')`. Use geodesic math to return meters/square-meters.
- [ ] **Step 4: Write tests**
Verify feature querying and filter expression generation.
- [ ] **Step 5: Commit**
`git add frontend/lib/map-kit/ && git commit -m "feat(map-kit): implement UI data intelligence modules"`

### Task 3: System Integration & Prompt Tuning

**Files:**
- Modify: `app/services/spatial_analyzer.py`
- Modify: `app/tools/advanced_spatial.py`
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Register Pro tools**
Expose `zonal_statistics` and `idw_interpolation` in `app/tools/advanced_spatial.py`.
- [ ] **Step 2: Refine SYSTEM_PROMPT**
Add instructions for "Map Probing" (querying features) and "Contextual Raster Analysis". Explicitly mention that filtering is now an instant client-side operation.
- [ ] **Step 3: End-to-End Smoke Test**
Verify that the Agent can query a point on the map and then filter the layer based on that point's attributes.
- [ ] **Step 4: Commit**
`git add app/ && git commit -m "feat: complete GIS Pro Suite v2 integration and prompt tuning"`
