# Geoprocessing Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `generate_fishnet` and `aggregate_points_to_polygons` in `app/lib/geoprocessing/aggregation.py` with TDD and self-healing logic.

**Architecture:** 
- `generate_fishnet`: Creates a square or hexagonal grid over a specified bounding box. Includes OOM protection by automatically adjusting `cell_size` if it exceeds 50,000 cells.
- `aggregate_points_to_polygons`: Performs spatial join and statistical aggregation (count, sum, mean, max, min) of points within polygons.
- Uses `geopandas` and `shapely` for spatial operations.

**Tech Stack:** Python, Geopandas, Shapely, Pytest, Numpy.

---

### Task 1: Setup and `generate_fishnet` (Square)

**Files:**
- Create: `app/lib/geoprocessing/aggregation.py`
- Create: `tests/unit/geoprocessing/test_aggregation.py`

- [ ] **Step 1: Write the failing test for `generate_fishnet` (square)**

```python
import pytest
from app.lib.geoprocessing.aggregation import generate_fishnet
from shapely.geometry import Polygon

def test_generate_fishnet_square():
    bounds = [0, 0, 10, 10]
    cell_size = 5
    result = generate_fishnet(bounds, cell_size, type='square')
    
    assert result.success is True
    assert len(result.data['features']) == 4
    # Check first cell geometry
    first_geom = result.data['features'][0]['geometry']
    assert first_geom['type'] == 'Polygon'
    # 5x5 cell at (0,0)
    coords = first_geom['coordinates'][0]
    assert [0.0, 0.0] in coords
    assert [5.0, 0.0] in coords
    assert [5.0, 5.0] in coords
    assert [0.0, 5.0] in coords
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: FAIL (ModuleNotFound or ImportError)

- [ ] **Step 3: Write minimal implementation for `generate_fishnet` (square)**

```python
import numpy as np
import geopandas as gpd
from shapely.geometry import box, Polygon
from app.lib.geoprocessing.interface import GeoAnalysisResult

def generate_fishnet(bounds, cell_size, type='square'):
    xmin, ymin, xmax, ymax = bounds
    
    # OOM Protection
    width = xmax - xmin
    height = ymax - ymin
    estimated_cells = (width / cell_size) * (height / cell_size)
    
    warning = ""
    if estimated_cells > 50000:
        new_cell_size = np.sqrt((width * height) / 50000)
        warning = f"Warning: Grid too dense ({int(estimated_cells)} cells). Cell size adjusted from {cell_size} to {new_cell_size:.4f}."
        cell_size = new_cell_size

    if type == 'square':
        cols = list(np.arange(xmin, xmax, cell_size))
        rows = list(np.arange(ymin, ymax, cell_size))
        polygons = []
        for x in cols:
            for y in rows:
                polygons.append(box(x, y, x + cell_size, y + cell_size))
        
        grid = gpd.GeoDataFrame({'geometry': polygons})
        return GeoAnalysisResult(
            success=True,
            data=grid.__geo_interface__,
            summary=f"Generated {len(polygons)} square cells. {warning}".strip()
        )
    
    return GeoAnalysisResult(success=False, data=None, summary="Not implemented")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/geoprocessing/aggregation.py tests/unit/geoprocessing/test_aggregation.py
git commit -m "feat(geoprocessing): implement generate_fishnet square with OOM protection"
```

---

### Task 2: `generate_fishnet` (Hexagon)

**Files:**
- Modify: `app/lib/geoprocessing/aggregation.py`
- Modify: `tests/unit/geoprocessing/test_aggregation.py`

- [ ] **Step 1: Write the failing test for `generate_fishnet` (hexagon)**

```python
def test_generate_fishnet_hexagon():
    bounds = [0, 0, 10, 10]
    cell_size = 5
    result = generate_fishnet(bounds, cell_size, type='hexagon')
    
    assert result.success is True
    assert len(result.data['features']) > 0
    # Basic check for hexagonal structure (should have 6 points + closing point)
    assert len(result.data['features'][0]['geometry']['coordinates'][0]) == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: FAIL (Summary "Not implemented")

- [ ] **Step 3: Write implementation for `generate_fishnet` (hexagon)**

```python
def generate_fishnet(bounds, cell_size, type='square'):
    xmin, ymin, xmax, ymax = bounds
    
    # OOM Protection (same logic as before)
    width = xmax - xmin
    height = ymax - ymin
    # Hexagon area is ~0.649 * d^2 where d is "size" or spacing. 
    # For simplicity, use same OOM check.
    estimated_cells = (width / cell_size) * (height / cell_size)
    
    warning = ""
    if estimated_cells > 50000:
        new_cell_size = np.sqrt((width * height) / 50000)
        warning = f"Warning: Grid too dense ({int(estimated_cells)} cells). Cell size adjusted from {cell_size} to {new_cell_size:.4f}."
        cell_size = new_cell_size

    if type == 'square':
        cols = list(np.arange(xmin, xmax, cell_size))
        rows = list(np.arange(ymin, ymax, cell_size))
        polygons = []
        for x in cols:
            for y in rows:
                polygons.append(box(x, y, x + cell_size, y + cell_size))
        
        grid = gpd.GeoDataFrame({'geometry': polygons})
        return GeoAnalysisResult(
            success=True,
            data=grid.__geo_interface__,
            summary=f"Generated {len(polygons)} square cells. {warning}".strip()
        )
    
    elif type == 'hexagon':
        # Hexagon spacing
        dx = cell_size * 1.5
        dy = cell_size * np.sqrt(3)
        
        polygons = []
        for i, x in enumerate(np.arange(xmin, xmax + dx, dx)):
            offset = dy / 2 if i % 2 == 1 else 0
            for y in np.arange(ymin - offset, ymax + dy, dy):
                # Create a hexagon
                hexagon = []
                for angle in range(0, 360, 60):
                    rad = np.deg2rad(angle)
                    hexagon.append((x + cell_size * np.cos(rad), y + cell_size * np.sin(rad)))
                polygons.append(Polygon(hexagon))
        
        grid = gpd.GeoDataFrame({'geometry': polygons})
        return GeoAnalysisResult(
            success=True,
            data=grid.__geo_interface__,
            summary=f"Generated {len(polygons)} hexagonal cells. {warning}".strip()
        )
    
    return GeoAnalysisResult(success=False, data=None, summary=f"Unsupported type: {type}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/geoprocessing/aggregation.py
git commit -m "feat(geoprocessing): implement generate_fishnet hexagon"
```

---

### Task 3: `aggregate_points_to_polygons`

**Files:**
- Modify: `app/lib/geoprocessing/aggregation.py`
- Modify: `tests/unit/geoprocessing/test_aggregation.py`

- [ ] **Step 1: Write the failing test for `aggregate_points_to_polygons`**

```python
import json
from app.lib.geoprocessing.aggregation import aggregate_points_to_polygons

def test_aggregate_points_to_polygons():
    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2, 2]}, "properties": {"val": 20}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9, 9]}, "properties": {"val": 30}}
        ]
    }
    polygons = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [5,0], [5,5], [0,5], [0,0]]]}, "properties": {"name": "Zone A"}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[6,6], [10,6], [10,10], [6,10], [6,6]]]}, "properties": {"name": "Zone B"}}
        ]
    }
    
    result = aggregate_points_to_polygons(points, polygons, stats=['count', 'sum', 'mean'], value_field='val')
    
    assert result.success is True
    data = result.data
    assert len(data['features']) == 2
    
    # Zone A should have 2 points, sum 30, mean 15
    zone_a = next(f for f in data['features'] if f['properties']['name'] == 'Zone A')
    assert zone_a['properties']['count'] == 2
    assert zone_a['properties']['sum'] == 30
    assert zone_a['properties']['mean'] == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: FAIL (ImportError or NameError for aggregate_points_to_polygons)

- [ ] **Step 3: Write implementation for `aggregate_points_to_polygons`**

```python
def aggregate_points_to_polygons(points_geojson, polygons_geojson, stats=['count'], value_field=None):
    try:
        if isinstance(points_geojson, str):
            points_geojson = json.loads(points_geojson)
        if isinstance(polygons_geojson, str):
            polygons_geojson = json.loads(polygons_geojson)
            
        pts = gpd.GeoDataFrame.from_features(points_geojson['features'])
        polys = gpd.GeoDataFrame.from_features(polygons_geojson['features'])
        
        # Spatial Join
        joined = gpd.sjoin(pts, polys, how='left', predicate='within')
        
        # Group by polygon index (which is row index of polys)
        # sjoin adds 'index_right' which is the index of the polygon
        results = polys.copy()
        
        # Initialize stats columns
        for stat in stats:
            results[stat] = 0.0
            
        if not joined.empty:
            grouped = joined.groupby('index_right')
            
            for idx, group in grouped:
                if pd.isna(idx): continue
                idx = int(idx)
                
                if 'count' in stats:
                    results.loc[idx, 'count'] = len(group)
                
                if value_field and value_field in group.columns:
                    if 'sum' in stats:
                        results.loc[idx, 'sum'] = group[value_field].sum()
                    if 'mean' in stats:
                        results.loc[idx, 'mean'] = group[value_field].mean()
                    if 'max' in stats:
                        results.loc[idx, 'max'] = group[value_field].max()
                    if 'min' in stats:
                        results.loc[idx, 'min'] = group[value_field].min()

        # Generate summary
        poly_count = len(polys)
        pt_count = len(pts)
        max_stat = ""
        if 'count' in stats and not results['count'].empty:
            max_idx = results['count'].idxmax()
            max_val = results.loc[max_idx, 'count']
            # Try to get a name if available
            name_field = next((c for c in polys.columns if 'name' in c.lower()), None)
            zone_name = polys.loc[max_idx, name_field] if name_field else f"Polygon {max_idx}"
            max_stat = f" The highest concentration is in '{zone_name}' with {int(max_val)} points."

        summary = f"Aggregated {pt_count} points into {poly_count} polygons.{max_stat}"
        
        return GeoAnalysisResult(
            success=True,
            data=results.__geo_interface__,
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(success=False, data=None, summary=f"Aggregation failed: {str(e)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_aggregation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/geoprocessing/aggregation.py tests/unit/geoprocessing/test_aggregation.py
git commit -m "feat(geoprocessing): implement aggregate_points_to_polygons"
```

---

### Task 4: Final Validation

- [ ] **Step 1: Run all geoprocessing tests**

Run: `pytest tests/unit/geoprocessing/ -v`

- [ ] **Step 2: Check for any edge cases (e.g., empty inputs)**

Add a test for empty points.

- [ ] **Step 3: Final Commit**

```bash
git commit -m "test(geoprocessing): add edge cases and verify all aggregation features"
```
