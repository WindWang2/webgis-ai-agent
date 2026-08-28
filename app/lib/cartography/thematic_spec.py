"""Canonical thematic-style contract — the single source of truth for thematic
cartography on both the MapSpec paint path and the legend path.

ADR-0078 supersedes ADR-0007. ADR-0007 deferred a unified cartographic-style
module because MapSpec ``paint`` was headless-only (``applyMapSpecToMap`` had
zero live callers). ADR-0036 replaced that orphan with ``MapSpecRuntime``,
which IS the live map path (``map-panel.tsx`` → ``hudStateToMapSpec`` →
``runtime.reconcile``). The revisit trigger is now satisfied, so the
``legend_spec`` payload — already the cross-boundary wire format and already a
discriminated union on both sides — is promoted from "legend-only" to the
canonical thematic style. Both MapSpec ``paint`` (StyleMethod) and the legend
UI derive from the SAME ``legend_spec``.

This module is a CONTRACT + pure helpers, not a service. It does not merge the
converters (ADR-0017 keeps ``analysis_cartography_converter`` and
``raster_cartography_converter`` as separate renderers) and it does not replace
``CartographyService`` (ADR-0012 keeps it as the classification engine).
Instead it:

  * owns the single classification-result path (delegates the algorithm to
    ``CartographyService.classify`` — never reimplements Jenks/quantile);
  * resolves palette colors through ONE function (fixing the historical
    three-way divergence: midpoint sampling vs verbatim truncation vs raw
    slice);
  * filters NaN/Inf/null ONCE so nulls cannot poison classification;
  * projects ``legend_spec`` → MapSpec ``paint.color`` StyleMethod through ONE
    pure function (``spec_to_paint``), consumed by both the vector converter
    and the semantic checks;
  * normalizes legacy ``legend_spec`` shapes so old payloads keep working.

Contract: ``legend_spec`` is a JSON-serializable discriminated union::

    {
      "type": "graduated" | "continuous" | "categorical" | "divergent",
      "field": str,                       # the data field (always present)
      # graduated:
      "breaks": [number],                 # class boundaries (len >= 2)
      # continuous / divergent:
      "min": number, "max": number,       # value domain
      "center": number,                   # divergent only
      # categorical:
      "categories": [{"key","color","label"}],
      # shared visual encoding:
      "palette": str,                     # palette identity (for traceability)
      "palette_colors": [str],            # resolved hex colors
      "labels": [str],                    # optional per-class labels
      "method": str,                      # optional classification method
      "nodata": {"color": str, "label": str} | null,  # optional no-data rule
      "unit": str | null, "title": str | null,        # optional
    }

Deterministic, framework-agnostic (no React/MapLibre instances, no numpy
beyond what ``CartographyService.classify`` already uses).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    get_color_from_palette,
    resolve_palette_colors,
)

logger = logging.getLogger(__name__)

# Discriminant values for the canonical thematic-style union.
GRADUATED = "graduated"
CONTINUOUS = "continuous"
CATEGORICAL = "categorical"
DIVERGENT = "divergent"
_THEMATIC_MODES = (GRADUATED, CONTINUOUS, CATEGORICAL, DIVERGENT)

#: Default no-data swatch when a thematic style declares no-data handling.
NODATA_DEFAULT: Dict[str, str] = {"color": "#cccccc", "label": "No data"}


# ─── value hygiene ──────────────────────────────────────────────────────────


def is_finite_number(val: Any) -> bool:
    """True for a real numeric value (int/float), excluding bool/NaN/Inf."""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def finite_numbers(values: Sequence[Any]) -> List[float]:
    """Project a raw property stream to finite floats (NaN/Inf/None/bool/str dropped).

    This is the SINGLE place thematic classification filters bad values, so a
    stray NaN in one column can no longer poison ``np.quantile``/Jenks breaks.
    """
    out: List[float] = []
    for v in values:
        if is_finite_number(v):
            out.append(float(v))
    return out


# ─── palette resolution (single path) ───────────────────────────────────────


def resolve_thematic_colors(
    palette: str,
    k: int,
    breaks: Optional[Sequence[float]] = None,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> List[str]:
    """Resolve exactly ``k`` thematic colors for a palette, ONE consistent way.

    For graduated classification the colors are sampled at each class midpoint
    (normalized over ``[min_val, max_val]``) — the same scheme
    ``CartographyService.build_thematic_style`` already used. For non-graduated
    use (continuous/divergent ramps) the full resolved palette is returned.

    This collapses the historical divergence where ``build_thematic_style``
    sampled by midpoint, ``h3_binning`` took ``resolve_palette_colors(palette)[:5]``
    verbatim, and ``kde_contours`` sliced ``COLOR_PALETTES['Viridis'][:5]`` —
    three different color sets for the same palette name.
    """
    if k <= 0:
        return []
    if breaks is not None and len(breaks) >= 2 and is_finite_number(min_val) and is_finite_number(max_val):
        val_range = (float(max_val) - float(min_val)) or 1.0  # guard flat domain
        colors: List[str] = []
        for i in range(len(breaks) - 1):
            mid = (float(breaks[i]) + float(breaks[i + 1])) / 2.0
            normalized = (mid - float(min_val)) / val_range
            colors.append(get_color_from_palette(palette, normalized))
        return colors
    # Continuous/divergent ramp: the full resolved palette is the ramp itself.
    return resolve_palette_colors(palette)


# ─── canonical builders ─────────────────────────────────────────────────────


def build_graduated_spec(
    geojson: Dict[str, Any],
    field: str,
    method: str = "quantiles",
    k: int = 5,
    palette: str = "YlOrRd",
    *,
    nodata: Optional[Dict[str, str]] = None,
    unit: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a graduated ``legend_spec`` from a GeoJSON feature collection.

    Runs the SINGLE classification (``CartographyService.classify``) over the
    finite values of ``field`` and resolves colors through
    ``resolve_thematic_colors``. Returns ``None`` when there is too little
    numeric data to classify (matches the legacy ``build_thematic_style``
    contract so existing callers/tests are unaffected).
    """
    # E-3（#894）：分类算法已下沉本层（classify.py），不再反向 import
    # services 层的 CartographyService。
    from app.lib.cartography.classify import classify_values

    features = (geojson or {}).get("features", []) or []
    raw = (f.get("properties", {}).get(field) for f in features if isinstance(f, dict))
    values = finite_numbers(raw)
    if len(values) < 2:
        return None

    breaks = classify_values(values, method, k)
    if not breaks:
        return None
    # #618-19: 全等数值列（常量字段）在 n>k 时 classify 返回单断点 [v]，与
    # n≤k 分支的 [v, v] 形状不一致 —— 归一化为 [v, v] 产出合法的单级 graduated
    # spec（此前 create_thematic_map 对常量字段静默 style:None）。
    if len(breaks) == 1:
        breaks = [breaks[0], breaks[0]]
    if len(breaks) < 2:
        return None

    min_val, max_val = min(values), max(values)
    palette_colors = resolve_thematic_colors(palette, len(breaks) - 1, breaks, min_val, max_val)
    labels = [_graduated_label(breaks[i], breaks[i + 1]) for i in range(len(breaks) - 1)]

    spec: Dict[str, Any] = {
        "type": GRADUATED,
        "field": field,
        "breaks": [float(b) for b in breaks],
        "palette": palette,
        "palette_colors": palette_colors,
        "method": method,
        "labels": labels,
    }
    # Default a no-data rule so null/missing field values are diverted to the
    # no-data color on the live map instead of being coerced by `to-number`
    # into the lowest class (ADR-0078 no-data semantics).
    spec["nodata"] = nodata if nodata is not None else dict(NODATA_DEFAULT)
    if unit is not None:
        spec["unit"] = unit
    if title is not None:
        spec["title"] = title
    return spec


def build_categorical_spec(
    field: str,
    categories: Sequence[Dict[str, str]],
    *,
    palette: Optional[str] = None,
    nodata: Optional[Dict[str, str]] = None,
    title: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a categorical ``legend_spec`` from explicit ``{key,color,label}`` entries.

    LISA and other categorical emitters already know their category set; this
    normalizes them into the canonical shape with a stable field identity and
    per-category labels. Returns ``None`` when no valid categories are given.
    """
    cats: List[Dict[str, str]] = []
    for c in categories or []:
        if isinstance(c, dict) and c.get("key") is not None and c.get("color"):
            cats.append({
                "key": str(c["key"]),
                "color": str(c["color"]),
                "label": str(c.get("label") or c["key"]),
            })
        elif isinstance(c, (list, tuple)) and len(c) >= 2:
            cats.append({"key": str(c[0]), "color": str(c[1]), "label": str(c[2] if len(c) > 2 else c[0])})
    if not cats:
        return None

    spec: Dict[str, Any] = {
        "type": CATEGORICAL,
        "field": field,
        "categories": cats,
    }
    if palette:
        spec["palette"] = palette
    if nodata is not None:
        spec["nodata"] = nodata
    if title is not None:
        spec["title"] = title
    return spec


def build_continuous_spec(
    min_val: float,
    max_val: float,
    palette: str,
    *,
    field: str = "",
    palette_colors: Optional[Sequence[str]] = None,
    nodata: Optional[Dict[str, str]] = None,
    unit: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a continuous ``legend_spec`` over ``[min_val, max_val]``.

    ``field`` is REQUIRED for the paint derivation to produce a data-driven
    interpolate expression (the legacy continuous shape omitted it, which
    silently fell back to constant paint). Emitters that know the field should
    pass it; when absent the legacy shape is preserved for backward compat but
    ``spec_to_paint`` will fall back to constant.
    """
    # Accept a flat domain (min == max, e.g. a degenerate KDE with one contour
    # band): emit a constant-domain legend rather than dropping it — the legend
    # overlay still appears, and spec_to_paint falls back to a constant color
    # (correct: a constant field has one class). Preserves the pre-ADR-0078
    # behavior where kde_contours emitted a legend whenever features existed.
    if not (is_finite_number(min_val) and is_finite_number(max_val) and float(min_val) <= float(max_val)):
        return None
    colors = list(palette_colors) if palette_colors else resolve_palette_colors(palette)
    if len(colors) < 2:
        return None

    spec: Dict[str, Any] = {
        "type": CONTINUOUS,
        "min": float(min_val),
        "max": float(max_val),
        "palette": palette,
        "palette_colors": colors,
    }
    if field:
        spec["field"] = field
    # Default no-data rule (see build_graduated_spec).
    spec["nodata"] = nodata if nodata is not None else dict(NODATA_DEFAULT)
    if unit is not None:
        spec["unit"] = unit
    return spec


def build_divergent_spec(
    values: Sequence[Any],
    field: str,
    center: float,
    palette: str,
    *,
    k: int = 5,
    nodata: Optional[Dict[str, str]] = None,
    unit: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a divergent ``legend_spec`` centered at ``center``.

    The domain is derived from the finite ``values`` (clamped to be symmetric
    around ``center`` so both arms of the diverging ramp are represented), and
    the breaks are symmetric quantile steps from ``center``. No producer emits
    divergent today; this builder + its projections + the DIVERGENT_DOMAIN
    check make the mode first-class so a future emitter cannot silently drift.
    """
    nums = finite_numbers(values)
    if len(nums) < 2 or not is_finite_number(center):
        return None
    lo, hi = min(nums), max(nums)
    # Symmetric half-range around center so the diverging ramp covers both arms.
    half = max(abs(lo - center), abs(hi - center)) or 1.0
    min_val = center - half
    max_val = center + half
    colors = resolve_palette_colors(palette)
    if len(colors) < 2:
        return None
    spec: Dict[str, Any] = {
        "type": DIVERGENT,
        "field": field,
        "center": float(center),
        "min": float(min_val),
        "max": float(max_val),
        "palette": palette,
        "palette_colors": colors,
    }
    # Default no-data rule (see build_graduated_spec).
    spec["nodata"] = nodata if nodata is not None else dict(NODATA_DEFAULT)
    if unit is not None:
        spec["unit"] = unit
    return spec


# ─── legend_spec → MapSpec paint.color StyleMethod (single projection) ───────


def spec_to_paint(
    legend_spec: Any,
    fallback_color: str = "#999999",
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Project a canonical ``legend_spec`` to a MapSpec ``paint.color`` StyleMethod.

    Returns ``(style_method_or_None, warnings)``. ``None`` signals "fall back to
    a constant color" — the caller decides the constant. This is the SINGLE
    construction site for the paint projection: the vector converter and the
    semantic checks both consume it, so paint and legend cannot diverge.

    Output is byte-identical to the converter's historical ``_convert_*_legend``
    functions (pinned by ``tests/unit/test_analysis_cartography_converter.py``).
    """
    warnings: List[str] = []
    if legend_spec is None:
        return None, warnings
    if not isinstance(legend_spec, dict):
        warnings.append("invalid_legend_spec: legend_spec must be a dictionary")
        return None, warnings

    ltype = legend_spec.get("type")

    if ltype == GRADUATED:
        return _graduated_to_step(legend_spec, warnings)

    if ltype in (CONTINUOUS, DIVERGENT):
        return _domain_to_interpolate(legend_spec, warnings)

    if ltype == CATEGORICAL:
        return _categorical_to_match(legend_spec, fallback_color, warnings)

    warnings.append(f"unrecognized_legend_type: {ltype}")
    return None, warnings


def _graduated_to_step(legend_spec: Dict[str, Any], warnings: List[str]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    field = legend_spec.get("field", "")
    breaks = legend_spec.get("breaks", [])
    palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

    valid_breaks = isinstance(breaks, list) and len(breaks) >= 2 and all(is_finite_number(b) for b in breaks)
    valid_colors = isinstance(palette_colors, list) and len(palette_colors) >= 1

    if field and valid_breaks and valid_colors:
        default_color = palette_colors[0]
        stops: List[List[Any]] = []
        for i in range(1, len(breaks) - 1):
            color_i = palette_colors[i] if i < len(palette_colors) else palette_colors[-1]
            stops.append([float(breaks[i]), color_i])
        return {"method": "step", "field": field, "default": default_color, "stops": stops}, warnings

    warnings.append("graduated_legend_invalid: insufficient breaks or palette_colors")
    return None, warnings


def _domain_to_interpolate(legend_spec: Dict[str, Any], warnings: List[str]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    field = legend_spec.get("field", "")
    min_val = legend_spec.get("min")
    max_val = legend_spec.get("max")
    palette_colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []

    if (
        field
        and is_finite_number(min_val)
        and is_finite_number(max_val)
        and float(min_val) < float(max_val)
        and isinstance(palette_colors, list)
        and len(palette_colors) >= 2
    ):
        n = len(palette_colors)
        step = (float(max_val) - float(min_val)) / (n - 1)
        stops = [[round(float(min_val) + i * step, 6), palette_colors[i]] for i in range(n)]
        return {"method": "interpolate", "field": field, "stops": stops}, warnings

    warnings.append("continuous_legend_invalid: missing field, palette_colors (min 2), or min must be strictly less than max")
    return None, warnings


def _categorical_to_match(
    legend_spec: Dict[str, Any], fallback_color: str, warnings: List[str]
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    field = legend_spec.get("field", "")
    categories = legend_spec.get("categories", [])

    if field and isinstance(categories, list) and len(categories) >= 1:
        cases: List[List[Any]] = []
        for cat in categories:
            if isinstance(cat, dict):
                key = cat.get("key")
                color = cat.get("color")
                if key is not None and color:
                    cases.append([key, color])
            elif isinstance(cat, (list, tuple)) and len(cat) >= 2:
                cases.append([cat[0], cat[1]])
        if cases:
            # Spec contract: default = last category color (legend_spec.default ignored).
            default_color = cases[-1][1] or fallback_color
            return {"method": "match", "field": field, "cases": cases, "default": default_color}, warnings
        warnings.append("categorical_legend_invalid: no valid category entries")
        return None, warnings

    warnings.append("categorical_legend_invalid: missing field or categories")
    return None, warnings


# ─── normalization & identity (backward compat) ─────────────────────────────


def normalize_legend_spec(legend_spec: Any) -> Optional[Dict[str, Any]]:
    """Normalize a legacy / inbound ``legend_spec`` into the canonical shape.

    Semantics-preserving: sorts graduated breaks deterministically, drops
    non-finite breaks, back-fills a missing ``field`` where another field on
    the spec implies it, and coerces legacy ``colors`` → ``palette_colors``.
    Returns ``None`` for non-dict input. Never raises.
    """
    if not isinstance(legend_spec, dict):
        return None
    spec = dict(legend_spec)
    ltype = spec.get("type")

    # Legacy `colors` alias → canonical `palette_colors`.
    if "palette_colors" not in spec and spec.get("colors"):
        spec["palette_colors"] = spec["colors"]

    if ltype == GRADUATED:
        breaks = [b for b in (spec.get("breaks") or []) if is_finite_number(b)]
        if len(breaks) >= 2:
            breaks = sorted(set(breaks))
        spec["breaks"] = breaks
        if not spec.get("field"):
            spec["field"] = ""
    elif ltype in (CONTINUOUS, DIVERGENT):
        # Continuous/Divergent may carry an explicit field; leave it as-is.
        pass
    elif ltype == CATEGORICAL:
        cats = []
        for c in (spec.get("categories") or []):
            if isinstance(c, dict) and c.get("key") is not None and c.get("color"):
                cats.append({
                    "key": str(c["key"]),
                    "color": str(c["color"]),
                    "label": str(c.get("label") or c["key"]),
                })
        spec["categories"] = cats
        if not spec.get("field"):
            spec["field"] = ""
    return spec


def thematic_field(legend_spec: Any) -> Optional[str]:
    """The single thematic field identity for a ``legend_spec`` (or None).

    Consumers that need "the field this thematic map encodes" (legend filter,
    MapSpec paint, semantic checks) all read this, so the legend filter, the
    paint expression and the consistency check can never reference three
    different fields.
    """
    if not isinstance(legend_spec, dict):
        return None
    field = legend_spec.get("field")
    if isinstance(field, str) and field:
        return field
    return None


def is_thematic(legend_spec: Any) -> bool:
    """True when a ``legend_spec`` carries a usable thematic encoding."""
    if not isinstance(legend_spec, dict):
        return False
    return legend_spec.get("type") in _THEMATIC_MODES and thematic_field(legend_spec) is not None


def palette_size(palette: str) -> int:
    """The declared number of swatches for a palette name (0 if unknown)."""
    colors = COLOR_PALETTES.get(palette)
    return len(colors) if colors else 0


def _graduated_label(lo: float, hi: float) -> str:
    return f"{lo:.2f} - {hi:.2f}"


__all__ = [
    "GRADUATED",
    "CONTINUOUS",
    "CATEGORICAL",
    "DIVERGENT",
    "NODATA_DEFAULT",
    "is_finite_number",
    "finite_numbers",
    "resolve_thematic_colors",
    "build_graduated_spec",
    "build_categorical_spec",
    "build_continuous_spec",
    "build_divergent_spec",
    "spec_to_paint",
    "normalize_legend_spec",
    "thematic_field",
    "is_thematic",
    "palette_size",
]
