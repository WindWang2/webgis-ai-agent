# Robust Geometric Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement precision-aware geometric operations (`buffer_smart`, `clip_smart`, `overlay_smart`) with automatic CRS management and LLM-friendly summaries.

**Architecture:** Use `geopandas` for core GIS logic and `GeoAnalysisResult` for structured output. Handle UTM projection automatically for distance-based operations on geographic coordinates.

**Tech Stack:** Python, Geopandas, Shapely, Pytest.

---

### Task 1: Setup and `buffer_smart` Foundation

**Files:**
- Create: `app/lib/geoprocessing/geometry.py`
- Test: `tests/unit/geoprocessing/test_geometry.py`

- [ ] **Step 1: Write failing test for `buffer_smart` with WGS84 and metric units**

```python
import pytest
from app.lib.geoprocessing.geometry import buffer_smart
from app.lib.geoprocessing.interface import GeoAnalysisResult
import json

def test_buffer_smart_wgs84_metric():
    # A point near London
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 51.5]},
            "properties": {"name": "London"}
        }]
    }
    # Buffer by 1000m
    result = buffer_smart(geojson, distance=1000, unit='m')
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "Buffered 1 features by 1000m" in result.summary
    assert "UTM" in result.summary
    # Verify geometry is now a Polygon
    data = result.data
    assert data["features"][0]["geometry"]["type"] == "Polygon"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_geometry.py -v`
Expected: FAIL (ModuleNotFoundError or ImportError)

- [ ] **Step 3: Implement `buffer_smart` with UTM auto-detection**

```python
import geopandas as gpd
import json
from typing import Any, Union
from app.lib.geoprocessing.interface import GeoAnalysisResult

def buffer_smart(geojson: Union[dict, str], distance: float, unit: str = 'm') -> GeoAnalysisResult:
    try:
        if isinstance(geojson, str):
            geojson = json.loads(geojson)
        
        gdf = gpd.GeoDataFrame.from_features(geojson)
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        
        original_crs = gdf.crs
        summary_suffix = ""
        
        # Handle unit conversion if necessary (simplified for this task)
        # In a real app, we'd have a more robust unit conversion utility
        dist = distance
        if unit == 'km':
            dist = distance * 1000
            unit = 'm'
        
        if gdf.crs.is_geographic and unit in ['m', 'km']:
            utm_crs = gdf.estimate_utm_crs()
            gdf = gdf.to_crs(utm_crs)
            gdf['geometry'] = gdf.buffer(dist)
            gdf = gdf.to_crs(original_crs)
            summary_suffix = f" using {utm_crs.to_string()} projection"
        else:
            gdf['geometry'] = gdf.buffer(dist)
            
        summary = f"Buffered {len(gdf)} features by {distance}{unit}{summary_suffix}."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Buffer operation failed: {str(e)}",
            error_type=type(e).__name__
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_geometry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/geoprocessing/geometry.py tests/unit/geoprocessing/test_geometry.py
git commit -m "feat: implement buffer_smart with UTM auto-detection"
```

---

### Task 2: Implement `clip_smart`

**Files:**
- Modify: `app/lib/geoprocessing/geometry.py`
- Test: `tests/unit/geoprocessing/test_geometry.py`

- [ ] **Step 1: Write failing test for `clip_smart` with CRS mismatch**

```python
def test_clip_smart_crs_mismatch():
    from app.lib.geoprocessing.geometry import clip_smart
    
    # Target in WGS84
    target = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}, "properties": {}}]
    }
    # Mask in Web Mercator
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[11500000, 3500000], [11600000, 3500000], [11600000, 3600000], [11500000, 3600000], [11500000, 3500000]]]}, "properties": {"name": "Mask"}}]
    }
    
    result = clip_smart(target, mask)
    assert result.success is True
    assert "Clipped" in result.summary
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement `clip_smart`**

```python
def clip_smart(target_layer: Union[dict, str], mask_layer: Union[dict, str]) -> GeoAnalysisResult:
    try:
        if isinstance(target_layer, str): target_layer = json.loads(target_layer)
        if isinstance(mask_layer, str): mask_layer = json.loads(mask_layer)
        
        target_gdf = gpd.GeoDataFrame.from_features(target_layer)
        mask_gdf = gpd.GeoDataFrame.from_features(mask_layer)
        
        if target_gdf.crs is None: target_gdf.set_crs("EPSG:4326", inplace=True)
        if mask_gdf.crs is None: mask_gdf.set_crs("EPSG:4326", inplace=True)
        
        input_count = len(target_gdf)
        
        if target_gdf.crs != mask_gdf.crs:
            mask_gdf = mask_gdf.to_crs(target_gdf.crs)
            
        clipped_gdf = gpd.clip(target_gdf, mask_gdf)
        remaining_count = len(clipped_gdf)
        
        summary = f"Clipped {input_count} features to the boundary of mask, {remaining_count} features remaining."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(clipped_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Clip operation failed: {str(e)}",
            error_type=type(e).__name__
        )
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

---

### Task 3: Implement `overlay_smart`

**Files:**
- Modify: `app/lib/geoprocessing/geometry.py`
- Test: `tests/unit/geoprocessing/test_geometry.py`

- [ ] **Step 1: Write failing test for `overlay_smart` (intersection)**

```python
def test_overlay_smart_intersection():
    from app.lib.geoprocessing.geometry import overlay_smart
    
    layer_a = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}, "properties": {"id": "A"}}]
    }
    layer_b = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]}, "properties": {"id": "B"}}]
    }
    
    result = overlay_smart(layer_a, layer_b, how='intersection')
    assert result.success is True
    assert "Performed intersection overlay" in result.summary
    assert len(result.data["features"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement `overlay_smart`**

```python
def overlay_smart(layer_a: Union[dict, str], layer_b: Union[dict, str], how: str = 'intersection') -> GeoAnalysisResult:
    try:
        if isinstance(layer_a, str): layer_a = json.loads(layer_a)
        if isinstance(layer_b, str): layer_b = json.loads(layer_b)
        
        gdf_a = gpd.GeoDataFrame.from_features(layer_a)
        gdf_b = gpd.GeoDataFrame.from_features(layer_b)
        
        if gdf_a.crs is None: gdf_a.set_crs("EPSG:4326", inplace=True)
        if gdf_b.crs is None: gdf_b.set_crs("EPSG:4326", inplace=True)
        
        if gdf_a.crs != gdf_b.crs:
            gdf_b = gdf_b.to_crs(gdf_a.crs)
            
        result_gdf = gpd.overlay(gdf_a, gdf_b, how=how)
        
        summary = f"Performed {how} overlay. Result contains {len(result_gdf)} features."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(result_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Overlay operation failed: {str(e)}",
            error_type=type(e).__name__
        )
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**
