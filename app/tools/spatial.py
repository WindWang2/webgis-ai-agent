"""空间分析 FC 工具"""
import json
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import cached_tool, trim_features
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_analysis.density import generate_heatmap_raster
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson

logger = logging.getLogger(__name__)

_PALETTE_MAP = {
    "classic": "YlOrRd",
    "magma": "Magma",
    "viridis": "Viridis",
    "thermal": "Reds",
}


def _build_legend_spec(palette: str, min_val: float = 0.0, max_val: float = 1.0) -> dict:
    """Build a continuous legend spec for heatmap rendering."""
    from app.lib.cartography.palettes import resolve_palette_colors
    palette_key = _PALETTE_MAP.get(palette, "YlOrRd")
    palette_colors = resolve_palette_colors(palette_key)
    return {
        "type": "continuous",
        "min": min_val,
        "max": max_val,
        "palette": palette_key,
        "palette_colors": palette_colors,
    }


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
    @cached_tool(ttl=86400)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.buffer(features, distance, unit)
        out = res.to_llm_response()
        # 裁剪可能很大的缓冲结果载荷
        if isinstance(out, dict) and out.get("type") == "FeatureCollection":
            out = trim_features(out)
        elif isinstance(out, dict) and isinstance(out.get("data"), dict) and out["data"].get("type") == "FeatureCollection":
            out["data"] = trim_features(out["data"])
        return out

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
           description=(
               "点要素热力图。✅ 用于：用户宽泛询问『分布』『热度』『密度趋势』时"
               "的首选——优先 render_type='native' 原生渲染，轻量、不增加数据负担。"
               "\n❌ 不要用于：(1) 需要网格统计值（每格计数/求和）— 用 h3_binning；"
               "(2) 需要矢量等值面用于导出/制图 — 用 kde_contours；"
               "(3) 需要连续概率面做后续叠加分析 — 用 kde_surface。"
           ),
           args_model=HeatmapDataArgs)
    @cached_tool(ttl=3600)
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
                # Generate legend_spec for native mode so the frontend can
                # show a color gradient legend alongside the heatmap layer.
                try:
                    data["legend_spec"] = _build_legend_spec(palette)
                except Exception as e:
                    logger.warning(f"[heatmap_data] legend_spec generation failed: {e}")
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                data = trim_features(data)
            return data

        try:
            from app.services.spatial_tasks import run_heatmap_generation
            task = run_heatmap_generation.apply_async(
                kwargs={"features": features, "cell_size": cell_size, "radius": radius, "render_type": render_type, "palette": palette}
            )
            result = task.get(timeout=120)
        except Exception as exc:  # noqa: BLE001
            # Celery unavailable or task failed — fall back to in-process computation.
            # Any Celery failure (ImportError, broker down, TimeoutError, WorkerLostError)
            # degrades gracefully to an in-process fallback.
            logger.warning(f"[heatmap_data] Celery fallback triggered: {type(exc).__name__}: {exc}")
            result = generate_heatmap_raster(features, cell_size, radius, render_type, palette)
        
        if result.get("success"):
            res_data = result.get("data")
            if isinstance(res_data, dict):
                if render_type == "raster":
                    res_data["command"] = "add_heatmap_raster"
                else:
                    res_data["command"] = "add_layer"
                # non-native modes emit continuous legend_spec
                if render_type != "native":
                    try:
                        metadata = res_data.get("metadata", {})
                        res_data["legend_spec"] = _build_legend_spec(
                            palette,
                            min_val=float(metadata.get("min_value", 0.0)),
                            max_val=float(metadata.get("max_value", 1.0)),
                        )
                    except Exception as e:
                        logger.warning(f"[heatmap_data] legend_spec generation failed (result path): {e}")
            if isinstance(res_data, dict) and res_data.get("type") == "FeatureCollection":
                res_data = trim_features(res_data)
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
