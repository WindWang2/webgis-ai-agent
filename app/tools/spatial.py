"""空间分析 FC 工具"""
import json
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson

logger = logging.getLogger(__name__)

class BufferAnalysisArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)")
    distance: float = Field(..., gt=0, description="缓冲距离（米），必须大于0")
    unit: str = Field("m", description="单位：m/km，默认m")

class HeatmapDataArgs(BaseModel):
    geojson: Any = Field(..., description="输入点要素 GeoJSON 或数据引用(ref:xxx)")
    cell_size: int = Field(500, ge=10, le=5000, description="网格大小（米），范围 10-5000")
    radius: int = Field(1000, ge=10, le=10000, description="搜索半径（米），范围 10-10000")
    render_type: str = Field("raster", description="渲染模式: raster(栅格), grid(格网), native(原生)")
    palette: str = Field("classic", description="配色方案: classic, magma, viridis, thermal")

def _generate_heatmap(features: list, cell_size: int = 500, radius: int = 1000,
                       render_type: str = "raster", palette: str = "classic") -> dict:
    """Generate heatmap data without Celery. Supports raster and grid render types."""
    import base64
    import io
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from scipy.ndimage import gaussian_filter
    from shapely.geometry import box, mapping

    fig = None
    try:
        points = []
        for f in features or []:
            if not isinstance(f, dict):
                continue
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point":
                coords = geom.get("coordinates")
                if coords and len(coords) >= 2:
                    try:
                        lon, lat = float(coords[0]), float(coords[1])
                        if not math.isnan(lon) and not math.isnan(lat):
                            points.append((lon, lat))
                    except (ValueError, TypeError):
                        continue

        if not points:
            return {"error": "No valid point features found"}

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        cell_deg = cell_size / 111000
        margin = cell_deg * 2
        x_min, x_max = min(xs) - margin, max(xs) + margin
        y_min, y_max = min(ys) - margin, max(ys) + margin

        if x_min == x_max:
            x_max += cell_deg
        if y_min == y_max:
            y_max += cell_deg

        x_bins = np.arange(x_min, x_max + cell_deg, cell_deg)
        y_bins = np.arange(y_min, y_max + cell_deg, cell_deg)

        if len(x_bins) > 5000 or len(y_bins) > 5000:
            return {"error": "Resolution too high for the data extent"}

        H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])

        if render_type == "grid":
            grid_features = []
            max_val = float(H.max()) if H.max() > 0 else 1.0

            MAX_GRID_FEATURES = 500_000
            total_cells = H[H > 0].size
            if total_cells > MAX_GRID_FEATURES:
                return {"error": f"Grid too dense ({total_cells} cells). Increase cell_size or reduce data extent. Max allowed: {MAX_GRID_FEATURES}"}

            for i in range(len(xedges) - 1):
                for j in range(len(yedges) - 1):
                    count = H[i, j]
                    if count > 0:
                        rect = box(xedges[i], yedges[j], xedges[i + 1], yedges[j + 1])
                        grid_features.append({
                            "type": "Feature",
                            "geometry": mapping(rect),
                            "properties": {
                                "count": int(count),
                                "weight": round(float(count / max_val), 4)
                            }
                        })

            return {
                "success": True,
                "data": {
                    "type": "FeatureCollection",
                    "features": grid_features,
                    "metadata": {
                        "render_type": "grid",
                        "field": "weight",
                        "cell_size": cell_size,
                        "point_count": len(points),
                        "palette": palette
                    }
                },
                "status_desc": f"Vector grid heatmap generated with {len(grid_features)} cells."
            }

        else:
            # Raster mode
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)
            
            v_max_actual = H_smooth.max()
            if v_max_actual > 0:
                H_smooth[H_smooth < v_max_actual * 0.01] = 0

            PALETTES = {
                "classic": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.15, (0.0, 1.0, 1.0, 0.4)),
                    (0.40, (0.0, 1.0, 0.0, 0.6)),
                    (0.70, (1.0, 1.0, 0.0, 0.8)),
                    (0.90, (1.0, 0.5, 0.0, 0.9)),
                    (1.00, (1.0, 0.0, 0.0, 1.0)),
                ],
                "magma": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.20, (0.2, 0.04, 0.48, 0.5)),
                    (0.50, (0.7, 0.13, 0.45, 0.7)),
                    (0.80, (0.99, 0.55, 0.35, 0.85)),
                    (1.00, (0.98, 0.94, 0.60, 1.0)),
                ],
                "viridis": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.25, (0.27, 0.0, 0.33, 0.5)),
                    (0.50, (0.13, 0.57, 0.55, 0.7)),
                    (0.75, (0.37, 0.79, 0.36, 0.85)),
                    (1.00, (0.99, 0.9, 0.14, 1.0)),
                ],
                "thermal": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.33, (0.0, 0.0, 1.0, 0.5)),
                    (0.66, (1.0, 1.0, 0.0, 0.8)),
                    (1.00, (1.0, 0.0, 0.0, 1.0)),
                ]
            }

            colors = PALETTES.get(palette, PALETTES["classic"])
            cmap = LinearSegmentedColormap.from_list("dynamic_heat", colors, N=256)

            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
            v_max = np.percentile(H_smooth, 98) if H_smooth.max() > 0 else 1.0
            if v_max <= 0:
                v_max = H_smooth.max() or 1.0

            ax.imshow(
                H_smooth,
                cmap=cmap,
                origin="lower",
                aspect="auto",
                vmin=0,
                vmax=v_max,
                interpolation="bilinear",
            )
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
            buf.seek(0)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

            return {
                "success": True,
                "data": {
                    "type": "heatmap_raster",
                    "image": img_b64,
                    "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                    "total_points": len(points),
                    "metadata": {
                        "render_type": "raster",
                        "point_count": len(points),
                        "palette": palette
                    }
                },
                "status_desc": f"Raster heatmap generated (palette: {palette}) covering {len(points)} points."
            }
    except (ValueError, TypeError, OSError, RuntimeError) as e:
        logger.error(f"Heatmap generation failed: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        if fig:
            plt.close(fig)

def register_spatial_tools(registry: ToolRegistry):
    """注册空间分析工具"""

    @tool(registry, name="buffer_analysis",
           description=(
               "缓冲区分析：对点/线/面要素生成指定距离的缓冲多边形。"
               "\n何时用：『学校 500m 范围内』『地铁站 1km 缓冲』『高压线两侧 50m 退让』等距离邻近查询的母图层；"
               "做空间叠加 (overlay_analysis) 前的几何准备。"
               "\n何时不用：(1) 多个距离环 (如 100/300/500m) — 用 multi_ring_buffer；"
               "(2) 路网真实通达距离 — 用 isochrone_analysis (按时间) 或 service_area_simple；"
               "(3) 仅需统计数量而不需缓冲几何 — 用 spatial_aggregate 配合点数据。"
               "\n关键约束：distance 必须 > 0；单位严格按 unit (默认米)；"
               "投影会自动转 UTM 做精确缓冲，结果回 WGS84。"
           ),
           args_model=BufferAnalysisArgs)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.buffer(features, distance, unit)
        return res.to_llm_response()

    @tool(registry, name="spatial_stats",
           description=(
               "几何级聚合统计：对一个 FeatureCollection 计算总面积、总长度、要素数、bbox、平均中心点。"
               "\n何时用：用户问『这个图层有多大』『总长多少公里』『大致位置在哪』；"
               "完成分析后给出量纲摘要 (always-on 报告)。"
               "\n何时不用：(1) 统计每个多边形内的点数 — 用 spatial_aggregate；"
               "(2) 统计点集的聚集模式 — 用 nearest_neighbor / moran_i；"
               "(3) 栅格的统计 — 用 zonal_stats。"
               "\n返回：{total_area_m2, total_length_m, count, bbox, centroid}"
           ))
    def spatial_stats(geojson: Any) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.statistics(features)
        return res.to_llm_response()

    @tool(registry, name="nearest_neighbor",
           description=(
               "最近邻分析 (NNA)：用平均最近邻距离 + R 比率判断点集是聚集 / 随机 / 均匀分布。"
               "\n何时用：拿到一组 POI 点 (餐厅、案件、设施) 想判断它们是否扎堆；"
               "对比两个城市的同类设施分布模式 (R<1 聚集，R≈1 随机，R>1 均匀)。"
               "\n何时不用：(1) 要找统计显著的热点 — 用 hotspot_analysis (Gi*) 或 moran_i；"
               "(2) 要画出聚类边界 — 用 spatial_cluster (DBSCAN)；"
               "(3) 要找密度等值面 — 用 kde_contours。"
               "\n输入：必须是点要素 (Point)。返回 {mean_nearest_distance, expected, R, pattern}。"
           ))
    def nearest_neighbor(geojson: Any) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.nearest(features)
        return res.to_llm_response()

    @tool(registry, name="heatmap_data",
           description="根据点要素生成热力图。支持 'raster' (栅格图片)、'grid' (矢量格网) 和 'native' (原生渲染) 模式。支持通过 palette 参数切换配色方案。",
           args_model=HeatmapDataArgs)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: int = 2000, render_type: str = "raster", palette: str = "classic") -> dict:
        data = safe_parse_geojson(geojson)
        if not data:
            raise ValueError("Invalid GeoJSON input")
        features = data.get("features") or data.get("feature_collection", [])
        
        if render_type == "native":
            if isinstance(data, dict):
                data["command"] = "add_native_heatmap"
                data["metadata"] = {
                    "render_type": "native",
                    "point_count": len(features),
                    "radius": radius,
                    "palette": palette
                }
            return data

        try:
            from app.services.spatial_tasks import run_heatmap_generation
            task = run_heatmap_generation.apply_async(
                kwargs={"features": features, "cell_size": cell_size, "radius": radius, "render_type": render_type, "palette": palette}
            )
            result = task.get(timeout=120)
        except (ImportError, RuntimeError, TimeoutError, OSError) as exc:
            if not isinstance(exc, ImportError):
                logger.warning(f"Celery unavailable for heatmap: {exc}")
            result = _generate_heatmap(features, cell_size, radius, render_type, palette)
        
        if result.get("success"):
            res_data = result.get("data")
            if isinstance(res_data, dict):
                if render_type == "raster":
                    res_data["command"] = "add_heatmap_raster"
                else:
                    res_data["command"] = "add_layer"
            return res_data
        
        error_msg = result.get("error", "Heatmap generation failed")
        if "dense" in error_msg.lower() or "resolution" in error_msg.lower():
            raise ValueError(error_msg)
        raise RuntimeError(error_msg)

    @tool(registry, name="query_map_features",
           description="地图要素探查：在指定坐标位置查询地图上已有的要素详情。适合用户询问『这个点是什么』或需要获取特定要素属性时使用。",
           param_descriptions={
               "location": "查询位置经纬度 [lng, lat]",
               "buffer_m": "查询半径（米），默认 10",
           })
    def query_map_features(location: List[float], buffer_m: float = 10) -> dict:
        return {
            "command": "query_features",
            "location": location,
            "buffer_m": buffer_m,
            "summary": f"Initiated feature query at {location} within {buffer_m}m."
        }
