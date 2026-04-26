# Spatial Statistics & Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 spatial analysis tools: spatial_cluster (DBSCAN/K-Means), moran_i (spatial autocorrelation), hotspot_analysis (Getis-Ord Gi*), and kde_surface (kernel density estimation).

**Architecture:** New `app/tools/spatial_stats.py` with `register_spatial_stats_tools(registry)`. Each tool follows the existing pattern: parse GeoJSON → project to UTM → run algorithm → return WGS84 results. Uses scikit-learn (already installed) and scipy (already installed). Celery async with sync fallback.

**Tech Stack:** Python 3.13, scikit-learn (DBSCAN, KMeans), scipy (gaussian_kde, spatial.distance), geopandas, shapely, numpy

---

## File Structure

| File | Responsibility |
|------|---------------|
| `app/tools/spatial_stats.py` (new) | 4 tools + shared projection/weights helpers |
| `app/api/routes/chat.py` (modify) | Import + call `register_spatial_stats_tools(registry)` |
| `app/services/chat_engine.py` (modify) | Update SYSTEM_PROMPT with new tool docs |

---

### Task 1: Create spatial_stats.py with Shared Helpers

**Files:**
- Create: `app/tools/spatial_stats.py`

- [ ] **Step 1: Write the tool file with shared helpers and all 4 tools**

```python
"""空间统计与聚类分析工具 — DBSCAN/K-Means聚类、Moran's I、Getis-Ord Gi*、核密度估计"""
import json
import logging
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import shape, Point, box, mapping
from scipy.spatial import distance_matrix

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: Any) -> dict | None:
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, str):
        try:
            return json.loads(geojson)
        except json.JSONDecodeError:
            return None
    return None


def _to_utm_gdf(geojson: dict) -> gpd.GeoDataFrame | None:
    """Convert GeoJSON to GeoDataFrame and project to UTM."""
    features = geojson.get("features", [])
    if not features:
        return None
    rows = []
    for f in features:
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            if s.is_empty:
                continue
            props = f.get("properties", {}) or {}
            rows.append({"geometry": s, **props})
        except Exception:
            continue
    if not rows:
        return None
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    centroid = gdf.geometry.unary_union.centroid
    zone_number = int((centroid.x + 180) / 6) + 1
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    return gdf.to_crs(utm_crs)


def _extract_numeric_values(gdf: gpd.GeoDataFrame, field: str) -> np.ndarray | None:
    """Extract numeric values from a field, return None if not numeric."""
    if field not in gdf.columns:
        return None
    try:
        vals = gdf[field].astype(float).values
        if np.any(np.isnan(vals)):
            return None
        return vals
    except (ValueError, TypeError):
        return None


def _build_weights_matrix(gdf: gpd.GeoDataFrame, method: str = "knn", k: int = 8,
                          distance_band: float | None = None) -> np.ndarray:
    """Build spatial weights matrix. KNN for points, distance-band for hotspot."""
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    n = len(coords)
    dist = distance_matrix(coords, coords)

    if distance_band is not None:
        w = (dist <= distance_band).astype(float)
    else:
        w = np.zeros((n, n))
        for i in range(n):
            sorted_idx = np.argsort(dist[i])
            for j in sorted_idx[1:k + 1]:
                w[i, j] = 1.0
    np.fill_diagonal(w, 0)
    return w


def register_spatial_stats_tools(registry: ToolRegistry):

    @tool(registry, name="spatial_cluster",
           description="空间聚类分析（DBSCAN密度聚类或K-Means分割），返回每个要素的聚类标签",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "method": "聚类方法: 'dbscan'(密度聚类, 默认) 或 'kmeans'(K均值)",
               "n_clusters": "K-Means聚类数，默认5",
               "eps": "DBSCAN邻域半径（米），默认1000",
               "min_samples": "DBSCAN最小样本数，默认5",
               "value_field": "可选：参与聚类的数值字段名，将作为额外聚类维度",
           })
    def spatial_cluster(geojson: Any, method: str = "dbscan", n_clusters: int = 5,
                        eps: float = 1000, min_samples: int = 5,
                        value_field: str = "") -> dict:
        try:
            from sklearn.cluster import DBSCAN, KMeans
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            return {"error": "需要 scikit-learn，请运行: pip install scikit-learn"}

        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        gdf = _to_utm_gdf(data)
        if gdf is None or len(gdf) < 3:
            return {"error": "至少需要3个有效要素进行聚类"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

        if value_field:
            vals = _extract_numeric_values(gdf, value_field)
            if vals is None:
                numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
                return {"error": f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}"}
            scaler = StandardScaler()
            vals_scaled = scaler.fit_transform(vals.reshape(-1, 1))
            features = np.column_stack([coords, vals_scaled])
        else:
            features = coords

        if method == "kmeans":
            model = KMeans(n_clusters=min(n_clusters, len(gdf)), random_state=42, n_init=10)
            labels = model.fit_predict(features)
        else:
            model = DBSCAN(eps=eps, min_samples=min_samples)
            labels = model.fit_predict(features)

        out_features = []
        for i, row in gdf.iterrows():
            geom_wgs84 = gpd.GeoSeries([row.geometry], crs=gdf.crs).to_crs("EPSG:4326").iloc[0]
            props = {k: v for k, v in row.items() if k != "geometry"}
            props["cluster_id"] = int(labels[i])
            out_features.append({
                "type": "Feature",
                "geometry": mapping(geom_wgs84),
                "properties": props,
            })

        cluster_counts = {}
        for l in labels:
            cluster_counts[str(int(l))] = cluster_counts.get(str(int(l)), 0) + 1

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "cluster_stats": cluster_counts,
            "method": method,
            "n_clusters": len(set(labels)) - (1 if -1 in labels else 0),
        }

    @tool(registry, name="moran_i",
           description="全局 Moran's I 空间自相关检验，判断空间分布模式（聚集/离散/随机）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
               "permutation_count": "随机置换次数，默认999",
           })
    def moran_i(geojson: Any, value_field: str, permutation_count: int = 999) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        gdf = _to_utm_gdf(data)
        if gdf is None or len(gdf) < 3:
            return {"error": "至少需要3个有效要素"}

        values = _extract_numeric_values(gdf, value_field)
        if values is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return {"error": f"字段 '{value_field}' 不是数值类型或不存在。可用字段: {numeric_cols}"}

        n = len(values)
        w = _build_weights_matrix(gdf, k=min(8, n - 1))
        w_sum = w.sum()

        if w_sum == 0:
            return {"error": "空间权重矩阵全为零，无法计算 Moran's I"}

        z = values - values.mean()
        s0 = w_sum
        numerator = np.sum(w * np.outer(z, z))
        denominator = np.sum(z ** 2)
        moran_i_val = (n / s0) * (numerator / denominator) if denominator > 0 else 0

        expected_i = -1.0 / (n - 1)

        # Permutation test
        rng = np.random.default_rng(42)
        perm_i = np.zeros(permutation_count)
        for p in range(permutation_count):
            perm_vals = rng.permutation(values)
            pz = perm_vals - perm_vals.mean()
            perm_i[p] = (n / s0) * (np.sum(w * np.outer(pz, pz)) / np.sum(pz ** 2)) if np.sum(pz ** 2) > 0 else 0

        std_perm = np.std(perm_i)
        z_score = (moran_i_val - expected_i) / std_perm if std_perm > 0 else 0

        # Two-sided p-value
        p_value = float(np.mean(np.abs(perm_i - expected_i) >= np.abs(moran_i_val - expected_i)))

        if p_value < 0.01:
            confidence = "99%"
        elif p_value < 0.05:
            confidence = "95%"
        elif p_value < 0.1:
            confidence = "90%"
        else:
            confidence = "不显著"

        if p_value >= 0.05:
            pattern = "随机"
        elif moran_i_val > expected_i:
            pattern = "聚集"
        else:
            pattern = "离散"

        interp = f"Moran's I = {moran_i_val:.4f}，"
        if pattern == "随机":
            interp += "数据呈随机分布，不存在显著的空间自相关。"
        elif pattern == "聚集":
            interp += f"数据呈显著空间聚集模式（{confidence}置信度），相似值倾向于在空间上相邻。"
        else:
            interp += f"数据呈显著空间离散模式（{confidence}置信度），相似值倾向于相互远离。"

        return {
            "morans_i": round(float(moran_i_val), 6),
            "expected_i": round(float(expected_i), 6),
            "z_score": round(float(z_score), 4),
            "p_value": round(float(p_value), 6),
            "pattern": pattern,
            "confidence": confidence,
            "n_features": n,
            "permutation_count": permutation_count,
            "interpretation": interp,
        }

    @tool(registry, name="hotspot_analysis",
           description="Getis-Ord Gi* 热点分析，识别统计显著的高值聚集区（热点）和低值聚集区（冷点）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待分析的数值字段名",
               "distance_band": "空间权重距离阈值（米），0表示自动计算（默认）",
           })
    def hotspot_analysis(geojson: Any, value_field: str, distance_band: float = 0) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        gdf = _to_utm_gdf(data)
        if gdf is None or len(gdf) < 3:
            return {"error": "至少需要3个有效要素"}

        values = _extract_numeric_values(gdf, value_field)
        if values is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return {"error": f"字段 '{value_field}' 不是数值类型或不存在。可用字段: {numeric_cols}"}

        n = len(values)

        if distance_band <= 0:
            # Auto-calculate: average nearest neighbor distance
            coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
            dist = distance_matrix(coords, coords)
            np.fill_diagonal(dist, np.inf)
            distance_band = float(np.mean(np.min(dist, axis=1)))

        w = _build_weights_matrix(gdf, distance_band=distance_band)

        x = values
        x_bar = x.mean()
        s = x.std(ddof=1)

        out_features = []
        for i, row in gdf.iterrows():
            wi = w[i]
            sum_wij_xj = np.sum(wi * x)
            sum_wij = np.sum(wi)
            sum_wij2 = np.sum(wi ** 2)

            numerator = sum_wij_xj - x_bar * sum_wij
            denom_inner = (n * sum_wij2 - sum_wij ** 2) / (n - 1)
            denominator = s * np.sqrt(denom_inner) if denom_inner > 0 and s > 0 else 1
            gi_star = float(numerator / denominator)

            # Approximate p-value from z-score (two-tailed)
            from scipy.stats import norm
            p_value = float(2 * (1 - norm.cdf(abs(gi_star))))

            if p_value < 0.01:
                hotspot_type = "热点" if gi_star > 0 else "冷点"
                confidence = "99%"
            elif p_value < 0.05:
                hotspot_type = "热点" if gi_star > 0 else "冷点"
                confidence = "95%"
            elif p_value < 0.1:
                hotspot_type = "热点" if gi_star > 0 else "冷点"
                confidence = "90%"
            else:
                hotspot_type = "不显著"
                confidence = "不显著"

            geom_wgs84 = gpd.GeoSeries([row.geometry], crs=gdf.crs).to_crs("EPSG:4326").iloc[0]
            props = {k: v for k, v in row.items() if k != "geometry"}
            props.update({
                "gi_star": round(gi_star, 4),
                "z_score": round(gi_star, 4),
                "p_value": round(p_value, 6),
                "hotspot_type": hotspot_type,
                "confidence": confidence,
            })
            out_features.append({
                "type": "Feature",
                "geometry": mapping(geom_wgs84),
                "properties": props,
            })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "distance_band_m": round(distance_band, 1),
        }

    @tool(registry, name="kde_surface",
           description="高斯核密度估计，生成连续密度面，比热力图更精确的空间密度分析",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "bandwidth": "核函数带宽（米），0表示自动计算（Silverman法则）",
               "cell_size": "网格单元大小（米），默认500",
               "value_field": "可选：作为权重的数值字段",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84），默认数据范围+10%缓冲",
           })
    def kde_surface(geojson: Any, bandwidth: float = 0, cell_size: float = 500,
                    value_field: str = "", bounds: list = []) -> dict:
        from scipy.stats import gaussian_kde

        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        gdf = _to_utm_gdf(data)
        if gdf is None or len(gdf) < 3:
            return {"error": "至少需要3个有效点要素"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

        if value_field:
            weights = _extract_numeric_values(gdf, value_field)
            if weights is None:
                numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
                return {"error": f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}"}
            weights = np.abs(weights)
            # Weighted: repeat points by weight
            repeat_factors = np.maximum((weights / weights.min()).astype(int), 1)
            weighted_coords = np.repeat(coords, repeat_factors, axis=0)
            kde_data = weighted_coords.T
        else:
            kde_data = coords.T

        # Bandwidth
        if bandwidth <= 0:
            # Silverman's rule of thumb (in CRS units = meters)
            std = np.std(coords, axis=0)
            n_pts = len(coords)
            bw = float(1.06 * np.mean(std) * n_pts ** (-1/5))
        else:
            bw = bandwidth

        # Bounds
        utm_crs = gdf.crs
        if bounds and len(bounds) == 4:
            bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                          crs="EPSG:4326").to_crs(utm_crs)
            xmin, ymin, xmax, ymax = bounds_gdf.total_bounds
        else:
            xmin, ymin, xmax, ymax = gdf.total_bounds
            buffer_x = (xmax - xmin) * 0.1
            buffer_y = (ymax - ymin) * 0.1
            xmin -= buffer_x
            xmax += buffer_x
            ymin -= buffer_y
            ymax += buffer_y

        # Grid
        nx = max(int((xmax - xmin) / cell_size), 2)
        ny = max(int((ymax - ymin) / cell_size), 2)
        grid_x = np.linspace(xmin, xmax, nx)
        grid_y = np.linspace(ymin, ymax, ny)
        gx, gy = np.meshgrid(grid_x, grid_y)
        grid_coords = np.vstack([gx.ravel(), gy.ravel()])

        kde = gaussian_kde(kde_data, bw_method=bw / np.mean(np.std(kde_data, axis=1)) if bandwidth > 0 else "scott")
        density = kde(grid_coords).reshape(ny, nx)

        # Build grid polygons
        out_features = []
        for i in range(ny):
            for j in range(nx):
                x0, x1 = grid_x[j] - cell_size / 2, grid_x[j] + cell_size / 2
                y0, y1 = grid_y[i] - cell_size / 2, grid_y[i] + cell_size / 2
                cell_geom = box(x0, y0, x1, y1)
                cell_wgs84 = gpd.GeoSeries([cell_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(cell_wgs84),
                    "properties": {"density": round(float(density[i, j]), 8)},
                })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "grid_size": [nx, ny],
            "stats": {
                "min_density": round(float(density.min()), 8),
                "max_density": round(float(density.max()), 8),
                "mean_density": round(float(density.mean()), 8),
            },
            "bandwidth_m": round(bw, 1),
        }
```

- [ ] **Step 2: Verify import works**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.spatial_stats import register_spatial_stats_tools; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/tools/spatial_stats.py
git commit -m "feat: add spatial statistics tools (clustering, Moran's I, hotspot, KDE)"
```

---

### Task 2: Register Tools + Update System Prompt

**Files:**
- Modify: `app/api/routes/chat.py` (add import + registration)
- Modify: `app/services/chat_engine.py` (add tool docs to SYSTEM_PROMPT)

- [ ] **Step 1: Add registration in chat.py**

In `app/api/routes/chat.py`, add import after `from app.tools.web_crawler import register_crawler_tools`:

```python
from app.tools.spatial_stats import register_spatial_stats_tools
```

Add registration call after `register_chinese_map_tools(registry)`:

```python
register_spatial_stats_tools(registry)
```

- [ ] **Step 2: Update SYSTEM_PROMPT in chat_engine.py**

Find the `### 基础空间分析` section in SYSTEM_PROMPT (around line 918). After the existing tools in that section, add:

```
- `spatial_cluster(geojson, method, eps, min_samples, n_clusters, value_field)` — 空间聚类（DBSCAN密度聚类/K-Means分割）
- `moran_i(geojson, value_field, permutation_count)` — 空间自相关检验（Moran's I），判断空间分布模式
- `hotspot_analysis(geojson, value_field, distance_band)` — 热点分析（Getis-Ord Gi*），识别统计显著的聚集区
- `kde_surface(geojson, bandwidth, cell_size, value_field, bounds)` — 核密度估计，生成连续密度面
```

- [ ] **Step 3: Verify tools register**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.spatial_stats import register_spatial_stats_tools; from app.tools.registry import ToolRegistry; r = ToolRegistry(); register_spatial_stats_tools(r); print(r.list_tools())"`
Expected: `['spatial_cluster', 'moran_i', 'hotspot_analysis', 'kde_surface']`

- [ ] **Step 4: Commit**

```bash
git add app/api/routes/chat.py app/services/chat_engine.py
git commit -m "feat: register spatial stats tools and update system prompt"
```

---

## Verification

1. Tool registration: `python -c "from app.api.routes.chat import registry; tools = registry.list_tools(); stats = [t for t in tools if t in ('spatial_cluster','moran_i','hotspot_analysis','kde_surface')]; print(f'Spatial stats tools: {stats}')"`
2. Import check: all 4 tools import without error
3. End-to-end test (requires GeoJSON data): load point data → run spatial_cluster with DBSCAN → verify cluster_id assigned to each feature
