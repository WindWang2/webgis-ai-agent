"""空间分析 FC 工具"""
import logging
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import cached_tool, std_error_response, trim_features
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_analysis.density import generate_heatmap_raster
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson
from app.lib.cartography.palettes import (
    HEATMAP_LEGEND_PALETTE_KEY,
    NATIVE_HEATMAP_COLORS,
)

logger = logging.getLogger(__name__)

# 单一来源：与 palettes.HEATMAP_LEGEND_PALETTE_KEY 同一映射（#717 审查意见：
# 新增 native 色带时只改 palettes.py 一处）
_PALETTE_MAP = dict(HEATMAP_LEGEND_PALETTE_KEY)

# native 渲染的图例色：与前端 HEATMAP_PALETTES 同源的单一色源在
# app/lib/cartography/palettes.py（NATIVE_HEATMAP_COLORS）——图例、MapSpec
# 授权与前端渲染共用，杜绝「地图多色、图例另一套色」的错位。


def _build_legend_spec(palette: str, min_val: float = 0.0, max_val: float = 1.0,
                       colors: Optional[List[str]] = None) -> dict:
    """Build a continuous legend spec for heatmap rendering.

    ``colors`` 直出图例渐变色（native 路径传前端同源停靠点色）；
    缺省走 matplotlib 风格的 cartography 调色板解析（raster 等路径）。
    #718: native 路径的构建统一委托 palettes.build_heatmap_legend_spec
    （单一构建口，webgis_map_product 与本工具共用）。
    """
    from app.lib.cartography.palettes import build_heatmap_legend_spec, resolve_palette_colors
    if colors is None and palette in _PALETTE_MAP_NAMED:
        return build_heatmap_legend_spec(palette, min_val, max_val)
    palette_key = _PALETTE_MAP.get(palette, "YlOrRd")
    palette_colors = colors if colors else resolve_palette_colors(palette_key)
    return {
        "type": "continuous",
        "min": min_val,
        "max": max_val,
        "palette": palette_key,
        "palette_colors": palette_colors,
    }


# 与 palettes.NATIVE_HEATMAP_COLORS 同名的 native 色带键
_PALETTE_MAP_NAMED = set(NATIVE_HEATMAP_COLORS)


def _buffer_scientific_evidence(distance: float, unit: str, res: Any) -> Optional[dict]:
    """Build the ADR-0099 ``scientific_evidence`` block for buffer_analysis.

    Reads the REAL CRS transformation note threaded through
    ``buffer_smart`` → ``to_utm_gdf_with_note`` (source frame / auto-UTM
    target / gcj02 normalization via the quality-evidence ``extra``), so
    ``transformations_applied`` reports what actually happened instead of
    re-guessing from the input. Never raises: evidence is additive, a
    descriptor lookup failure must not fail the analysis.
    """
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.scientific_evidence import build_evidence

        descriptor = get_algorithm_registry().get("geometry.buffer")
        if descriptor is None:
            return None
        qev = res.evidence if isinstance(res.evidence, dict) else {}
        source_crs = str(qev.get("source_crs") or "EPSG:4326")
        working_crs = str(qev.get("working_crs") or "")
        transformations: List[str] = []
        if qev.get("gcj02_normalized"):
            transformations.append("gcj02/bd09 偏移框架已归一化为 WGS84（audit #813）")
        if working_crs and working_crs != source_crs:
            transformations.append(
                f"auto UTM ({working_crs}) 用于度量缓冲，结果已转回 {source_crs}")
        elif working_crs:
            transformations.append(
                f"输入已为投影 CRS（{source_crs}），缓冲直接在其线性单位下执行")
        return build_evidence(
            descriptor,
            tool="buffer_analysis",
            parameters_applied={"distance": float(distance), "unit": unit},
            input_facts={
                "feature_count": qev.get("input_count"),
                "crs": source_crs,
                "units": "meters",
            },
            transformations=transformations,
        )
    except Exception:  # noqa: BLE001 — evidence is additive, never fatal
        logger.warning("buffer_analysis: scientific evidence build failed", exc_info=True)
        return None


class BufferAnalysisArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)")
    distance: float = Field(..., gt=0, description="缓冲距离（米），必须大于0")
    unit: str = Field("m", pattern=r"^(m|km)$", description="单位：m/km，默认m")

class HeatmapDataArgs(BaseModel):
    geojson: Any = Field(..., description="输入点要素 GeoJSON 或数据引用(ref:xxx)")
    cell_size: int = Field(500, ge=10, le=5000, description="网格大小（米），范围 10-5000（raster/grid 分析模式用）")
    radius_px: Optional[int] = Field(
        None, ge=4, le=80,
        description="视觉热力半径（MapLibre 屏幕像素，仅 native 渲染）。显式像素语义，"
                    "与米制分析带宽(bandwidth_m)分离；缺省 30px")
    bandwidth_m: Optional[int] = Field(
        None, ge=10, le=10000,
        description="分析密度带宽（米），raster/grid 模式的核平滑半径。定量密度语义")
    radius: Optional[int] = Field(
        None, ge=10, le=10000,
        description="[兼容] 旧搜索半径（米）。会被归一化为 bandwidth_m；native 视觉半径"
                    "在 4-60 历史窗口内直通为像素，否则用默认 30px 并告警。"
                    "新调用请显式用 radius_px/bandwidth_m")
    render_type: Literal["native", "raster", "grid"] = Field(
        "native", description="渲染模式: native(原生逐点密度，默认推荐), raster(服务端栅格PNG), grid(格网)")
    palette: Literal["classic", "magma", "viridis", "thermal"] = Field(
        "classic", description="配色方案: classic, magma, viridis, thermal")
    intensity: Optional[float] = Field(
        None, ge=0.0, le=10.0,
        description="[可选] 热力强度乘数 / 权重强度 (MapLibre heatmap-intensity)，缺省 1.0")
    weight_field: Optional[str] = Field(
        None, description="[可选] 权重字段名称（用于加权热力图计算）")
    weight: Optional[float] = Field(
        None, ge=0.0, description="[可选] 统一要素权重值")
    opacity: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="[可选] 图层透明度，范围 0-1")
    max_zoom: Optional[int] = Field(
        None, ge=0, le=24, description="[可选] 最大显示缩放级别")
    min_zoom: Optional[int] = Field(
        None, ge=0, le=24, description="[可选] 最小显示缩放级别")

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
        if not isinstance(data, dict):
            # Unparseable input must surface as a standard tool error, not a
            # silent empty-FC success (decorator would normalize None → []).
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # ARCH-01 (deep-audit round 3): SpatialAnalysisEngine (a name-dispatch
        # seam ADR-0013 deleted) was removed; call SpatialAnalyzer directly like
        # every other spatial tool.
        # #1110 review M1: forward the full FC — a bare features list is
        # rebuilt into a CRS-less FC and projected input falls back to 4326.
        res = SpatialAnalyzer.buffer(data, distance, unit)
        resp = res.to_llm_response()
        # ADR-0099: attach the scientific evidence block (descriptor
        # geometry.buffer + the real auto-UTM transformation note).
        evidence = _buffer_scientific_evidence(distance, unit, res)
        if evidence is not None:
            resp["scientific_evidence"] = evidence
        return resp

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
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110 review M2: forward the full FC so a declared CRS reaches
        # to_utm_gdf — projected input previously degraded to count-only.
        res = SpatialAnalyzer.statistics(data)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="nearest_neighbor",
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
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110 review M3: forward the full FC (CRS preservation, same as M1).
        res = SpatialAnalyzer.nearest(data)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="heatmap_data",
           description=(
               "点要素热力图（视觉密度表达）。✅ 用于：用户宽泛询问『分布』『热度』"
               "『密度趋势』时的视觉首选——优先 render_type='native' 原生渲染，轻量、"
               "不增加数据负担。半径语义分离：native 视觉热力用 radius_px（屏幕像素，"
               "默认 30）；定量/分析密度（每平方公里、带宽平滑）不是本工具的视觉模式——"
               "raster/grid 模式用 bandwidth_m（米）做核平滑。"
               " 定量护栏：点数 <10（`HEATMAP_MIN_POINTS`，默认 10，点数过少热力图无统计意义）"
               "或几何以线/面为主时禁止 native 热力图，工具侧会确定性拒绝并给出 correction_hint（改用点图/h3_binning）。"
               "\n❌ 不要用于：(1) 需要网格统计值（每格计数/求和）— 用 h3_binning；"
               "(2) 需要矢量等值面用于导出/制图 — 用 kde_contours；"
               "(3) 需要连续概率面做后续叠加分析 — 用 kde_surface；"
               "(4) 『每平方公里密度』等定量密度结论 — 用空间聚合/密度分析，视觉热力图不是定量证据。"
           ),
           args_model=HeatmapDataArgs,
           # #996: raster/grid 路径内部投递 Celery（run_heatmap_generation
           # .apply_async 后 task.get(timeout=120) 同步等结果）——重工具显式
           # 标 heavy + 显式墙钟预算（120s 任务等待 + 与原默认 300s 等量的
           # 进程内回退余量，不因显式化而收紧）。
           cost="heavy", timeout=300.0)
    @cached_tool(ttl=3600)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: Optional[int] = None,
                     render_type: str = "native", palette: str = "classic",
                     radius_px: Optional[int] = None, bandwidth_m: Optional[int] = None,
                     intensity: Optional[float] = None, weight_field: Optional[str] = None,
                     weight: Optional[float] = None, opacity: Optional[float] = None,
                     max_zoom: Optional[int] = None, min_zoom: Optional[int] = None,
                     **kwargs: Any) -> dict:
        from app.lib.cartography.heatmap_contract import normalize_heatmap_radius
        # 单位归一化唯一边界：legacy radius(米) → 显式 bandwidth_m(+视觉默认)，
        # 核心链路此后只消费显式字段，不再猜测单位。
        contract = normalize_heatmap_radius(
            radius_px=radius_px, bandwidth_m=bandwidth_m, legacy_radius=radius,
        )
        data = safe_parse_geojson(geojson)
        if not data:
            raise ValueError("Invalid GeoJSON input")
        features = data.get("features") or data.get("feature_collection", [])

        # #690: native 路径确定性守卫（小样本/非点几何）— 与 converter 同阈值
        if render_type == "native":
            try:
                from app.core.config import settings as _settings
                threshold = max(1, int(getattr(_settings, "HEATMAP_MIN_POINTS", 10)))
            except Exception:
                threshold = 10
            # Infer dominant geometry category from features (not shell `data` type)
            _counts = {"point": 0, "line": 0, "polygon": 0}
            for _f in features:
                if not isinstance(_f, dict):
                    continue
                _g = _f.get("geometry")
                if not isinstance(_g, dict):
                    continue
                _gt = _g.get("type")
                if _gt in ("Point", "MultiPoint"):
                    _counts["point"] += 1
                elif _gt in ("LineString", "MultiLineString"):
                    _counts["line"] += 1
                elif _gt in ("Polygon", "MultiPolygon"):
                    _counts["polygon"] += 1
            _active = [k for k, v in _counts.items() if v > 0]
            _geom_cat = max(_counts, key=lambda c: _counts[c]) if _active else "point"
            if _geom_cat != "point":
                return std_error_response(
                    f"原生热力图仅支持点要素，当前以{_geom_cat}为主",
                    code="INVALID_GEOMETRY_FOR_HEATMAP",
                    correction_hint="改用点图(circle)或先将线/面聚合为点/网格（h3_binning）后再做密度分析。",
                )
            if len(features) < threshold:
                return std_error_response(
                    f"点数{len(features)}<阈值{threshold}，原生热力图无统计意义",
                    code="INSUFFICIENT_POINTS_FOR_HEATMAP",
                    correction_hint=f"当前仅{len(features)}点，建议直接用点图(circle)展示或先聚合(h3_binning)，样本≥{threshold}时再用热力图。",
                )

        # 默认 native：MapLibre 逐点核密度渲染（轻量、密度真实）。raster 是
        # 服务端预渲染 PNG，仅在需要导出图片/离线渲染时显式指定。
        if render_type == "native":
            if isinstance(data, dict):
                # #990: safe_parse_geojson 透传 dict 时可能原样返回共享只读
                # payload 本体（#874 get_shared 零拷贝契约：工具不得就地改
                # payload）。native 元数据只写进顶层浅拷贝，原 ref payload /
                # 会话存储不被污染，同 ref 的并发调用也互不可见。
                data = dict(data)
                data["command"] = "add_native_heatmap"
                # type_hint 驱动 dispatch 的 MapSpec 授权把点要素结果落成
                # type=heatmap 图层（默认推断是 circle —— 热力图从未挂上的
                # 根因），metadata 供授权方生成官方范式 paint（zoom 插值
                # radius/intensity + 密度多停靠点色带）。
                data["type_hint"] = "heatmap"
                # 热力半径契约：显式 radius_px（像素）+ bandwidth_m（米）。
                # legacy radius 归一化结果经 source/warnings 显式可审计；
                # 消费方（converter/前端）不再解读模糊单位。
                radius_meta = contract.to_metadata()
                if contract.bandwidth_m is not None:
                    # 旧前端只读 meta.radius —— 保持米值回显（旧启发式会回落
                    # 默认，与新契约一致），新前端优先 radius_px。
                    radius_meta["radius"] = contract.bandwidth_m
                meta_dict = {
                    "render_type": "native",
                    "point_count": len(features),
                    "palette": palette,
                    **radius_meta,
                }
                if intensity is not None:
                    meta_dict["intensity"] = intensity
                if weight_field is not None:
                    meta_dict["weight_field"] = weight_field
                if weight is not None:
                    meta_dict["weight"] = weight
                if opacity is not None:
                    meta_dict["opacity"] = opacity
                if max_zoom is not None:
                    meta_dict["max_zoom"] = max_zoom
                if min_zoom is not None:
                    meta_dict["min_zoom"] = min_zoom
                data["metadata"] = meta_dict
                # Generate legend_spec for native mode so the frontend can
                # show a color gradient legend alongside the heatmap layer.
                # 图例色与前端 heatmap-color 停靠点同源（palettes.NATIVE_HEATMAP_COLORS）。
                try:
                    from app.lib.cartography.palettes import heatmap_legend_colors
                    data["legend_spec"] = _build_legend_spec(
                        palette,
                        colors=heatmap_legend_colors(palette),
                    )
                except Exception as e:
                    logger.warning(f"[heatmap_data] legend_spec generation failed: {e}")
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                data = trim_features(data)
            return data

        # raster/grid：分析密度路径，带宽按米消费（sigma = bandwidth / cell_size）
        bandwidth = contract.bandwidth_m
        # P3-3: the density grid math is WGS84-degree based — forward the
        # declared CRS (from the parsed FC) so projected input is reprojected
        # instead of silently consumed as degrees.
        from app.lib.geo_processor.core import extract_declared_crs
        declared_crs = extract_declared_crs(data)
        try:
            from app.services.spatial_tasks import run_heatmap_generation
            task = run_heatmap_generation.apply_async(
                kwargs={"features": features, "cell_size": cell_size, "radius": bandwidth, "render_type": render_type, "palette": palette, "declared_crs": declared_crs}
            )
            result = task.get(timeout=120)
        except Exception as exc:  # noqa: BLE001
            # Celery unavailable or task failed — fall back to in-process computation.
            # Any Celery failure (ImportError, broker down, TimeoutError, WorkerLostError)
            # degrades gracefully to an in-process fallback.
            logger.warning(f"[heatmap_data] Celery fallback triggered: {type(exc).__name__}: {exc}")
            result = generate_heatmap_raster(features, cell_size, bandwidth, render_type, palette, declared_crs)
        
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
