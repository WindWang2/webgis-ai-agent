# GIS Refactoring Task 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Task 3 of the GIS refactoring by updating all tool wrappers to return `GeoAnalysisResult`, refining the `SpatialAnalyzer` thin wrapper, and updating the `SYSTEM_PROMPT` in `ChatEngine`.

**Architecture:** 
- **Tool Layer**: `app/tools/*.py` tools refactored to call `SpatialAnalyzer` or direct library functions, returning `GeoAnalysisResult.to_llm_response()`.
- **Service Layer**: `SpatialAnalyzer` as a thin delegator to `app/lib`.
- **Logic Layer**: New analytical functions in `app/lib/geo_analysis/statistics.py`.
- **Prompting**: Updated `SYSTEM_PROMPT` to enforce "Precision Protocol" and better tool usage.

**Tech Stack:** Python, GeoPandas, Shapely, Scipy, LLM (ChatEngine)

---

### Task 1: Enhance `app/lib/geo_analysis/statistics.py`

**Files:**
- Modify: `app/lib/geo_analysis/statistics.py`

- [ ] **Step 1: Add `calculate_nearest` and `calculate_central_feature`**

```python
def calculate_nearest(geojson: dict) -> GeoAnalysisResult:
    """Nearest neighbor analysis with narrative summary."""
    from scipy.spatial import distance_matrix
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, _ = res
    if len(gdf) < 2:
        return GeoAnalysisResult(False, None, "At least 2 points required for nearest neighbor analysis")
    
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    dist = distance_matrix(coords, coords)
    np.fill_diagonal(dist, np.inf)
    nn_dist = dist.min(axis=1)
    
    mean_dist = float(nn_dist.mean())
    std_dist = float(nn_dist.std())
    
    # Simple pattern recognition
    # Expected mean distance for random distribution (Poisson process)
    # R = Observed / Expected
    # Expected = 0.5 * sqrt(Area / N)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    area = (xmax - xmin) * (ymax - ymin)
    expected_mean = 0.5 * np.sqrt(area / len(gdf))
    r_ratio = mean_dist / expected_mean if expected_mean > 0 else 1
    
    pattern = "random"
    if r_ratio < 0.7: pattern = "clustered"
    elif r_ratio > 1.3: pattern = "dispersed"
    
    summary = f"Nearest Neighbor Insight: The mean distance to the nearest neighbor is {mean_dist:.2f} meters. The distribution pattern appears to be {pattern} (R ratio: {r_ratio:.2f})."
    
    data = {
        "mean_distance": mean_dist,
        "std_distance": std_dist,
        "min_distance": float(nn_dist.min()),
        "max_distance": float(nn_dist.max()),
        "r_ratio": r_ratio,
        "pattern": pattern
    }
    return GeoAnalysisResult(True, data, summary)

def calculate_central_feature(geojson: dict, method: str = "mean_center") -> GeoAnalysisResult:
    """Find the central feature or mean center."""
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, utm_crs = res
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    
    if method == "mean_center":
        mc = coords.mean(axis=0)
        center_pt = Point(mc[0], mc[1])
        summary = f"Mean Center: The average geographic center is at {mc[0]:.2f}, {mc[1]:.2f} (UTM)."
    else:
        # Central Feature: point with minimum total distance to all other points
        from scipy.spatial.distance import cdist
        dists = cdist(coords, coords).sum(axis=1)
        idx = np.argmin(dists)
        center_pt = gdf.geometry.iloc[idx]
        summary = f"Central Feature: The feature at index {idx} is identified as the central feature (minimum total distance to others)."
        
    center_wgs84 = gpd.GeoSeries([center_pt], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    data = {
        "type": "Feature",
        "geometry": mapping(center_wgs84),
        "properties": {"method": method, "summary": summary}
    }
    return GeoAnalysisResult(True, data, summary)
```

- [ ] **Step 2: Commit**

```bash
git add app/lib/geo_analysis/statistics.py
git commit -m "feat(gis): add nearest and central feature analysis to lib"
```

### Task 2: Refactor `app/services/spatial_analyzer.py`

**Files:**
- Modify: `app/services/spatial_analyzer.py`

- [ ] **Step 1: Update `SpatialAnalyzer` methods to use new lib functions**

```python
# In app/services/spatial_analyzer.py

from app.lib.geo_analysis.statistics import (
    calculate_sde, moran_i_narrated, hotspot_narrated,
    calculate_nearest, calculate_central_feature  # Add these
)

class SpatialAnalyzer:
    # ...
    @classmethod
    def nearest(cls, source_features: List[Dict], **kwargs) -> GeoAnalysisResult:
        return calculate_nearest({"type": "FeatureCollection", "features": source_features})

    @classmethod
    def central_feature(cls, features: List[Dict], method: str = "mean_center", **kwargs) -> GeoAnalysisResult:
        return calculate_central_feature({"type": "FeatureCollection", "features": features}, method=method)

    @classmethod
    def spatial_join(cls, left_features: List[Dict], right_features: List[Dict], join_type: str = "inner", predicate: str = "intersects") -> GeoAnalysisResult:
        """Robust spatial join using GeoPandas."""
        try:
            import geopandas as gpd
            gdf_l = gpd.GeoDataFrame.from_features(left_features)
            gdf_r = gpd.GeoDataFrame.from_features(right_features)
            # Ensure CRS
            if gdf_l.crs is None: gdf_l.set_crs("EPSG:4326", inplace=True)
            if gdf_r.crs is None: gdf_r.set_crs("EPSG:4326", inplace=True)
            
            joined = gpd.sjoin(gdf_l, gdf_r, how=join_type, predicate=predicate)
            summary = f"Spatial Join ({join_type}, {predicate}) produced {len(joined)} features."
            return GeoAnalysisResult(True, joined.__geo_interface__, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"Spatial join failed: {str(e)}")
```

- [ ] **Step 2: Commit**

```bash
git add app/services/spatial_analyzer.py
git commit -m "refactor(gis): update SpatialAnalyzer with robust implementations"
```

### Task 3: Update `app/tools/spatial.py`

**Files:**
- Modify: `app/tools/spatial.py`

- [ ] **Step 1: Refactor `spatial_stats` and `nearest_neighbor`**

```python
# In app/tools/spatial.py

    @tool(registry, name="spatial_stats",
           description="计算几何要素的空间统计信息（面积、长度、中心点等）")
    def spatial_stats(geojson: Any) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            raise ValueError("Invalid GeoJSON input")
        
        # We can use SpatialAnalyzer.recognize_vector_data or a more specific stats call
        res = SpatialAnalyzer.recognize_vector_data(data.get("features", []))
        # But old tool returned 'stats' dict. Let's provide a better summary.
        return res.to_llm_response()

    @tool(registry, name="nearest_neighbor",
           description="查找最近的邻近距离和空间分布模式")
    def nearest_neighbor(geojson: Any) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            raise ValueError("Invalid GeoJSON input")
        
        res = SpatialAnalyzer.nearest(data.get("features", []))
        return res.to_llm_response()
```

- [ ] **Step 2: Commit**

```bash
git add app/tools/spatial.py
git commit -m "refactor(gis): update spatial tools to use SpatialAnalyzer and GeoAnalysisResult"
```

### Task 4: Update `app/tools/advanced_spatial.py`

**Files:**
- Modify: `app/tools/advanced_spatial.py`

- [ ] **Step 1: Ensure all tools use `to_llm_response()` and `SpatialAnalyzer`**

```python
# In app/tools/advanced_spatial.py

    @tool(registry, name="attribute_filter",
           description="根据属性条件筛选地理要素。输入 Pandas 风格的查询表达式，返回过滤后的结果。",
           args_model=AttributeFilterArgs)
    def attribute_filter(geojson: Any, query: str) -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.attribute_filter(data.get("features", []), query)
        return res.to_llm_response()

    @tool(registry, name="spatial_join",
           description="基于空间拓扑关系将两个图层的属性进行联接。",
           args_model=SpatialJoinArgs)
    def spatial_join(left_layer: Any, right_layer: Any, join_type: str = "inner", predicate: str = "intersects") -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        l_data = safe_parse_geojson(left_layer)
        r_data = safe_parse_geojson(right_layer)
        res = SpatialAnalyzer.spatial_join(l_data.get("features", []), r_data.get("features", []), join_type, predicate)
        return res.to_llm_response()

    @tool(registry, name="central_feature",
           description="中心分析：寻找点集的中心位置。支持计算平均中心(mean_center)或寻找距离所有点最近的中心要素(central_feature)。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "method": "方法: 'mean_center'(平均中心) 或 'central_feature'(中心要素)",
           })
    def central_feature(geojson: Any, method: str = "mean_center") -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.central_feature(data.get("features", []), method)
        return res.to_llm_response()

    @tool(registry, name="service_area_simple",
           description="简单服务区分析：根据出行模式和时间生成服务范围。适合分析『某设施 15 分钟步行圈』。",
           param_descriptions={
               "geojson": "设施点要素集 GeoJSON 或引用(ref:xxx)",
               "travel_time_min": "出行时间（分钟），默认 15",
               "mode": "出行方式: 'walking'(默认, 5km/h), 'cycling'(15km/h), 'driving'(40km/h)",
               "dissolve": "是否合并所有点的服务区，默认 True",
           })
    def service_area_simple(geojson: Any, travel_time_min: float = 15, mode: str = "walking", dissolve: bool = True) -> dict:
        # Re-use logic but return res.to_llm_response()
        # ...
        r = SpatialAnalyzer.buffer(feats, distance=distance_m, unit="m", dissolve=dissolve)
        return r.to_llm_response()
```

- [ ] **Step 2: Commit**

```bash
git add app/tools/advanced_spatial.py
git commit -m "refactor(gis): update advanced spatial tools for consistency"
```

### Task 5: Refine System Prompt

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Update `SYSTEM_PROMPT`**

```python
# In app/services/chat_engine.py

SYSTEM_PROMPT = """你是一名 WebGIS 空间分析助手。用户与一张 MapLibre 地图实时交互，你通过工具调用读取/修改地图状态并执行空间分析。

## 地图即 Agent（核心约束）
...

## 精准分析协议 (Precision Protocol)
涉及特定区域或行政区分析时，必须遵循以下步骤：
1. **边界确定 (Boundary)**: 使用 `get_admin_division` 获取该区精准 GeoJSON 边界。
2. **数据获取 (Search)**: 使用 `search_poi_polygon` 在该边界内获取兴趣点，或用 `get_sub_districts_polygons` 获取子级行政区。
3. **空间裁剪 (Clip)**: 若搜索结果可能超出边界，使用 `clip_layer` 进行精准裁剪。
4. **深度分析 (Analyze)**: 对裁剪后的精准数据进行 `spatial_aggregate`、`hotspot_analysis` 或 `kde_contours`。

## 空间洞察原则
- **拒绝机械回答**: 不要只说"已执行工具"，要叙述工具返回的 `summary` 中的核心发现（如"95%置信度聚集"、"向西北方向延伸"）。
- **原生可视化优先**: 对于热度展示，优先使用 `heatmap_data(render_type="native")`。
- **图表辅助**: 统计结果必须搭配 `generate_chart`。

...
"""
```

- [ ] **Step 2: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "chore(gis): refine system prompt with Precision Protocol"
```

### Task 6: Final Verification

- [ ] **Step 1: Run integration tests**

Run: `pytest tests/test_spatial_tools.py tests/test_geoprocessing_interface.py -v`
Expected: ALL PASS

- [ ] **Step 2: Manual smoke test (if possible)**
Check logs for any tool execution errors.
