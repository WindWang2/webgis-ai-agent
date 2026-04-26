"""空间分析 FC 工具"""
import json
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.spatial_analyzer import SpatialAnalyzer

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: Any) -> dict | None:
    """安全解析 GeoJSON，支持字符串或字典"""
    if isinstance(geojson, dict):
        return geojson
    if not isinstance(geojson, str):
        return None
    geojson = geojson.strip()
    if not geojson:
        return None
    try:
        return json.loads(geojson)
    except json.JSONDecodeError:
        logger.warning(f"GeoJSON parse failed, attempting repair (length={len(geojson)})")
        try:
            for end_pos in range(len(geojson) - 1, max(len(geojson) - 100, 0), -1):
                if geojson[end_pos] == '}':
                    candidate = geojson[:end_pos + 1] + ']}'
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict) and 'features' in result:
                            return result
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return None


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
            return {"success": False, "error": "No valid point features found"}

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        # 1 degree ≈ 111000 m
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
            return {"success": False, "error": "Resolution too high for the data extent"}

        H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])

        if render_type == "grid":
            grid_features = []
            max_val = float(H.max()) if H.max() > 0 else 1.0

            MAX_GRID_FEATURES = 500_000
            total_cells = H[H > 0].size
            if total_cells > MAX_GRID_FEATURES:
                return {"success": False, "error": f"Grid too dense ({total_cells} cells). Increase cell_size or reduce data extent. Max allowed: {MAX_GRID_FEATURES}"}

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
    except Exception as e:
        logger.error(f"Heatmap generation failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        if fig:
            plt.close(fig)


def register_spatial_tools(registry: ToolRegistry):
    """注册空间分析工具"""

    @tool(registry, name="buffer_analysis",
           description="对几何要素进行缓冲区分析，返回缓冲区多边形",
           args_model=BufferAnalysisArgs)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", data) if isinstance(data, dict) else data

            try:
                from app.services.spatial_tasks import run_buffer_analysis
                task = run_buffer_analysis.apply_async(args=[features, distance, unit])
                result = task.get(timeout=120)
            except Exception as exc:
                if not isinstance(exc, ImportError):
                    logger.warning(f"Celery unavailable for buffer_analysis: {exc}")
                r = SpatialAnalyzer.buffer(features, distance=distance, unit=unit)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Buffer analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="spatial_stats",
           description="计算几何要素的空间统计信息（面积、长度、中心点等）")
    def spatial_stats(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])

            try:
                from app.services.spatial_tasks import run_spatial_stats
                task = run_spatial_stats.apply_async(args=[features])
                result = task.get(timeout=60)
            except Exception as exc:
                if not isinstance(exc, ImportError):
                    logger.warning(f"Celery unavailable for spatial_stats: {exc}")
                from shapely.geometry import shape
                import geopandas as gpd
                geometries = [shape(f["geometry"]) for f in features if f.get("geometry")]
                if not geometries:
                    return {"error": "No valid geometries"}
                gdf = gpd.GeoSeries(geometries, crs="EPSG:4326")
                gdf_proj = gdf.to_crs("ESRI:54009")
                stats = {
                    "feature_count": len(geometries),
                    "total_area_sqkm": round(float(gdf_proj.area.sum()) / 1e6, 4),
                    "total_length_km": round(float(gdf_proj.length.sum()) / 1000, 4),
                    "centroid": {
                        "lat": round(float(gdf.centroid.y.mean()), 6),
                        "lon": round(float(gdf.centroid.x.mean()), 6),
                    },
                    "bounds": {
                        "min_lon": round(float(gdf.bounds.minx.min()), 6),
                        "min_lat": round(float(gdf.bounds.miny.min()), 6),
                        "max_lon": round(float(gdf.bounds.maxx.max()), 6),
                        "max_lat": round(float(gdf.bounds.maxy.max()), 6),
                    },
                }
                result = {"success": True, "stats": stats}

            if result.get("success"):
                return {"stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Spatial stats error: {e}")
            return {"error": str(e)}

    @tool(registry, name="nearest_neighbor",
           description="查找最近的邻近距离和空间分布模式")
    def nearest_neighbor(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])

            try:
                from app.services.spatial_tasks import run_nearest_neighbor
                task = run_nearest_neighbor.apply_async(args=[features])
                result = task.get(timeout=60)
            except Exception as exc:
                if not isinstance(exc, ImportError):
                    logger.warning(f"Celery unavailable for nearest_neighbor: {exc}")
                import numpy as np
                from scipy.spatial import distance_matrix
                points = []
                for f in features:
                    geom = f.get("geometry") or {}
                    if geom.get("type") == "Point" and geom.get("coordinates"):
                        coords = geom["coordinates"]
                        points.append((coords[0], coords[1]))
                if len(points) < 2:
                    return {"error": "Need at least 2 points"}
                coords_arr = np.array(points)
                dist = distance_matrix(coords_arr, coords_arr)
                np.fill_diagonal(dist, np.inf)
                nn_distances = dist.min(axis=1)
                result = {
                    "success": True,
                    "data": {
                        "point_count": len(points),
                        "mean_nearest_distance": round(float(nn_distances.mean()), 4),
                        "std_nearest_distance": round(float(nn_distances.std()), 4),
                        "min_distance": round(float(nn_distances.min()), 4),
                        "max_distance": round(float(nn_distances.max()), 4),
                    }
                }

            if result.get("success"):
                return result.get("data")
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"NN analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="heatmap_data",
           description="根据点要素生成热力图。支持 'raster' (栅格图片)、'grid' (矢量格网) 和 'native' (原生渲染) 模式。支持通过 palette 参数切换配色方案。",
           args_model=HeatmapDataArgs)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: int = 2000, render_type: str = "raster", palette: str = "classic") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features") or data.get("feature_collection", [])
            
            # --- 原生热力图模式 ---
            if render_type == "native":
                if isinstance(data, dict):
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
            except Exception as exc:
                if not isinstance(exc, ImportError):
                    logger.warning(f"Celery unavailable for heatmap: {exc}")
                result = _generate_heatmap(features, cell_size, radius, render_type, palette)
            if result.get("success"):
                data = result.get("data")
                # 注入 render 指令暗示前端
                if isinstance(data, dict):
                    if render_type == "raster":
                        data["command"] = "add_heatmap_raster"
                    else:
                        data["command"] = "add_layer"
                return data
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Heatmap error: {e}")
            return {"error": str(e)}
