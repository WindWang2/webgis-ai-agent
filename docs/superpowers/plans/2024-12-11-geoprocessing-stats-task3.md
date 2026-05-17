# Task 3: Geoprocessing Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete and verify the implementation of Task 3, specifically `hotspot_narrated` and ensuring all tests in `tests/unit/geoprocessing/test_statistics.py` pass.

**Architecture:** Use `scipy.spatial.distance_matrix` for distance calculations and `scipy.stats.norm` for p-value calculations in Getis-Ord Gi* analysis.

**Tech Stack:** Python, GeoPandas, NumPy, SciPy, Shapely, Pytest.

---

### Task 1: Cleanup and Refine `app/lib/geoprocessing/statistics.py`

**Files:**
- Modify: `app/lib/geoprocessing/statistics.py`

- [ ] **Step 1: Move imports to top**
Move `from scipy.stats import norm` to the top of the file for better style and consistency.

- [ ] **Step 2: Improve `hotspot_narrated` bandwidth calculation (Optional but recommended)**
If `bw` is too small, Gi* might not detect any significant clusters. Consider using a slightly larger bandwidth if auto-calculating. However, the primary issue is the test data. I will keep the current logic but ensure it's robust.

### Task 2: Fix and Expand Tests in `tests/unit/geoprocessing/test_statistics.py`

**Files:**
- Modify: `tests/unit/geoprocessing/test_statistics.py`

- [ ] **Step 1: Update `test_hotspot_narrated` to ensure significance**
Increase the number of points in clusters or adjust the values to ensure statistical significance with the auto-calculated bandwidth.

```python
def test_hotspot_narrated():
    # Hotspot: Group of high values, group of low values
    features = []
    # Hot area (10 points)
    for i in range(10):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0001*i, 0.0001*i]}, "properties": {"val": 100}})
    # Cold area (10 points)
    for i in range(10):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0 + 0.0001*i, 1.0 + 0.0001*i]}, "properties": {"val": 0}})
    
    geojson = {"type": "FeatureCollection", "features": features}
    from app.lib.geoprocessing.statistics import hotspot_narrated
    result = hotspot_narrated(geojson, "val")
    assert result.success is True
    assert "hot spots" in result.summary.lower()
    assert result.data["hot_spots_count"] > 0
    assert result.data["cold_spots_count"] > 0
```

- [ ] **Step 2: Add edge case tests**
Add tests for:
- Invalid field name
- Too few features (< 3)
- All identical values (variance = 0)

- [ ] **Step 3: Run all tests to verify**
Run: `pytest tests/unit/geoprocessing/test_statistics.py -v`
Expected: ALL PASS

### Task 3: Final Verification and Commit

- [ ] **Step 1: Run full suite and check coverage**
Ensure the new tool has good coverage.

- [ ] **Step 2: Commit changes**
Commit with a descriptive message.
