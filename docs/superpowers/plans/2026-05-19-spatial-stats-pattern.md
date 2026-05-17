# Spatial Statistics & Pattern Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spatial statistics tools (SDE, Moran's I, Hotspot) with narrative insights.

**Architecture:** Logic will reside in `app/lib/geoprocessing/statistics.py`, following the `GeoAnalysisResult` interface. It leverages existing UTM conversion and GeoJSON utility functions.

**Tech Stack:** Python, NumPy, SciPy, GeoPandas, Shapely.

---

### Task 1: Standard Deviational Ellipse (SDE)

**Files:**
- Create: `app/lib/geoprocessing/statistics.py`
- Test: `tests/unit/geoprocessing/test_statistics.py`

- [ ] **Step 1: Write the failing test for SDE**

```python
import pytest
import json
from app.lib.geoprocessing.statistics import calculate_sde

def test_calculate_sde_line():
    # Points in a vertical line
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 40.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 40.1]}, "properties": {}}
        ]
    }
    result = calculate_sde(geojson)
    assert result.success is True
    assert "Directional Insight" in result.summary
    assert "North-South" in result.summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_calculate_sde_line`
Expected: FAIL (Module not found)

- [ ] **Step 3: Implement `calculate_sde`**

```python
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon, mapping
from app.lib.geoprocessing.interface import GeoAnalysisResult
from app.tools._geojson_utils import to_utm_gdf, safe_parse_geojson

def calculate_sde(geojson: dict) -> GeoAnalysisResult:
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found", error_type="ValueError")
    
    gdf, utm_crs = res
    if len(gdf) < 3:
        return GeoAnalysisResult(False, None, "At least 3 points required", error_type="InsufficientData")

    coords = np.array([(g.x, g.y) for g in gdf.geometry])
    n = len(coords)
    mean_x, mean_y = coords.mean(axis=0)
    x_prime = coords[:, 0] - mean_x
    y_prime = coords[:, 1] - mean_y

    sum_x2 = np.sum(x_prime**2)
    sum_y2 = np.sum(y_prime**2)
    sum_xy = np.sum(x_prime * y_prime)

    # Angle calculation
    delta = sum_x2 - sum_y2
    if delta == 0:
        theta = np.pi / 4 if sum_xy > 0 else 0
    else:
        theta = 0.5 * np.arctan2(2 * sum_xy, delta)

    # Standard deviations
    sin_t = np.sin(theta)
    cos_t = np.cos(theta)
    
    sigma_x_2 = 2 * np.sum((x_prime * cos_t - y_prime * sin_t)**2) / n
    sigma_y_2 = 2 * np.sum((x_prime * sin_t + y_prime * cos_t)**2) / n
    sigma_x = np.sqrt(max(sigma_x_2, 0))
    sigma_y = np.sqrt(max(sigma_y_2, 0))

    # Create ellipse polygon
    t = np.linspace(0, 2*np.pi, 100)
    ell_x = sigma_x * np.cos(t)
    ell_y = sigma_y * np.sin(t)
    
    # Rotate and translate
    rot_x = mean_x + ell_x * cos_t - ell_y * sin_t
    rot_y = mean_y + ell_x * sin_t + ell_y * cos_t
    
    ellipse_poly = Polygon(np.column_stack([rot_x, rot_y]))
    ellipse_wgs84 = gpd.GeoSeries([ellipse_poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    
    # Directional Insight
    deg = np.degrees(theta) % 180
    if 67.5 <= deg < 112.5: direction = "North-South"
    elif 22.5 <= deg < 67.5: direction = "North-East to South-West"
    elif 112.5 <= deg < 157.5: direction = "North-West to South-East"
    else: direction = "East-West"
    
    area_km2 = ellipse_poly.area / 1e6
    summary = f"Directional Insight: The points show a clear {direction} directional trend, covering an area of {area_km2:.2f} sq km."
    
    data_out = {
        "type": "Feature",
        "geometry": mapping(ellipse_wgs84),
        "properties": {
            "center": [float(mean_x), float(mean_y)],
            "sigma_x": float(sigma_x),
            "sigma_y": float(sigma_y),
            "angle_deg": float(deg),
            "area_km2": float(area_km2)
        }
    }
    
    return GeoAnalysisResult(True, data_out, summary)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_calculate_sde_line`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/geoprocessing/statistics.py tests/unit/geoprocessing/test_statistics.py
git commit -m "feat: implement calculate_sde with directional insight"
```

### Task 2: Moran's I with Narrative

**Files:**
- Modify: `app/lib/geoprocessing/statistics.py`
- Test: `tests/unit/geoprocessing/test_statistics.py`

- [ ] **Step 1: Write the failing test for Moran's I**

```python
def test_moran_i_clustered():
    # Clustered values: high in one corner, low in other
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.01, 0.01]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"val": 0}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.01, 1.01]}, "properties": {"val": 0}}
        ]
    }
    from app.lib.geoprocessing.statistics import moran_i_narrated
    result = moran_i_narrated(geojson, "val")
    assert result.success is True
    assert "clustering" in result.summary.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_moran_i_clustered`
Expected: FAIL (Function not found)

- [ ] **Step 3: Implement `moran_i_narrated`**

```python
from scipy.spatial import distance_matrix

def _build_weights(gdf, k=8):
    coords = np.array([(g.x, g.y) for g in gdf.geometry])
    n = len(coords)
    dist = distance_matrix(coords, coords)
    w = np.zeros((n, n))
    for i in range(n):
        idx = np.argsort(dist[i])[1:k+1]
        w[i, idx] = 1.0
    return w

def moran_i_narrated(geojson: dict, value_field: str) -> GeoAnalysisResult:
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
    if not res: return GeoAnalysisResult(False, None, "Invalid GeoJSON")
    gdf, _ = res
    
    from app.tools._geojson_utils import extract_numeric_values
    values = extract_numeric_values(gdf, value_field)
    if values is None: return GeoAnalysisResult(False, None, f"Field {value_field} missing or non-numeric")
    
    n = len(values)
    if n < 3: return GeoAnalysisResult(False, None, "Need at least 3 features")
    
    w = _build_weights(gdf, k=min(8, n-1))
    z = values - values.mean()
    s0 = w.sum()
    numerator = np.sum(w * np.outer(z, z))
    denominator = np.sum(z**2)
    moran_i_val = (n / s0) * (numerator / denominator) if denominator > 0 else 0
    expected_i = -1.0 / (n - 1)
    
    # Simple permutation for p-value
    rng = np.random.default_rng(42)
    perms = 99
    perm_is = []
    for _ in range(perms):
        pv = rng.permutation(values)
        pz = pv - pv.mean()
        p_num = np.sum(w * np.outer(pz, pz))
        p_den = np.sum(pz**2)
        perm_is.append((n / s0) * (p_num / p_den) if p_den > 0 else 0)
    
    p_value = float(np.mean(np.abs(np.array(perm_is) - expected_i) >= np.abs(moran_i_val - expected_i)))
    
    if p_value < 0.05:
        pattern = "clustering" if moran_i_val > expected_i else "dispersion"
        narrative = f"There is a statistically significant {pattern} of {value_field} values (Moran's I: {moran_i_val:.4f}, p < {p_value:.4f})."
    else:
        narrative = f"The distribution of {value_field} appears to be spatially random (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f})."
    
    return GeoAnalysisResult(True, {"moran_i": moran_i_val, "p_value": p_value}, narrative)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_moran_i_clustered`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: add moran_i_narrated"
```

### Task 3: Hotspot Analysis with Narrative

**Files:**
- Modify: `app/lib/geoprocessing/statistics.py`
- Test: `tests/unit/geoprocessing/test_statistics.py`

- [ ] **Step 1: Write the failing test for Hotspot**

```python
def test_hotspot_narrated():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.001, 0.001]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.1, 0.1]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.101, 0.101]}, "properties": {"val": 10}}
        ]
    }
    from app.lib.geoprocessing.statistics import hotspot_narrated
    result = hotspot_narrated(geojson, "val")
    assert result.success is True
    assert "hot spots" in result.summary.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_hotspot_narrated`
Expected: FAIL (Function not found)

- [ ] **Step 3: Implement `hotspot_narrated`**

```python
from scipy.stats import norm

def hotspot_narrated(geojson: dict, value_field: str) -> GeoAnalysisResult:
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
    if not res: return GeoAnalysisResult(False, None, "Invalid GeoJSON")
    gdf, utm_crs = res
    
    from app.tools._geojson_utils import extract_numeric_values
    values = extract_numeric_values(gdf, value_field)
    if values is None: return GeoAnalysisResult(False, None, f"Field {value_field} missing")
    
    n = len(values)
    coords = np.array([(g.x, g.y) for g in gdf.geometry])
    dist = distance_matrix(coords, coords)
    np.fill_diagonal(dist, np.inf)
    bw = float(np.mean(np.min(dist, axis=1)))
    w = (dist <= bw).astype(float)
    
    x_bar = values.mean()
    s = values.std(ddof=0)
    
    hot_count = 0
    cold_count = 0
    features = []
    
    for i, row in gdf.iterrows():
        wi = w[i]
        sum_wi = np.sum(wi)
        sum_wi2 = np.sum(wi**2)
        numerator = np.sum(wi * values) - x_bar * sum_wi
        denominator = s * np.sqrt((n * sum_wi2 - sum_wi**2) / (n - 1))
        gi_star = float(numerator / denominator) if denominator > 0 else 0
        p = 2 * (1 - norm.cdf(abs(gi_star)))
        
        h_type = "Not Significant"
        if p < 0.05:
            if gi_star > 0:
                h_type = "Hot Spot"
                hot_count += 1
            else:
                h_type = "Cold Spot"
                cold_count += 1
        
        geom_wgs84 = gpd.GeoSeries([row.geometry], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
        props = {**{k:v for k,v in row.items() if k != 'geometry'}, "gi_star": gi_star, "p_value": p, "hotspot_type": h_type}
        features.append({
            "type": "Feature",
            "geometry": mapping(geom_wgs84),
            "properties": props
        })
        
    summary = f"Hotspot analysis identified {hot_count} statistically significant hot spots and {cold_count} cold spots."
    data_out = {"type": "FeatureCollection", "features": features}
    return GeoAnalysisResult(True, data_out, summary)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/geoprocessing/test_statistics.py -k test_hotspot_narrated`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: add hotspot_narrated"
```
