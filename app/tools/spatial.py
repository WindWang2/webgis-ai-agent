"""空间分析 FC 工具"""
import json
import logging
from typing import Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: str) -> dict | None:
    """安全解析 GeoJSON 字符串，处理截断/格式错误"""
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
        # 尝试修复常见的截断问题
        logger.warning(f"GeoJSON parse failed, attempting repair (length={len(geojson)})")
        # 尝试找到最后一个完整的 feature
        try:
            # 找到最后一个 } 并尝试闭合
            for end_pos in range(len(geojson) - 1, max(len(geojson) - 100, 0), -1):
                if geojson[end_pos] == '}':
                    candidate = geojson[:end_pos + 1] + ']}'
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict) and 'features' in result:
                            logger.info(f"GeoJSON repair succeeded, recovered {len(result.get('features', []))} features")
                            return result
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return None


def register_spatial_tools(registry: ToolRegistry):
    """注册空间分析工具"""

    @tool(registry, name="buffer_analysis",
           description="对几何要素进行缓冲区分析，返回缓冲区多边形",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection（JSON字符串）",
               "distance": "缓冲距离（米）",
               "unit": "单位：m/km，默认m"
           })
    def buffer_analysis(geojson: str, distance: float, unit: str = "m") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", data) if isinstance(data, dict) else data
            from app.services.spatial_analyzer import SpatialAnalyzer
            analyzer = SpatialAnalyzer()
            result = analyzer.buffer(features, distance, unit)
            if result.success:
                return {"geojson": result.data, "stats": result.stats}
            return {"error": result.error_message}
        except Exception as e:
            logger.error(f"Buffer analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="spatial_stats",
           description="计算几何要素的空间统计信息（面积、长度、中心点等）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection（JSON字符串）"
           })
    def spatial_stats(geojson: str) -> dict:
        try:
            import geopandas as gpd
            from shapely.geometry import shape

            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])
            if not features:
                return {"error": "No features in GeoJSON"}

            geometries = [shape(f["geometry"]) for f in features if f.get("geometry")]
            if not geometries:
                return {"error": "No valid geometries"}

            gdf = gpd.GeoSeries(geometries, crs="EPSG:4326")
            # 投影到等面积坐标系（Mollweide）计算面积/长度，单位为米
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
            return {"stats": stats}
        except Exception as e:
            logger.error(f"Spatial stats error: {e}")
            return {"error": str(e)}

    @tool(registry, name="nearest_neighbor",
           description="查找最近的邻近距离和空间分布模式",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection（JSON字符串）"
           })
    def nearest_neighbor(geojson: str) -> dict:
        try:
            import numpy as np
            from scipy.spatial import distance_matrix

            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])

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

            return {
                "point_count": len(points),
                "mean_nearest_distance": round(float(nn_distances.mean()), 4),
                "std_nearest_distance": round(float(nn_distances.std()), 4),
                "min_distance": round(float(nn_distances.min()), 4),
                "max_distance": round(float(nn_distances.max()), 4),
            }
        except ImportError:
            return {"error": "scipy not installed"}
        except Exception as e:
            logger.error(f"NN analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="heatmap_data",
           description="根据点要素生成热力图数据（网格密度统计）",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection（JSON字符串）",
               "cell_size": "网格大小（米），默认500",
               "radius": "搜索半径（米），默认1000"
           })
    def heatmap_data(geojson: str, cell_size: int = 500, radius: int = 1000) -> dict:
        try:
            import base64
            import io

            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
            from matplotlib.colors import LinearSegmentedColormap
            from scipy.ndimage import gaussian_filter

            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input: 无法解析，请确保数据完整"}
            features = data.get("features") or data.get("feature_collection", [])

            points = []
            for f in features:
                geom = f.get("geometry") or {}
                if geom.get("type") == "Point" and geom.get("coordinates"):
                    coords = geom["coordinates"]
                    points.append((coords[0], coords[1]))

            if not points:
                return {"error": "No point features"}

            xs = [p[0] for p in points]
            ys = [p[1] for p in points]

            cell_deg = cell_size / 111000
            margin = cell_deg * 3
            x_min, x_max = min(xs) - margin, max(xs) + margin
            y_min, y_max = min(ys) - margin, max(ys) + margin

            x_bins = np.arange(x_min, x_max + cell_deg, cell_deg)
            y_bins = np.arange(y_min, y_max + cell_deg, cell_deg)
            H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])

            # 高斯平滑（sigma 由 radius/cell_size 决定，最少 1）
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)  # 转置对齐经纬度轴

            # 色带：透明 → 深蓝 → 青 → 黄 → 橙 → 红
            cmap = LinearSegmentedColormap.from_list("heat", [
                (0.00, (0.00, 0.00, 0.50, 0.00)),
                (0.10, (0.00, 0.10, 0.90, 0.65)),
                (0.30, (0.00, 0.75, 1.00, 0.80)),
                (0.55, (0.10, 1.00, 0.20, 0.88)),
                (0.75, (1.00, 0.90, 0.00, 0.93)),
                (0.88, (1.00, 0.40, 0.00, 0.97)),
                (1.00, (0.90, 0.00, 0.00, 1.00)),
            ], N=256)

            # 渲染为 PNG（无边框，透明背景）
            dpi = 150
            w = H_smooth.shape[1] / dpi * 2
            h = H_smooth.shape[0] / dpi * 2
            fig, ax = plt.subplots(figsize=(max(w, 2), max(h, 2)), dpi=dpi)
            ax.imshow(
                H_smooth,
                cmap=cmap,
                origin="lower",
                aspect="auto",
                vmin=0,
                vmax=H_smooth.max(),
                interpolation="bilinear",
            )
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight",
                        pad_inches=0, transparent=True)
            plt.close(fig)
            buf.seek(0)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

            return {
                "type": "heatmap_raster",
                "image": img_b64,
                "bbox": [float(xedges[0]), float(yedges[0]),
                         float(xedges[-1]), float(yedges[-1])],
                "total_points": len(points),
            }
        except Exception as e:
            logger.error(f"Heatmap error: {e}")
            return {"error": str(e)}
