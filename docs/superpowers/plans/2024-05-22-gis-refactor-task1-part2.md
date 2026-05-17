# GIS Library Refactoring Task 1 (Part 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Task 1 of the GIS library refactoring by implementing smart geometry and overlay operations.

**Architecture:** Extend `app/lib/geo_processor` with `geometry.py` and `overlay.py`. Use the core UTM conversion logic to ensure metric precision for geometric operations like buffering. Ensure CRS consistency for overlay operations.

**Tech Stack:** Python, GeoPandas, Shapely, Pytest.

---

### Task 2: Implement Smart Geometry Operations

**Files:**
- Create: `app/lib/geo_processor/geometry.py`
- Test: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write failing tests for geometry operations**

```python
def test_buffer_smart():
    from app.lib.geo_processor.geometry import buffer_smart
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    # 100 meters buffer
    buffered = buffer_smart(geojson, distance=100)
    assert buffered["type"] == "FeatureCollection"
    # Check if area is roughly pi * 100^2
    import geopandas as gpd
    from shapely.geometry import shape
    # Convert back to UTM to check area
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(buffered)
    assert abs(gdf.area.iloc[0] - 3.14159 * 100**2) < 500 # Allowing some tolerance

def test_clip_smart():
    from app.lib.geo_processor.geometry import clip_smart
    target = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [10,0], [10,10], [0,10], [0,0]]]}, "properties": {}}]
    }
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[5,5], [15,5], [15,15], [5,15], [5,5]]]}, "properties": {}}]
    }
    clipped = clip_smart(target, mask)
    assert clipped["type"] == "FeatureCollection"
    # Result should be a 5x5 square
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(clipped)
    assert len(gdf) > 0

def test_dissolve_smart():
    from app.lib.geo_processor.geometry import dissolve_smart
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [1,0], [1,1], [0,1], [0,0]]]}, "properties": {"group": 1}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1,0], [2,0], [2,1], [1,1], [1,0]]]}, "properties": {"group": 1}}
        ]
    }
    dissolved = dissolve_smart(geojson, field="group")
    assert len(dissolved["features"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`
Expected: FAIL (ModuleNotFoundError for `app.lib.geo_processor.geometry`)

- [ ] **Step 3: Implement `app/lib/geo_processor/geometry.py`**

```python
import json
import geopandas as gpd
from app.lib.geo_processor.core import to_utm_gdf, safe_parse

def buffer_smart(geojson: dict | str, distance: float, unit: str = "meters") -> dict:
    \"\"\"Buffer GeoJSON with metric precision using UTM projection.\"\"\"
    gdf, utm_crs = to_utm_gdf(geojson)
    if gdf is None:
        return {"type": "FeatureCollection", "features": []}
    
    # Distance is assumed to be in meters as per to_utm_gdf
    buffered_gdf = gdf.copy()
    buffered_gdf[\"geometry\"] = gdf.buffer(distance)
    
    # Convert back to WGS84
    return json.loads(buffered_gdf.to_crs(\"EPSG:4326\").to_json())

def clip_smart(target_layer: dict | str, mask_layer: dict | str) -> dict:
    \"\"\"Clip target layer with mask layer ensuring same CRS.\"\"\"
    t_parsed = safe_parse(target_layer)
    m_parsed = safe_parse(mask_layer)
    
    if not t_parsed or not m_parsed:
        return {"type": "FeatureCollection", "features": []}
        
    tgdf = gpd.GeoDataFrame.from_features(t_parsed if t_parsed.get(\"type\") == \"FeatureCollection\" else [t_parsed], crs=\"EPSG:4326\")
    mgdf = gpd.GeoDataFrame.from_features(m_parsed if m_parsed.get(\"type\") == \"FeatureCollection\" else [m_parsed], crs=\"EPSG:4326\")
    
    clipped = gpd.clip(tgdf, mgdf)
    return json.loads(clipped.to_json())

def dissolve_smart(geojson: dict | str, field: str = None) -> dict:
    \"\"\"Dissolve geometries in GeoJSON.\"\"\"
    parsed = safe_parse(geojson)
    if not parsed:
        return {"type": "FeatureCollection", "features": []}
        
    gdf = gpd.GeoDataFrame.from_features(parsed if parsed.get(\"type\") == \"FeatureCollection\" else [parsed], crs=\"EPSG:4326\")
    dissolved = gdf.dissolve(by=field).reset_index()
    return json.loads(dissolved.to_json())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 5: Commit changes**

```bash
git add app/lib/geo_processor/geometry.py tests/unit/lib/test_geo_processor.py
git commit -m \"feat(geo): implement smart geometry operations (buffer, clip, dissolve)\"
```

### Task 3: Implement Smart Overlay Operations

**Files:**
- Create: `app/lib/geo_processor/overlay.py`
- Test: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write failing tests for overlay operations**

```python
def test_overlay_smart():
    from app.lib.geo_processor.overlay import overlay_smart
    poly1 = {
        \"type\": \"FeatureCollection\",
        \"features\": [{\"type\": \"Feature\", \"geometry\": {\"type\": \"Polygon\", \"coordinates\": [[[0,0], [2,0], [2,2], [0,2], [0,0]]]}, \"properties\": {\"id\": 1}}]
    }
    poly2 = {
        \"type\": \"FeatureCollection\",
        \"features\": [{\"type\": \"Feature\", \"geometry\": {\"type\": \"Polygon\", \"coordinates\": [[[1,1], [3,1], [3,3], [1,3], [1,1]]]}, \"properties\": {\"id\": 2}}]
    }
    
    # Intersection
    res_int = overlay_smart(poly1, poly2, how=\"intersection\")
    assert len(res_int[\"features\"]) > 0
    
    # Union
    res_uni = overlay_smart(poly1, poly2, how=\"union\")
    assert len(res_uni[\"features\"]) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`
Expected: FAIL (ModuleNotFoundError for `app.lib.geo_processor.overlay`)

- [ ] **Step 3: Implement `app/lib/geo_processor/overlay.py`**

```python
import json
import geopandas as gpd
from app.lib.geo_processor.core import safe_parse

def overlay_smart(layer_a: dict | str, layer_b: dict | str, how: str = \"intersection\") -> dict:
    \"\"\"Perform overlay operation between two layers.\"\"\"
    a_parsed = safe_parse(layer_a)
    b_parsed = safe_parse(layer_b)
    
    if not a_parsed or not b_parsed:
        return {"type": "FeatureCollection", "features": []}
        
    gdf_a = gpd.GeoDataFrame.from_features(a_parsed if a_parsed.get(\"type\") == \"FeatureCollection\" else [a_parsed], crs=\"EPSG:4326\")
    gdf_b = gpd.GeoDataFrame.from_features(b_parsed if b_parsed.get(\"type\") == \"FeatureCollection\" else [b_parsed], crs=\"EPSG:4326\")
    
    result = gpd.overlay(gdf_a, gdf_b, how=how)
    return json.loads(result.to_json())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 5: Commit changes**

```bash
git add app/lib/geo_processor/overlay.py tests/unit/lib/test_geo_processor.py
git commit -m \"feat(geo): implement smart overlay operations (intersection, union, difference)\"
```

### Task 4: Final Verification and Cleanup

- [ ] **Step 1: Run all tests in the suite**
Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 2: Check for any linting issues (if applicable)**
Run: `flake8 app/lib/geo_processor` (or similar if available)
