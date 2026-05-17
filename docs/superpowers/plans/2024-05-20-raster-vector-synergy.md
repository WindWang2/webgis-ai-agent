# Raster-Vector Synergy Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement professional Raster-Vector synergy modules (Zonal Stats & IDW Interpolation) with TDD.

**Architecture:** 
- `raster_ops.py`: Wraps `rasterstats.zonal_stats` to compute statistics for GeoJSON polygons against a raster file.
- `interpolation.py`: Implements IDW interpolation to map point data to an H3 grid.
- Modules are designed as stateless library functions.

**Tech Stack:** Python, `rasterio`, `rasterstats`, `h3`, `numpy`, `scipy`, `pytest`.

---

### Task 1: Environment Setup

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependencies to `requirements.txt`**
- [ ] **Step 2: Install dependencies**
Run: `pip install -r requirements.txt`
- [ ] **Step 3: Commit**
```bash
git add requirements.txt
git commit -m "chore: add rasterio and rasterstats dependencies"
```

---

### Task 2: Zonal Statistics Implementation

**Files:**
- Create: `app/lib/geo_analysis/raster_ops.py`
- Test: `tests/unit/lib/test_geo_analysis_pro.py`

- [ ] **Step 1: Write failing test for `zonal_statistics`**
```python
import pytest
import os
import numpy as np
import rasterio
from rasterio.transform import from_origin
from app.lib.geo_analysis.raster_ops import zonal_statistics

def test_zonal_statistics_basic(tmp_path):
    # Create a dummy raster
    raster_path = str(tmp_path / "test_raster.tif")
    data = np.ones((10, 10), dtype=np.float32)
    data[0:5, 0:5] = 2.0
    
    transform = from_origin(0, 10, 1, 1)
    with rasterio.open(
        raster_path, 'w', driver='GTiff',
        height=10, width=10, count=1, dtype=np.float32,
        crs='+proj=latlong', transform=transform
    ) as dst:
        dst.write(data, 1)

    # GeoJSON polygon covering the 2.0 area
    polygons = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 10], [5, 10], [5, 5], [0, 5], [0, 10]]]
            },
            "properties": {"id": 1}
        }]
    }

    stats = zonal_statistics(polygons, raster_path, stats=['mean', 'sum'])
    assert stats[0]['mean'] == 2.0
    assert stats[0]['sum'] == 50.0 # 5x5 = 25 pixels * 2.0 = 50.0
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/unit/lib/test_geo_analysis_pro.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.lib.geo_analysis.raster_ops'`

- [ ] **Step 3: Write minimal implementation of `zonal_statistics`**
```python
from rasterstats import zonal_stats

def zonal_statistics(polygons_geojson, raster_path, stats=['mean', 'sum', 'max', 'min']):
    """
    Compute zonal statistics for polygons against a raster.
    """
    return zonal_stats(polygons_geojson, raster_path, stats=stats)
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/unit/lib/test_geo_analysis_pro.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add app/lib/geo_analysis/raster_ops.py tests/unit/lib/test_geo_analysis_pro.py
git commit -m "feat: implement zonal_statistics"
```

---

### Task 3: IDW Interpolation Implementation

**Files:**
- Create: `app/lib/geo_analysis/interpolation.py`
- Test: `tests/unit/lib/test_geo_analysis_pro.py`

- [ ] **Step 1: Write failing test for `idw_interpolation`**
```python
from app.lib.geo_analysis.interpolation import idw_interpolation

def test_idw_interpolation_h3():
    # Simple points: (lat, lon, value)
    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.1, 30.1]}, "properties": {"val": 20}}
        ]
    }
    
    # Interpolate to H3 grid at resolution 8
    result = idw_interpolation(points, value_field="val", resolution=8)
    
    assert len(result) > 0
    assert "h3_index" in result[0]
    assert "value" in result[0]
    # Check if values are within range
    values = [r['value'] for r in result]
    assert min(values) >= 10 and max(values) <= 20
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/unit/lib/test_geo_analysis_pro.py -v`
Expected: FAIL with `ImportError` or `AttributeError` for `idw_interpolation`

- [ ] **Step 3: Write minimal implementation of `idw_interpolation`**
```python
import h3
import numpy as np
from scipy.spatial import KDTree

def idw_interpolation(points_geojson, value_field, resolution=8, power=2):
    """
    Simple IDW interpolation to H3 grid.
    """
    coords = []
    values = []
    for feature in points_geojson['features']:
        lon, lat = feature['geometry']['coordinates']
        coords.append([lat, lon])
        values.append(feature['properties'][value_field])
    
    coords = np.array(coords)
    values = np.array(values)
    
    # Get bounding box to find relevant H3 cells
    min_lat, min_lon = np.min(coords, axis=0)
    max_lat, max_lon = np.max(coords, axis=0)
    
    # Simple strategy: get all cells in bounding box (or simplified buffered area)
    # For a real implementation, we might use h3.polyfill or similar if we had a boundary
    # Here we'll just use the points' cells and their neighbors as a proxy for the area
    target_cells = set()
    for lat, lon in coords:
        cell = h3.latlng_to_cell(lat, lon, resolution)
        target_cells.add(cell)
        target_cells.update(h3.grid_disk(cell, 2)) # Buffer a bit
    
    tree = KDTree(coords)
    results = []
    for cell in target_cells:
        c_lat, c_lon = h3.cell_to_latlng(cell)
        dist, idx = tree.query([c_lat, c_lon], k=min(5, len(coords)))
        
        if np.any(dist == 0):
            val = values[idx[dist == 0][0]]
        else:
            weights = 1.0 / (dist ** power)
            val = np.sum(weights * values[idx]) / np.sum(weights)
            
        results.append({"h3_index": cell, "value": float(val)})
    
    return results
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/unit/lib/test_geo_analysis_pro.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add app/lib/geo_analysis/interpolation.py
git commit -m "feat: implement idw_interpolation with H3 output"
```

---

### Task 4: Final Verification and Cleanup

**Files:**
- Test: `tests/unit/lib/test_geo_analysis_pro.py`

- [ ] **Step 1: Run all tests in the project**
Run: `pytest tests/unit/lib/test_geo_analysis_pro.py`
- [ ] **Step 2: Verify code style/linting**
- [ ] **Step 3: Commit final changes**
