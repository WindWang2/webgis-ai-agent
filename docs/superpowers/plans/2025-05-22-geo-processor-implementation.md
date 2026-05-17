# Geo Processor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the `geo_processor` foundation library with core geoprocessing functions and automatic CRS management.

**Architecture:** A modular library in `app/lib/geo_processor` with functional APIs for parsing, projection, geometry operations, and overlays.

**Tech Stack:** Python, GeoPandas, Shapely, Pytest.

---

### Task 1: Scaffolding and Core Engine (geo_processor)

**Files:**
- Create: `app/lib/geo_processor/__init__.py`
- Create: `app/lib/geo_processor/core.py`
- Create: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write initial tests for safe_parse and to_utm_gdf**

```python
import pytest
from app.lib.geo_processor.core import safe_parse, to_utm_gdf
import geopandas as gpd

def test_safe_parse():
    assert safe_parse('{"type": "Point", "coordinates": [0, 0]}') == {"type": "Point", "coordinates": [0, 0]}
    assert safe_parse({"type": "Point", "coordinates": [0, 0]}) == {"type": "Point", "coordinates": [0, 0]}
    assert safe_parse("invalid") is None

def test_to_utm_gdf():
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, "properties": {"name": "Beijing"}}]
    }
    gdf, utm_crs = to_utm_gdf(geojson)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert utm_crs.startswith("EPSG:326")
    assert gdf.crs == utm_crs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`
Expected: FAIL (Module not found or functions not defined)

- [ ] **Step 3: Implement safe_parse and to_utm_gdf in core.py**

Migrate and harden from `app/tools/_geojson_utils.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/lib/geo_processor/__init__.py app/lib/geo_processor/core.py tests/unit/lib/test_geo_processor.py
git commit -m "feat: scaffold geo_processor and implement core engine"
```

### Task 2: Coordinate Transformers

**Files:**
- Modify: `app/lib/geo_processor/core.py`
- Modify: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write tests for coordinate transformers**

```python
from app.lib.geo_processor.core import wgs84_to_gcj02, gcj02_to_wgs84

def test_coord_transform_smoke():
    lng, lat = 116.404, 39.915 # Beijing
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng != lng
    assert gcj_lat != lat
    
    wgs_lng, wgs_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(wgs_lng - lng) < 1e-6
    assert abs(wgs_lat - lat) < 1e-6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 3: Implement coordinate transformers in core.py**

Migrate from `app/utils/coord_transform.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/lib/geo_processor/core.py tests/unit/lib/test_geo_processor.py
git commit -m "feat: migrate coordinate transformers to geo_processor.core"
```

### Task 3: Geometry Engine (Buffer and Clip)

**Files:**
- Create: `app/lib/geo_processor/geometry.py`
- Modify: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write tests for buffer_smart and clip_smart**

```python
from app.lib.geo_processor.geometry import buffer_smart, clip_smart

def test_buffer_smart():
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    result = buffer_smart(geojson, 100) # 100 meters
    assert result["type"] == "Polygon"

def test_clip_smart():
    target = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]]]}, "properties": {}}]
    }
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[116.5, 39.5], [117.5, 39.5], [117.5, 40.5], [116.5, 40.5], [116.5, 39.5]]]}, "properties": {}}]
    }
    result = clip_smart(target, mask)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 3: Implement buffer_smart and clip_smart in geometry.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/lib/test_geo_processor.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/lib/geo_processor/geometry.py tests/unit/lib/test_geo_processor.py
git commit -m "feat: implement smart buffer and clip in geo_processor.geometry"
```

### Task 4: Dissolve and Overlay Engine

**Files:**
- Modify: `app/lib/geo_processor/geometry.py`
- Create: `app/lib/geo_processor/overlay.py`
- Modify: `tests/unit/lib/test_geo_processor.py`

- [ ] **Step 1: Write tests for dissolve and overlays**

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement dissolve_smart in geometry.py**

- [ ] **Step 4: Implement intersection, union, difference in overlay.py**

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Commit**

```bash
git add app/lib/geo_processor/geometry.py app/lib/geo_processor/overlay.py tests/unit/lib/test_geo_processor.py
git commit -m "feat: implement dissolve and overlay operations"
```

### Task 5: Final Verification

- [ ] **Step 1: Run all tests in the project**
- [ ] **Step 2: Ensure docstrings are present**
- [ ] **Step 3: Final Commit**
