"""
Analysis -> Cartography Converter Service.
Converts spatial analysis results (GeoAnalysisResult output) into MapSpec layer specifications.
"""
from collections import Counter
from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    "fill": {"color": "#3b82f6", "opacity": 0.6},
    "heatmap": {"color": "#d97706", "radius": 10},
}


# ─── small shared helpers (extracted to kill duplicated logic shape) ────────


def _is_number(val: Any) -> bool:
    """Check if val is a valid finite number (int/float, excluding bool and NaN/Inf)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


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


def _extract_geojson(analysis_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract GeoJSON FeatureCollection/Feature dict from analysis_result."""
    if "data" in analysis_result:
        data = analysis_result["data"]
        if _looks_like_geojson(data):
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


# ─── legend_spec -> StyleMethod converters ─────────────────────────────────
# Each returns (style_method_or_None, warnings). None signals "fall back to constant".


def _convert_graduated_legend(legend_spec: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Converts a graduated legend_spec into a step StyleMethod."""
    warnings: List[str] = []
    field = legend_spec.get("field", "")
    breaks = legend_spec.get("breaks", [])
    palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

    valid_breaks = isinstance(breaks, list) and len(breaks) >= 2 and all(_is_number(b) for b in breaks)
    valid_colors = isinstance(palette_colors, list) and len(palette_colors) >= 1

    if field and valid_breaks and valid_colors:
        default_color = palette_colors[0]
        stops = []
        for i in range(1, len(breaks) - 1):
            color_i = palette_colors[i] if i < len(palette_colors) else palette_colors[-1]
            stops.append([float(breaks[i]), color_i])

        return {
            "method": "step",
            "field": field,
            "default": default_color,
            "stops": stops,
        }, warnings

    warnings.append("graduated_legend_invalid: insufficient breaks or palette_colors")
    return None, warnings


def _convert_continuous_legend(legend_spec: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Converts a continuous legend_spec into an interpolate StyleMethod."""
    warnings: List[str] = []
    field = legend_spec.get("field", "")
    min_val = legend_spec.get("min")
    max_val = legend_spec.get("max")
    palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

    if (
        field
        and _is_number(min_val)
        and _is_number(max_val)
        and float(min_val) < float(max_val)
        and isinstance(palette_colors, list)
        and len(palette_colors) >= 2
    ):
        n = len(palette_colors)
        step = (float(max_val) - float(min_val)) / (n - 1)
        stops = [
            [round(float(min_val) + i * step, 6), palette_colors[i]]
            for i in range(n)
        ]

        return {
            "method": "interpolate",
            "field": field,
            "stops": stops,
        }, warnings

    warnings.append("continuous_legend_invalid: missing field, palette_colors (min 2), or min must be strictly less than max")
    return None, warnings


def _convert_categorical_legend(
    legend_spec: Dict[str, Any], default_fallback: str = "#999999"
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Converts a categorical legend_spec into a match StyleMethod.

    Per the legend_spec contract the categorical variant carries only
    `categories: [{key, color, label}]`; the match default is the last
    category color (falling back to `default_fallback` if a category lacks one).
    """
    warnings: List[str] = []
    field = legend_spec.get("field", "")
    categories = legend_spec.get("categories", [])

    if field and isinstance(categories, list) and len(categories) >= 1:
        cases = []
        for cat in categories:
            if isinstance(cat, dict):
                key = cat.get("key")
                color = cat.get("color")
                if key is not None and color:
                    cases.append([key, color])
            elif isinstance(cat, (list, tuple)) and len(cat) >= 2:
                cases.append([cat[0], cat[1]])

        if cases:
            # Spec contract: default = last category color.
            default_color = cases[-1][1] or default_fallback
            return {
                "method": "match",
                "field": field,
                "cases": cases,
                "default": default_color,
            }, warnings

        warnings.append("categorical_legend_invalid: no valid category entries")
        return None, warnings

    warnings.append("categorical_legend_invalid: missing field or categories")
    return None, warnings


# Dispatch table: drives the legend arms from one map so the converter body
# doesn't repeat the same convert→extend→flag shape per type.
_LEGEND_CONVERTERS: Dict[str, Callable[[Dict[str, Any], str], Tuple[Optional[Dict[str, Any]], List[str]]]] = {
    "graduated": lambda spec, fallback: _convert_graduated_legend(spec),
    "continuous": lambda spec, fallback: _convert_continuous_legend(spec),
    "categorical": _convert_categorical_legend,
}


def _resolve_paint_color(
    legend_spec: Any, layer_type: str
) -> Tuple[Any, bool, List[str]]:
    """Resolve paint.color from a legend_spec.

    Returns (paint_color, has_thematic_paint, warnings). When the legend_spec
    is absent/malformed, paint_color stays None and the caller applies the
    constant default.
    """
    warnings: List[str] = []
    if legend_spec is None:
        return None, False, warnings
    if not isinstance(legend_spec, dict):
        warnings.append("invalid_legend_spec: legend_spec must be a dictionary")
        return None, False, warnings

    legend_type = legend_spec.get("type")
    converter = _LEGEND_CONVERTERS.get(legend_type)
    if converter is None:
        warnings.append(f"unrecognized_legend_type: {legend_type}")
        return None, False, warnings

    paint_color, legend_warns = converter(legend_spec, _default_color(layer_type))
    warnings.extend(legend_warns)
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

        # All legend-bearing emitters attach legend_spec at the top level of their
        # result dict (h3_binning, kde_contours, heatmap_data, apply_template,
        # create_thematic_map). An earlier `data`-inner fallback existed for
        # heatmap_data, but heatmap_data's output is a FeatureCollection shape,
        # which is_analysis_result rejects ("GeoJSON wins") before this converter
        # runs — so the fallback was unreachable dead code. See ADR-0015.
        legend_spec = analysis_result.get("legend_spec")

        paint_color, has_thematic_paint, legend_warnings = _resolve_paint_color(legend_spec, layer_type)
        warnings.extend(legend_warnings)

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
        if unique_warnings:
            provenance["warnings"] = unique_warnings

        layer_id = base_layer.get("id") or f"{_slugify(algorithm)}_layer"
        source_id = base_layer.get("source") or f"{layer_id}_source"

        res_layer = _build_layer(base_layer, layer_id, source_id, layer_type, paint, provenance)
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
