"""
Analysis -> Cartography Converter Service.
Converts spatial analysis results (GeoAnalysisResult output) into MapSpec layer specifications.
"""
from collections import Counter
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

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

DEFAULT_CONSTANT_PAINTS = {
    "circle": {"color": "#3b82f6", "radius": 5},
    "line": {"color": "#2563eb", "width": 2},
    "fill": {"color": "#3b82f6", "opacity": 0.6},
    "heatmap": {"color": "#d97706", "radius": 10},
}


def is_analysis_result(source_data: Any) -> bool:
    """
    Returns True if source_data is a spatial Analysis Result dict.
    Differentiates from plain GeoJSON dicts or string URLs/paths/refs.
    Detection order:
    1. Not dict -> False
    2. Explicit analysis metadata -> True
    3. GeoJSON dict -> False
    4. Wrapped 'data' payload -> True
    """
    if not isinstance(source_data, dict):
        return False

    analysis_keys = {"legend_spec", "algorithm", "analysis_type", "source_ref"}
    if any(k in source_data for k in analysis_keys):
        return True

    top_type = source_data.get("type")
    if top_type in GEOJSON_TYPES or "features" in source_data:
        return False

    if "data" in source_data:
        return True

    return False


def _extract_geojson(analysis_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract GeoJSON FeatureCollection/Feature dict from analysis_result."""
    if "data" in analysis_result:
        data = analysis_result["data"]
        if isinstance(data, dict):
            data_type = data.get("type")
            if data_type in GEOJSON_TYPES or "features" in data:
                return data
            return None

    top_type = analysis_result.get("type")
    if top_type in GEOJSON_TYPES or "features" in analysis_result:
        return analysis_result

    return None


def _infer_geometry_category(geojson: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """
    Inspects GeoJSON features to infer primary geometry category ('point', 'line', 'polygon').
    Returns (primary_category, list_of_warnings).
    """
    warnings: List[str] = []
    if not geojson or not isinstance(geojson, dict):
        warnings.append("no_geometries: analysis result contains no valid GeoJSON features")
        return "point", warnings

    features: List[Dict[str, Any]] = []
    if geojson.get("type") == "FeatureCollection":
        features = geojson.get("features", [])
    elif geojson.get("type") == "Feature":
        features = [geojson]
    elif "features" in geojson:
        features = geojson.get("features", [])

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

    unique_types = sorted(list(set(geom_types)))
    active_categories = [cat for cat in ["point", "line", "polygon"] if counts[cat] > 0]

    if len(active_categories) > 1:
        warnings.append(f"mixed_geometries: source contains multiple geometry types ({unique_types})")

    majority_cat = max(counts.keys(), key=lambda c: counts[c])
    return majority_cat, warnings


def convert_analysis_to_mapspec_layer(
    analysis_result: Dict[str, Any],
    layer: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[str]]:
    """
    Converts an Analysis Result dict into a MapSpec layer specification and inline GeoJSON data.
    """
    warnings: List[str] = []
    base_layer = dict(layer) if layer else {}

    inline_geojson = _extract_geojson(analysis_result)
    geom_cat, geom_warnings = _infer_geometry_category(inline_geojson)
    warnings.extend(geom_warnings)

    cat_to_layer_type = {
        "point": "circle",
        "line": "line",
        "polygon": "fill",
    }
    inferred_layer_type = cat_to_layer_type.get(geom_cat, "circle")

    type_hint = analysis_result.get("type_hint") or base_layer.get("type_hint")
    if type_hint == "heatmap":
        inferred_layer_type = "heatmap"

    layer_type = base_layer.get("type") or inferred_layer_type

    legend_spec = analysis_result.get("legend_spec")
    if not legend_spec and isinstance(analysis_result.get("data"), dict):
        legend_spec = analysis_result["data"].get("legend_spec")

    paint_color: Any = None
    has_thematic_paint = False

    if isinstance(legend_spec, dict):
        legend_type = legend_spec.get("type")
        if legend_type == "graduated":
            field = legend_spec.get("field", "")
            breaks = legend_spec.get("breaks", [])
            palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

            if (
                field
                and isinstance(breaks, list)
                and len(breaks) >= 2
                and isinstance(palette_colors, list)
                and len(palette_colors) >= 1
            ):
                default_color = palette_colors[0]
                stops = []
                for i in range(1, len(breaks) - 1):
                    color_i = palette_colors[i] if i < len(palette_colors) else palette_colors[-1]
                    stops.append([breaks[i], color_i])

                paint_color = {
                    "method": "step",
                    "field": field,
                    "default": default_color,
                    "stops": stops,
                }
                has_thematic_paint = True
            else:
                warnings.append("graduated_legend_invalid: insufficient breaks or palette_colors")

        elif legend_type == "continuous":
            field = legend_spec.get("field", "")
            min_val = legend_spec.get("min")
            max_val = legend_spec.get("max")
            palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

            if (
                field
                and isinstance(min_val, (int, float))
                and isinstance(max_val, (int, float))
                and isinstance(palette_colors, list)
                and len(palette_colors) >= 1
            ):
                n = len(palette_colors)
                if n == 1:
                    stops = [[float(min_val), palette_colors[0]], [float(max_val), palette_colors[0]]]
                else:
                    step = (float(max_val) - float(min_val)) / (n - 1) if max_val != min_val else 0.0
                    stops = [
                        [round(float(min_val) + i * step, 6), palette_colors[i]]
                        for i in range(n)
                    ]

                paint_color = {
                    "method": "interpolate",
                    "field": field,
                    "stops": stops,
                }
                has_thematic_paint = True
            else:
                warnings.append("continuous_legend_invalid: missing field, min, max, or palette_colors")

        elif legend_type == "categorical":
            field = legend_spec.get("field", "")
            categories = legend_spec.get("categories", [])

            if field and isinstance(categories, list) and len(categories) >= 1:
                cases = []
                for cat in categories:
                    if isinstance(cat, dict):
                        key = cat.get("key")
                        color = cat.get("color", "#999999")
                        if key is not None:
                            cases.append([key, color])

                if cases:
                    default_color = cases[-1][1]
                    paint_color = {
                        "method": "match",
                        "field": field,
                        "cases": cases,
                        "default": default_color,
                    }
                    has_thematic_paint = True
                else:
                    warnings.append("categorical_legend_invalid: no valid category entries")
            else:
                warnings.append("categorical_legend_invalid: missing field or categories")

    default_paint_defaults = DEFAULT_CONSTANT_PAINTS.get(layer_type, {"color": "#3b82f6"})
    if paint_color is None:
        paint_color = default_paint_defaults.get("color", "#3b82f6")

    existing_paint = base_layer.get("paint")
    paint = dict(default_paint_defaults)
    if isinstance(existing_paint, dict):
        paint.update(existing_paint)

    if has_thematic_paint or "color" not in paint:
        paint["color"] = paint_color

    algorithm = (
        analysis_result.get("algorithm")
        or analysis_result.get("analysis_type")
        or "spatial_analysis"
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

    provenance = {
        "algorithm": algorithm,
        "source_ref": source_ref,
        "params": params,
        "computed_at": computed_at,
    }
    if warnings:
        provenance["warnings"] = warnings

    layer_id = base_layer.get("id")
    if not layer_id:
        algo_slug = str(algorithm).lower().replace("-", "_")
        layer_id = f"{algo_slug}_layer"

    source_id = base_layer.get("source")
    if not source_id:
        source_id = f"{layer_id}_source"

    res_layer = dict(base_layer)
    res_layer["id"] = layer_id
    res_layer["source"] = source_id
    res_layer["type"] = layer_type
    res_layer["paint"] = paint
    res_layer["provenance"] = provenance

    return res_layer, inline_geojson, warnings
