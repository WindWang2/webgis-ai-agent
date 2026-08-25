"""
Analysis -> Cartography Converter Service.
Converts spatial analysis results (GeoAnalysisResult output) into MapSpec layer specifications.
"""
from collections import Counter
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.thematic_spec import spec_to_paint

logger = logging.getLogger(__name__)

GEOJSON_TYPES = {
    "FeatureCollection",
    "Feature",
    "Point",
    "LineString",
    "Polygon",
    "MultiPoint",
    "MultiLineString",
    "MultiPolygon",
    "GeometryCollection",
}

DEFAULT_CONSTANT_COLOR = "#3b82f6"

DEFAULT_CONSTANT_PAINTS = {
    "circle": {"color": "#3b82f6", "radius": 5},
    "line": {"color": "#2563eb", "width": 2},
    # 面层默认不透明度 0.4（2026-08-26 用户反馈"行政区图层颜色太深遮挡下面
    # 内容"）：0.6 叠加饱和色带会把底图道路与下层层内容整片盖死；0.4 保持
    # 分级可辨的同时透出底图。该值经 runtime_patch.opacity 同步到 HUD 呈现，
    # 是专题面层的唯一权威默认（用户仍可用不透明度滑杆覆盖）。
    "fill": {"color": "#3b82f6", "opacity": 0.4},
    "heatmap": {"color": "#d97706", "radius": 10},
}

# 行政边界/区划面类工具的产出是"参考语境层"——填面只会盖住底图与数据层
# （2026-08-26 用户反馈"行政区图层只显示边缘，不要显示面对象"）。这些层
# 直接以线图层渲染多边形边界：fill-outline-color 恒为 1px 发丝线不可加粗，
# line-width 可调（用户随后要求"边缘更粗一点"，取 2.5px）。分级专题
# （choropleth）不在此列——填面即语义。
_BOUNDARY_OUTLINE_ALGORITHMS = {
    "get_local_admin_boundary",
    "get_local_child_districts",
}
# 级别 → 边界线宽（国界最粗、区县界最细；2026-08-26 用户要求按行政区
# 级别区分粗细）。admin_level 由 local_admin 工具写入结果 metadata 下传。
_ADMIN_BOUNDARY_LINE_WIDTH = {
    "country": 4.0,
    "province": 3.5,
    "city": 3.0,
    "district": 2.0,
}
_BOUNDARY_LINE_WIDTH_DEFAULT = 2.5


# ─── small shared helpers (extracted to kill duplicated logic shape) ────────


def _slugify(name: str) -> str:
    """Sanitize name string for layer/source IDs."""
    slug = re.sub(r"[^a-zA-Z0-9_]", "", str(name).lower().replace(" ", "_").replace("-", "_"))
    return slug or "layer"


def _looks_like_geojson(data: Any) -> bool:
    """True if data is a dict shaped like a GeoJSON object/geometry/feature collection."""
    if not isinstance(data, dict):
        return False
    return data.get("type") in GEOJSON_TYPES or "features" in data


def _default_color(layer_type: str) -> str:
    """Resolve the constant default color for a layer type."""
    return DEFAULT_CONSTANT_PAINTS.get(layer_type, {"color": DEFAULT_CONSTANT_COLOR}).get(
        "color", DEFAULT_CONSTANT_COLOR
    )


def _iter_features(geojson: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize a GeoJSON object into its list of Feature dicts."""
    if not isinstance(geojson, dict):
        return []
    if geojson.get("type") == "FeatureCollection" or "features" in geojson:
        return geojson.get("features", [])
    if geojson.get("type") == "Feature":
        return [geojson]
    return []


def _build_layer(
    base_layer: Dict[str, Any],
    layer_id: str,
    source_id: str,
    layer_type: str,
    paint: Dict[str, Any],
    provenance: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble a MapSpec layer dict from its resolved parts (single construction site)."""
    res_layer = dict(base_layer)
    res_layer["id"] = layer_id
    res_layer["source"] = source_id
    res_layer["type"] = layer_type
    res_layer["paint"] = paint
    res_layer["provenance"] = provenance
    return res_layer


# ─── branch detection & geometry inference ─────────────────────────────────


def is_analysis_result(source_data: Any) -> bool:
    """
    Returns True if source_data is a spatial Analysis Result dict.
    Differentiates from plain GeoJSON dicts or string URLs/paths/refs.

    Detection priority follows the spec contract:
      1. Not a dict -> False
      2. GeoJSON-shaped dict -> False (GeoJSON wins over analysis markers;
         a FeatureCollection carrying a stray top-level `algorithm`/`source_ref`
         key is still GeoJSON, not an analysis result)
      3. Dict carrying an analysis marker (`legend_spec`/`algorithm`/
         `analysis_type`/`source_ref`) OR a wrapped `data` payload -> True
      4. Otherwise -> False
    """
    if not isinstance(source_data, dict):
        return False

    # GeoJSON shape wins: a plain FeatureCollection/Feature is never an analysis result,
    # even if it happens to carry a top-level marker key.
    if _looks_like_geojson(source_data):
        return False

    analysis_keys = {"legend_spec", "algorithm", "analysis_type", "source_ref"}
    if any(k in source_data for k in analysis_keys):
        return True

    if "data" in source_data:
        return True

    return False


def _get_heatmap_min_points() -> int:
    """#690: read HEATMAP_MIN_POINTS from settings, clamped to >=1."""
    try:
        from app.core.config import settings as _s  # lazy import to avoid cycle
        raw = getattr(_s, "HEATMAP_MIN_POINTS", 10)
        return max(1, int(raw))
    except Exception:
        return 10


def _extract_geojson(analysis_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract GeoJSON FeatureCollection/Feature dict from analysis_result."""
    if "data" in analysis_result:
        data = analysis_result["data"]
        if _looks_like_geojson(data):
            return data
        return None
    # #690: dispatch payload uses `geojson` key (see _author_display_result)
    if "geojson" in analysis_result:
        data = analysis_result["geojson"]
        if isinstance(data, dict) and _looks_like_geojson(data):
            return data
        return None

    if _looks_like_geojson(analysis_result):
        return analysis_result

    return None


def _infer_geometry_category(geojson: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """
    Inspects GeoJSON features to infer primary geometry category ('point', 'line', 'polygon').
    Returns (primary_category, list_of_warnings).
    """
    warnings: List[str] = []
    features = _iter_features(geojson) if geojson else []

    if not features:
        warnings.append("no_geometries: analysis result contains no valid GeoJSON features")
        return "point", warnings

    geom_types: List[str] = []
    counts: Counter = Counter()

    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not geom or not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        if not gtype:
            continue
        geom_types.append(gtype)
        if gtype in ("Point", "MultiPoint"):
            counts["point"] += 1
        elif gtype in ("LineString", "MultiLineString"):
            counts["line"] += 1
        elif gtype in ("Polygon", "MultiPolygon"):
            counts["polygon"] += 1

    if not geom_types or not counts:
        warnings.append("no_geometries: analysis result contains no valid GeoJSON features")
        return "point", warnings

    unique_types = sorted(set(geom_types))
    active_categories = [cat for cat in ["point", "line", "polygon"] if counts[cat] > 0]

    if len(active_categories) > 1:
        warnings.append(f"mixed_geometries: source contains multiple geometry types ({unique_types})")

    majority_cat = max(counts.keys(), key=lambda c: counts[c])
    return majority_cat, warnings


# ─── legend_spec -> StyleMethod paint.color (single projection) ─────────────
# Paint derivation delegates to ``thematic_spec.spec_to_paint`` — the ONE
# construction site shared with the frontend adapter and the semantic checks
# (ADR-0052). Keeping a single projection is what guarantees the live map's
# paint and the legend can never diverge: both are deterministic functions of
# the same canonical ``legend_spec``.


def _category_from_geometry_types(geometry_types: Any) -> Optional[str]:
    """#688 收尾：descriptor 的 geometry_types → 几何主类别（零遍历）。

    与 _infer_geometry_category 同一映射；types 缺失/空/不可解析时返回
    None，调用方降级为对 FC 自行推断（向后兼容无 profile 的调用方）。
    """
    if not isinstance(geometry_types, (list, tuple)):
        return None
    has_point = has_line = has_polygon = False
    for t in geometry_types:
        if not isinstance(t, str):
            continue
        if "Point" in t:
            has_point = True
        elif "LineString" in t:
            has_line = True
        elif "Polygon" in t:
            has_polygon = True
    if has_polygon:
        return "polygon"
    if has_line:
        return "line"
    if has_point:
        return "point"
    return None


def _resolve_paint_color(
    legend_spec: Any, layer_type: str
) -> Tuple[Any, bool, List[str]]:
    """Resolve paint.color from a legend_spec.

    Returns (paint_color, has_thematic_paint, warnings). When the legend_spec
    is absent/malformed, paint_color stays None and the caller applies the
    constant default.
    """
    paint_color, warnings = spec_to_paint(legend_spec, _default_color(layer_type))
    return paint_color, paint_color is not None, warnings


# ─── main entry point ──────────────────────────────────────────────────────


def convert_analysis_to_mapspec_layer(
    analysis_result: Dict[str, Any],
    layer: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[str]]:
    """
    Converts an Analysis Result dict into a MapSpec layer specification and inline GeoJSON data.
    Guaranteed best-effort execution (never raises unhandled exceptions).
    """
    warnings: List[str] = []
    base_layer = dict(layer) if isinstance(layer, dict) else {}

    try:
        if not isinstance(analysis_result, dict):
            warnings.append("invalid_analysis_result: input is not a dictionary")
            analysis_result = {}

        inline_geojson = _extract_geojson(analysis_result)
        # #688 收尾：授权路径传入 descriptor 派生的 profile 时，几何类别与
        # 点数零遍历可得（featureCount/geometryTypes 在 store 时已算好）；
        # 无 profile 才对 FC 自行推断（其他调用方向后兼容）。
        raw_profile = analysis_result.get("profile")
        profile = raw_profile if isinstance(raw_profile, dict) else None
        geom_cat = _category_from_geometry_types(profile.get("geometryTypes")) if profile else None
        if geom_cat is not None:
            geom_warnings = []
        else:
            geom_cat, geom_warnings = _infer_geometry_category(inline_geojson)
            warnings.extend(geom_warnings)

        cat_to_layer_type = {
            "point": "circle",
            "line": "line",
            "polygon": "fill",
        }
        inferred_layer_type = cat_to_layer_type.get(geom_cat, "circle")

        type_hint = analysis_result.get("type_hint") or base_layer.get("type_hint")
        # #690: deterministic guard — do not flip to heatmap when unsuitable
        heatmap_guard_triggered = False
        heatmap_guard_reason = ""
        if type_hint == "heatmap":
            threshold = _get_heatmap_min_points()
            # Prefer explicit metadata point_count (heatmap_data sets it), fall back to
            # actual GeoJSON feature count; geometry guard uses inferred category.
            meta = analysis_result.get("metadata") or {}
            hint_count = meta.get("point_count")
            if not (isinstance(hint_count, int) and hint_count >= 0) and profile:
                hint_count = profile.get("featureCount")
            if isinstance(hint_count, int) and hint_count >= 0:
                point_count = hint_count
            else:
                point_count = len(_iter_features(inline_geojson)) if inline_geojson else 0
            if geom_cat != "point":
                heatmap_guard_triggered = True
                heatmap_guard_reason = f"非点几何({geom_cat})不适合原生热力图"
            elif point_count < threshold:
                heatmap_guard_triggered = True
                heatmap_guard_reason = f"点数{point_count}<阈值{threshold}，热力图无统计意义"
            if heatmap_guard_triggered:
                correction = (
                    f"已拦截原生热力图（{heatmap_guard_reason}）"
                    f"，已回退为点图(circle)展示；"
                    f"如需密度趋势请先聚合(h3_binning)或增大样本。"
                )
                warnings.append(f"heatmap_guard: {heatmap_guard_reason}; {correction}")
            else:
                inferred_layer_type = "heatmap"

        layer_type = base_layer.get("type") or inferred_layer_type
        # Guard overrides: never emit heatmap layer when guard triggered
        if heatmap_guard_triggered and layer_type == "heatmap":
            layer_type = "circle"
        # 行政边界/区划面 → 线图层（边界线宽可调；填面恒被拒）。
        if (
            str(analysis_result.get("algorithm") or "") in _BOUNDARY_OUTLINE_ALGORITHMS
            and not base_layer.get("type")
        ):
            layer_type = "line"

        # All legend-bearing emitters attach legend_spec at the top level of their
        # result dict (h3_binning, kde_contours, heatmap_data, apply_template,
        # create_thematic_map). An earlier `data`-inner fallback existed for
        # heatmap_data, but heatmap_data's output is a FeatureCollection shape,
        # which is_analysis_result rejects ("GeoJSON wins") before this converter
        # runs — so the fallback was unreachable dead code. See ADR-0015.
        legend_spec = analysis_result.get("legend_spec")

        paint_color, has_thematic_paint, legend_warnings = _resolve_paint_color(legend_spec, layer_type)
        warnings.extend(legend_warnings)

        if layer_type == "heatmap":
            # 原生热力图层：官方 create-a-heatmap-layer 范式（zoom 插值
            # radius/intensity + 密度多停靠点色带，首段透明）。不走
            # circle/fill 的 color 语义——heatmap paint 键是 MapLibre 原生
            # 表达式，图例一致性由 palettes.NATIVE_HEATMAP_COLORS 单一色源保证。
            from app.lib.cartography.heatmap_contract import resolve_paint_radius_px
            from app.lib.cartography.palettes import heatmap_paint
            meta = analysis_result.get("metadata") or {}
            params_meta = analysis_result.get("params") or {}
            radius_meta = {
                k: v for k, v in (
                    *meta.items(), *params_meta.items()
                ) if k in ("radius_px", "bandwidth_m", "radius")
            }
            # 半径契约：显式 radius_px 优先；legacy radius 经唯一归一化边界
            # （heatmap_contract）解析——米值（如 1000/2000）绝不会再被当作
            # 像素消费，超历史窗口回落默认 30px 并记录迁移警示。
            radius_contract = resolve_paint_radius_px(radius_meta)
            paint = heatmap_paint(
                meta.get("palette") or params_meta.get("palette") or "classic",
                radius_contract.radius_px,
            )
            for warn in radius_contract.warnings:
                warnings.append(f"heatmap_radius_contract: {warn}")
            existing_paint = base_layer.get("paint")
            if isinstance(existing_paint, dict):
                paint.update(existing_paint)
        else:
            default_paint = dict(DEFAULT_CONSTANT_PAINTS.get(layer_type, {"color": DEFAULT_CONSTANT_COLOR}))
            if not has_thematic_paint:
                paint_color = _default_color(layer_type)

            existing_paint = base_layer.get("paint")
            paint = dict(default_paint)
            if isinstance(existing_paint, dict):
                paint.update(existing_paint)

            if has_thematic_paint or "color" not in paint:
                paint["color"] = paint_color

        algorithm = (
            analysis_result.get("algorithm")
            or analysis_result.get("analysis_type")
            or "spatial_analysis"
        )
        if (
            layer_type == "line"
            and not has_thematic_paint
            and str(algorithm) in _BOUNDARY_OUTLINE_ALGORITHMS
        ):
            meta = analysis_result.get("metadata")
            admin_level = (
                meta.get("admin_level") if isinstance(meta, dict) else None
            )
            paint["width"] = _ADMIN_BOUNDARY_LINE_WIDTH.get(
                str(admin_level or ""), _BOUNDARY_LINE_WIDTH_DEFAULT
            )
        source_ref = analysis_result.get("source_ref")
        params = analysis_result.get("params", {})
        computed_at = analysis_result.get("computed_at") or datetime.now(timezone.utc).isoformat()

        raw_warnings = analysis_result.get("warnings", [])
        if isinstance(raw_warnings, list):
            for w in raw_warnings:
                sw = str(w)
                if sw not in warnings:
                    warnings.append(sw)
        elif raw_warnings:
            warnings.append(str(raw_warnings))

        unique_warnings = list(dict.fromkeys(warnings))
        if unique_warnings:
            logger.warning("Analysis cartography converter emitted warnings: %s", unique_warnings)

        provenance = {
            "algorithm": algorithm,
            "source_ref": source_ref,
            "params": params,
            "computed_at": computed_at,
        }
        # ``source_ref`` identifies the analysis input; ``result_ref`` is the
        # distinct, session-owned output that the map actually renders.  Keep
        # both so provenance checks cannot let an input dataset satisfy an
        # output-to-map binding.
        result_ref = analysis_result.get("result_ref")
        if isinstance(result_ref, str) and result_ref.startswith("ref:"):
            provenance["result_ref"] = result_ref
        if unique_warnings:
            provenance["warnings"] = unique_warnings

        layer_id = base_layer.get("id") or f"{_slugify(algorithm)}_layer"
        source_id = base_layer.get("source") or f"{layer_id}_source"

        res_layer = _build_layer(base_layer, layer_id, source_id, layer_type, paint, provenance)
        if layer_type == "heatmap":
            # 热力半径契约记录（diffable/auditable）：runtime/compiler 只转发
            # id/type/source/paint/layout/filter，此兄弟键与 legend_spec 同一
            # 先例——不进 MapLibre paint，但让 MapSpec 上的单位语义可审计。
            res_layer["heatmap"] = radius_contract.to_metadata()
        # #690: correction_hint accompanies the fallback when heatmap was denied
        if heatmap_guard_triggered:
            res_layer["correction_hint"] = (
                f"已拦截原生热力图（{heatmap_guard_reason}），"
                f"已回退为点图(circle)；如需密度趋势请用 h3_binning 聚合或增大样本。"
            )
        # ADR-0052: attach the canonical legend_spec onto the output layer so the
        # cartography semantic checks can verify paint ↔ legend equivalence on
        # the SAME MapSpec (previously the vector path dropped legend_spec while
        # the raster path kept it — an asymmetry that made drift undetectable).
        # Extra keys are ignored by the compiler/runtime (they forward only
        # id/type/source/paint/layout/filter to MapLibre).
        if isinstance(legend_spec, dict):
            res_layer["legend_spec"] = legend_spec
        # 行政边界层 = 参考语境层：默认常显（制图语境），前端 finalize 收口
        # 与「地图随对话」主题切换豁免之（context_role 兄弟键先例同上）。
        if (
            layer_type == "line"
            and str(algorithm) in _BOUNDARY_OUTLINE_ALGORITHMS
        ):
            res_layer["context_role"] = "boundary"
        return res_layer, inline_geojson, unique_warnings

    except Exception as e:
        logger.exception("Analysis to MapSpec converter encountered unhandled exception: %s", e)
        err_msg = f"converter_error: {str(e)}"
        unique_warnings = list(dict.fromkeys(warnings + [err_msg]))

        algorithm = (
            analysis_result.get("algorithm")
            if isinstance(analysis_result, dict)
            else base_layer.get("algorithm", "spatial_analysis")
        ) or "spatial_analysis"

        layer_id = base_layer.get("id") or f"{_slugify(algorithm)}_layer"
        source_id = base_layer.get("source") or f"{layer_id}_source"
        layer_type = base_layer.get("type", "circle")

        default_paint = dict(DEFAULT_CONSTANT_PAINTS.get(layer_type, {"color": DEFAULT_CONSTANT_COLOR}))
        existing_paint = base_layer.get("paint")
        if isinstance(existing_paint, dict):
            default_paint.update(existing_paint)

        provenance = {
            "algorithm": algorithm,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "warnings": unique_warnings,
        }
        res_layer = _build_layer(base_layer, layer_id, source_id, layer_type, default_paint, provenance)
        return res_layer, None, unique_warnings
