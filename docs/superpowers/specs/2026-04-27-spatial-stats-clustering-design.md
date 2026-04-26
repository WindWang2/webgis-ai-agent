# Spatial Statistics & Clustering Design (Phase 1)

**Date:** 2026-04-27
**Status:** Approved

## Goal

Add 4 spatial analysis tools for clustering and geostatistics: DBSCAN/K-Means clustering, Moran's I spatial autocorrelation, Getis-Ord Gi* hotspot analysis, and Gaussian KDE surface estimation.

## Architecture

```
Agent → spatial_cluster / moran_i / hotspot_analysis / kde_surface
                    ↓
              app/tools/spatial_stats.py
                    ↓
         _to_gdf() → WGS84 → UTM projection → algorithm → UTM → WGS84
                    ↓
         scipy.spatial / sklearn.cluster / numpy
                    ↓
              GeoJSON output → Agent → MapLibre
```

Single tool file `app/tools/spatial_stats.py` using the existing `ToolRegistry` pattern. All distance calculations done in UTM (auto-detected from data centroid), results returned in WGS84.

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `app/tools/spatial_stats.py` | 4 tools registered via `register_spatial_stats_tools(registry)` |

### Modified Files

| File | Change |
|------|--------|
| `app/core/config.py` | No changes needed |
| `requirements.txt` | Add `scikit-learn>=1.4.0` |
| `app/api/routes/chat.py` | Import and call `register_spatial_stats_tools(registry)` |
| `app/services/chat_engine.py` | Update SYSTEM_PROMPT with new tools |

## Dependencies

- **New**: `scikit-learn>=1.4.0` — DBSCAN, KMeans, StandardScaler
- **Existing**: `scipy` (gaussian_kde, spatial.distance), `numpy`, `shapely`, `geopandas`

## Tools

### `spatial_cluster`

Spatial clustering of point features using DBSCAN or K-Means.

- **Params:**
  - `geojson` (dict) — Input GeoJSON FeatureCollection of points
  - `method` (str) — `"dbscan"` (default) or `"kmeans"`
  - `n_clusters` (int) — Number of clusters for K-Means (default 5)
  - `eps` (float) — Maximum distance between samples for DBSCAN in meters (default 1000)
  - `min_samples` (int) — Minimum samples in a DBSCAN neighborhood (default 5)
  - `value_field` (str, optional) — Numeric field to include as clustering dimension (weighted)
- **Algorithm:**
  1. Convert GeoJSON to GeoDataFrame, project to UTM
  2. Extract coordinates (and optional normalized attribute)
  3. DBSCAN: `sklearn.cluster.DBSCAN(eps=eps, min_samples=min_samples)` — eps converted from meters to CRS units
  4. K-Means: `sklearn.cluster.KMeans(n_clusters=n_clusters)`
  5. Assign `cluster_id` to each feature (-1 = noise for DBSCAN)
- **Output:** GeoJSON FeatureCollection with `cluster_id` property per feature + `cluster_stats` summary (count per cluster, centroid coordinates)

### `moran_i`

Global Moran's I spatial autocorrelation test.

- **Params:**
  - `geojson` (dict) — Input GeoJSON FeatureCollection
  - `value_field` (str) — Numeric field to test for spatial autocorrelation
  - `permutation_count` (int) — Number of random permutations for significance test (default 999)
- **Algorithm:**
  1. Convert to GeoDataFrame, extract values and compute spatial weights matrix (queen contiguity for polygons, k-nearest k=8 for points)
  2. Compute Moran's I = (N / W) * (Σᵢⱼ wᵢⱼ(xᵢ-x̄)(xⱼ-x̄)) / Σᵢ(xᵢ-x̄)²
  3. Compute expected I = -1/(N-1)
  4. Compute z-score and p-value via random permutation
  5. Classify: 聚集 (I > E[I], p < 0.05), 离散 (I < E[I], p < 0.05), 随机 (p >= 0.05)
- **Output:**
  ```json
  {
    "morans_i": 0.35,
    "expected_i": -0.01,
    "z_score": 4.2,
    "p_value": 0.001,
    "pattern": "聚集",
    "confidence": "99%",
    "n_features": 150,
    "interpretation": "数据呈现显著的空间聚集模式..."
  }
  ```

### `hotspot_analysis`

Getis-Ord Gi* local spatial autocorrelation (hotspot/coldspot detection).

- **Params:**
  - `geojson` (dict) — Input GeoJSON FeatureCollection
  - `value_field` (str) — Numeric field for hotspot analysis
  - `distance_band` (float) — Distance threshold for spatial weights in meters (default 0, auto-calculated as average nearest neighbor distance)
- **Algorithm:**
  1. Project to UTM, compute spatial weights (distance band)
  2. For each feature i: Gi* = (Σⱼ wᵢⱼxⱼ - X̄Σⱼwᵢⱼ) / (S × √((NΣⱼwᵢⱼ² - (Σⱼwᵢⱼ)²) / (N-1)))
  3. Compute z-score and p-value
  4. Classify: 热点 (z > 1.96, p < 0.05), 冷点 (z < -1.96, p < 0.05), 不显著 (otherwise)
- **Output:** GeoJSON FeatureCollection with additional properties per feature:
  ```json
  {
    "gi_star": 2.8,
    "z_score": 2.8,
    "p_value": 0.005,
    "hotspot_type": "热点",
    "confidence": "99%"
  }
  ```

### `kde_surface`

Gaussian kernel density estimation producing a continuous density surface.

- **Params:**
  - `geojson` (dict) — Input GeoJSON FeatureCollection of points
  - `bandwidth` (float) — Kernel bandwidth in meters (default 0 = auto via Silverman's rule)
  - `cell_size` (float) — Grid cell size in meters (default 500)
  - `value_field` (str, optional) — Numeric field to use as weight for each point
  - `bounds` (list, optional) — Bounding box `[xmin, ymin, xmax, ymax]` in WGS84 (default: data extent + 10% buffer)
- **Algorithm:**
  1. Project to UTM, generate regular grid over bounds
  2. Use `scipy.stats.gaussian_kde` with bandwidth
  3. Evaluate KDE at each grid cell center
  4. Build polygon grid with density values
- **Output:** GeoJSON FeatureCollection of grid polygons with `density` property + `stats` (min, max, mean, total)

## Shared Helpers

```python
def _to_gdf(geojson: dict) -> gpd.GeoDataFrame:
    """Convert GeoJSON dict to GeoDataFrame with WGS84 CRS."""

def _to_utm(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project to UTM zone based on data centroid."""

def _spatial_weights(gdf: gpd.GeoDataFrame, method: str = "queen", k: int = 8, distance_band: float | None = None) -> np.ndarray:
    """Build spatial weights matrix. Queen contiguity for polygons, k-nearest for points, distance-band for hotspot."""
```

## Registration

In `app/api/routes/chat.py`:
```python
from app.tools.spatial_stats import register_spatial_stats_tools
register_spatial_stats_tools(registry)
```

## SYSTEM_PROMPT Addition

Under `### 基础空间分析`, add:
```
- `spatial_cluster(geojson, method, eps, min_samples, n_clusters, value_field)` — 空间聚类（DBSCAN密度聚类/K-Means分割）
- `moran_i(geojson, value_field, permutation_count)` — 空间自相关检验（Moran's I），判断空间分布模式
- `hotspot_analysis(geojson, value_field, distance_band)` — 热点分析（Getis-Ord Gi*），识别统计显著的聚集区
- `kde_surface(geojson, bandwidth, cell_size, value_field, bounds)` — 核密度估计，生成连续密度面
```

## Degradation

| Condition | Behavior |
|-----------|----------|
| scikit-learn not installed | `spatial_cluster` returns error asking to `pip install scikit-learn` |
| < 3 features | Return error "至少需要3个要素" |
| Non-numeric value_field | Return error "字段必须是数值类型" |
| No value_field provided (moran_i, hotspot) | Return error with available numeric fields listed |

## Verification

1. Clustering: Load a set of points in Beijing, run DBSCAN with eps=500m → expect cluster assignment per point
2. Moran's I: Load Chinese province GDP data → expect positive I (GDP is spatially clustered)
3. Hotspot: Same GDP data → expect coastal provinces as hotspots
4. KDE: Load restaurant POIs → expect density surface grid with higher values in city center
