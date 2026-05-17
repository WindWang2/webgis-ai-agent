# Refactor Spatial Tools and Update System Prompt Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor remaining spatial tools to use `SpatialAnalyzer` and `GeoAnalysisResult`, and update the `SYSTEM_PROMPT` in `ChatEngine` with specific mandates.

**Architecture:** 
1. Enhance `SpatialAnalyzer` as a thin wrapper for all spatial operations.
2. Delegate core logic to `app/lib/geo_analysis/` and `app/lib/geoprocessing/`.
3. Standardize all spatial tools to return `GeoAnalysisResult.to_llm_response()`.
4. Update `SYSTEM_PROMPT` to enforce best practices and mandatory workflows.

**Tech Stack:** Python, GeoPandas, Shapely, Scipy, Scikit-learn, Matplotlib.

---

### Task 1: Enhance SpatialAnalyzer

**Files:**
- Modify: `app/services/spatial_analyzer.py`

- [ ] **Step 1: Add missing methods to `SpatialAnalyzer`**

```python
    @classmethod
    def path_analysis(
        cls,
        network_features: List[Dict],
        start_point: List[float],
        end_point: List[float],
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.network import shortest_path
        return shortest_path(
            {"type": "FeatureCollection", "features": network_features},
            start_point,
            end_point
        )

    @classmethod
    def spatial_join(
        cls,
        left_features: List[Dict],
        right_features: List[Dict],
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        try:
            import geopandas as gpd
            left_gdf = gpd.GeoDataFrame.from_features(left_features, crs="EPSG:4326")
            right_gdf = gpd.GeoDataFrame.from_features(right_features, crs="EPSG:4326")
            result_gdf = gpd.sjoin(left_gdf, right_gdf, how=join_type, predicate=predicate)
            summary = f"Spatial join ({predicate}) produced {len(result_gdf)} features."
            return GeoAnalysisResult(True, result_gdf.__geo_interface__, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"Spatial join failed: {str(e)}")

    @classmethod
    def spatial_cluster(
        cls,
        features: List[Dict],
        method: str = "dbscan",
        n_clusters: int = 5,
        eps: float = 1000,
        min_samples: int = 5,
        value_field: str = "",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.statistics import cluster_narrated
        return cluster_narrated(
            {"type": "FeatureCollection", "features": features},
            method=method,
            n_clusters=n_clusters,
            eps=eps,
            min_samples=min_samples,
            value_field=value_field
        )
```

- [ ] **Step 2: Add `central_feature` to `SpatialAnalyzer`**

```python
    @classmethod
    def central_feature(
        cls,
        features: List[Dict],
        method: str = "mean_center",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.statistics import calculate_central_feature
        return calculate_central_feature({"type": "FeatureCollection", "features": features}, method)
```

### Task 2: Implement logic in libraries

**Files:**
- Modify: `app/lib/geo_analysis/statistics.py`
- Modify: `app/lib/geo_analysis/network.py`

- [ ] **Step 1: Add `cluster_narrated` to `app/lib/geo_analysis/statistics.py`** (Move logic from `app/tools/spatial_stats.py`)

- [ ] **Step 2: Ensure `shortest_path` exists in `app/lib/geo_analysis/network.py`**

### Task 3: Refactor `app/tools/spatial.py`

**Files:**
- Modify: `app/tools/spatial.py`

- [ ] **Step 1: Refactor `spatial_stats` to use `SpatialAnalyzer.statistics`**
- [ ] **Step 2: Refactor `nearest_neighbor` to use `SpatialAnalyzer.nearest`**
- [ ] **Step 3: Refactor `heatmap_data` to return `GeoAnalysisResult` style output** (Wrap `_generate_heatmap` output or refactor it)

### Task 4: Refactor `app/tools/spatial_stats.py`

**Files:**
- Modify: `app/tools/spatial_stats.py`

- [ ] **Step 1: Update all tools to return `res.to_llm_response()`**
- [ ] **Step 2: Delegate `spatial_cluster` to `SpatialAnalyzer.spatial_cluster`**
- [ ] **Step 3: Delegate `kde_surface`, `kde_contours`, `voronoi_polygons`, `convex_hull`, `multi_ring_buffer` to `SpatialAnalyzer` or keep logic but ensure `GeoAnalysisResult` return.**

### Task 5: Refactor `app/tools/advanced_spatial.py`

**Files:**
- Modify: `app/tools/advanced_spatial.py`

- [ ] **Step 1: Update `path_analysis`, `zonal_stats`, `attribute_filter`, `spatial_join`, `central_feature` to use `SpatialAnalyzer` and return `GeoAnalysisResult`.**

### Task 6: Update SYSTEM_PROMPT

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Inject mandates into `SYSTEM_PROMPT`**
    - Mandate: 1. Boundary -> 2. Search -> 3. Clip -> 4. Analyze.
    - Mandate: `render_type="native"` for heatmaps.
    - Mandate: use of `summary` field for narration.
