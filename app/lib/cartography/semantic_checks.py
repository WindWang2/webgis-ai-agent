"""Deterministic cartographic semantic checks (closed loop: GIS profile ↔ MapSpec).

These are deterministic, evidence-based checks that connect the spatial data
profile (produced by GIS analysis / profile_geojson_source) to the MapSpec's
layer/paint/legend/view configuration. They catch cartographic defects that
"the tool didn't error" cannot: a layer painting a non-existent field, stops
that don't cover the data range, a geometry/layer-type mismatch, an empty-data
source presented as a successful map, etc.

Contract: where evidence is unavailable (no profile), a check is reported as
``not_evaluated`` rather than ``passed`` — never fake success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.cartography.thematic_spec import palette_size, thematic_field

# Layer "type" expected for a given geometry type. A Point layer painted as
# "fill" is almost certainly a configuration defect.
_GEOM_LAYER_TYPE = {
    "Point": {"circle", "symbol"},
    "MultiPoint": {"circle", "symbol"},
    "LineString": {"line"},
    "MultiLineString": {"line"},
    "Polygon": {"fill"},
    "MultiPolygon": {"fill"},
}


@dataclass
class CartographyFinding:
    check: str
    severity: str  # "error" | "warning" | "info"
    message: str
    layer_id: Optional[str] = None
    source_id: Optional[str] = None
    evaluated: bool = True  # False when evidence was unavailable


@dataclass
class CartographyReport:
    findings: List[CartographyFinding] = field(default_factory=list)

    @property
    def errors(self) -> List[CartographyFinding]:
        return [f for f in self.findings if f.severity == "error" and f.evaluated]

    @property
    def warnings(self) -> List[CartographyFinding]:
        return [f for f in self.findings if f.severity == "warning" and f.evaluated]

    @property
    def ok(self) -> bool:
        """No error-severity findings. Warnings do not fail (cartographic taste)."""
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "findings": [
                {
                    "check": f.check,
                    "severity": f.severity,
                    "message": f.message,
                    "layer_id": f.layer_id,
                    "source_id": f.source_id,
                    "evaluated": f.evaluated,
                }
                for f in self.findings
            ],
        }


def _normalize_paint_spec(prop: str, spec: Any) -> Optional[Dict[str, Any]]:
    """Normalize a paint property value into a method-discriminated spec.

    Recognizes two forms:
      1. The canonical StyleMethod dict (``{method: step|interpolate|match, ...}``).
      2. MapLibre-native categorical ``{property, type: "categorical", stops}``
         emitted by ``CompositeMapSpecBuilder`` — normalized to a ``match`` so
         the semantic checks no longer have a categorical blind spot.
    """
    if not isinstance(spec, dict):
        return None
    method = spec.get("method")
    if method in ("interpolate", "step", "match"):
        return spec
    # MapLibre-native categorical: {"property": field, "type": "categorical", "stops": [[val,color],...]}
    if spec.get("type") == "categorical" and spec.get("property"):
        stops = spec.get("stops") or []
        cases = [[s[0], s[1]] for s in stops if isinstance(s, (list, tuple)) and len(s) >= 2]
        default = cases[-1][1] if cases else "#999999"
        return {"method": "match", "field": spec.get("property"), "cases": cases, "default": default}
    # MapLibre-native interval stops: {"property", "type":"interval"|"exponential", "stops"}
    if spec.get("property") and spec.get("type") in ("interval", "exponential"):
        stops = spec.get("stops") or []
        norm_stops = [[s[0], s[1]] for s in stops if isinstance(s, (list, tuple)) and len(s) >= 2]
        if spec.get("type") == "interval":
            default = norm_stops[0][1] if norm_stops else "#999999"
            return {"method": "step", "field": spec.get("property"), "default": default,
                    "stops": norm_stops[1:] if norm_stops else []}
        return {"method": "interpolate", "field": spec.get("property"), "stops": norm_stops}
    return None


def _paint_methods(paint: Dict[str, Any]):
    """Yield (prop_name, method_dict) for every data-driven paint property."""
    if not isinstance(paint, dict):
        return
    for prop, spec in paint.items():
        normalized = _normalize_paint_spec(prop, spec)
        if normalized is not None:
            yield prop, normalized


def _paint_output_colors(spec: Dict[str, Any]) -> List[str]:
    """Ordered output colors a StyleMethod produces (for legend equivalence)."""
    method = spec.get("method")
    if method == "step":
        colors = [spec.get("default")] if spec.get("default") is not None else []
        colors.extend(s[1] for s in (spec.get("stops") or []) if isinstance(s, (list, tuple)) and len(s) >= 2)
        return [c for c in colors if c is not None]
    if method == "interpolate":
        return [s[1] for s in (spec.get("stops") or []) if isinstance(s, (list, tuple)) and len(s) >= 2]
    if method == "match":
        return [c[1] for c in (spec.get("cases") or []) if isinstance(c, (list, tuple)) and len(c) >= 2]
    return []


def _paint_input_stops(spec: Dict[str, Any]) -> List[float]:
    """Ordered numeric inputs of a step/interpolate spec (for domain checks)."""
    method = spec.get("method")
    if method in ("step", "interpolate"):
        return [float(s[0]) for s in (spec.get("stops") or []) if isinstance(s, (list, tuple)) and len(s) >= 2 and _is_num(s[0])]
    return []


def _legend_colors(legend_spec: Dict[str, Any]) -> List[str]:
    """Ordered thematic colors declared by a legend_spec.

    For graduated/continuous/divergent the ramp is ``palette_colors`` (alias
    ``colors``). For categorical there is no ramp — colors live on each
    ``categories[{key,color,label}]`` entry, so they are extracted per-category.
    The previous ``or []`` made the categorical branch dead (a categorical spec
    has no ramp → ``[]`` → returned immediately), so categorical color drift was
    never detected. Fixed.
    """
    if not isinstance(legend_spec, dict):
        return []
    ramp = legend_spec.get("palette_colors") or legend_spec.get("colors")
    if isinstance(ramp, list) and ramp:
        return [c for c in ramp if isinstance(c, str)]
    # categorical: extract per-category colors in declared order.
    cats = legend_spec.get("categories") or []
    return [c.get("color") for c in cats if isinstance(c, dict) and c.get("color")]


def _categorical_color_map(legend_spec: Dict[str, Any]) -> Dict[Any, str]:
    """{key: color} for a categorical legend_spec (order-independent)."""
    out: Dict[Any, str] = {}
    for c in (legend_spec.get("categories") or []) if isinstance(legend_spec, dict) else []:
        if isinstance(c, dict) and c.get("key") is not None and c.get("color"):
            out[c.get("key")] = c.get("color")
    return out


def _approx_eq_list(a: List[float], b: List[float], tol: float = 1e-6) -> bool:
    """Order-sensitive float list equality within tolerance."""
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _thematic_color_spec(paint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick the load-bearing thematic color spec from a layer's paint.

    Prefers the semantic ``color`` property; falls back to the first
    method-discriminated paint property (so composite categorical is seen too).
    """
    for prop, spec in _paint_methods(paint):
        if prop == "color":
            return spec
    for _prop, spec in _paint_methods(paint):
        return spec
    return None


def _check_thematic_consistency(
    report: "CartographyReport",
    lid: Optional[str],
    sid: Optional[str],
    layer: Dict[str, Any],
    profile: Optional[Dict[str, Any]],
) -> None:
    """ADR-0052 cartographic-semantic checks pairing a layer's legend_spec with
    its paint + source profile. Each check is NOT_EVALUATED when the evidence it
    needs (legend_spec, paint, or profile fields) is absent — never a fake pass.
    """
    legend_spec = layer.get("legend_spec")
    color_spec = _thematic_color_spec(layer.get("paint") or {})
    fields_profile = (profile or {}).get("fields") or {}

    has_legend = isinstance(legend_spec, dict)
    if not has_legend and color_spec is None:
        return  # nothing thematic to check on this layer

    # ── LEGEND_STYLE_EQUIVALENCE ────────────────────────────────────────────
    # The live paint and the legend must derive from the same classification:
    # same field, same colors, same numeric thresholds. This is THE drift
    # detector. Categorical comparison is order-independent (match case order is
    # semantically irrelevant in MapLibre); graduated also compares break values.
    if has_legend and color_spec is not None:
        lfield = thematic_field(legend_spec)
        pfield = color_spec.get("field")
        if lfield and pfield and lfield != pfield:
            report.findings.append(CartographyFinding(
                check="LEGEND_STYLE_EQUIVALENCE", severity="error",
                message=(f"Layer '{lid}' legend field '{lfield}' differs from "
                         f"paint field '{pfield}'"),
                layer_id=lid, source_id=sid,
            ))
        ltype = legend_spec.get("type")
        if ltype == "categorical":
            # Compare as {key: color} maps — order-independent.
            legend_map = _categorical_color_map(legend_spec)
            paint_map: Dict[Any, str] = {}
            for case in (color_spec.get("cases") or []):
                if isinstance(case, (list, tuple)) and len(case) >= 2:
                    paint_map[case[0]] = case[1]
            if legend_map and paint_map:
                drifted = [k for k in legend_map if k in paint_map and paint_map[k] != legend_map[k]]
                if drifted:
                    report.findings.append(CartographyFinding(
                        check="LEGEND_STYLE_EQUIVALENCE", severity="error",
                        message=(f"Layer '{lid}' categorical color drift for keys "
                                 f"{drifted}: legend {legend_map} vs paint {paint_map}"),
                        layer_id=lid, source_id=sid,
                    ))
        else:
            # graduated / continuous / divergent: ordered color ramp comparison.
            legend_colors = _legend_colors(legend_spec)
            paint_colors = _paint_output_colors(color_spec)
            if legend_colors and paint_colors and legend_colors != paint_colors:
                report.findings.append(CartographyFinding(
                    check="LEGEND_STYLE_EQUIVALENCE", severity="error",
                    message=(f"Layer '{lid}' legend colors {legend_colors} differ "
                             f"from paint output colors {paint_colors}"),
                    layer_id=lid, source_id=sid,
                ))
            # graduated: also compare interior break thresholds (value drift).
            if ltype == "graduated" and color_spec.get("method") == "step":
                legend_interior = [float(b) for b in (legend_spec.get("breaks") or [])[1:-1] if _is_num(b)]
                paint_stops = _paint_input_stops(color_spec)
                if (legend_interior and paint_stops
                        and not _approx_eq_list(legend_interior, paint_stops)):
                    report.findings.append(CartographyFinding(
                        check="LEGEND_STYLE_EQUIVALENCE", severity="error",
                        message=(f"Layer '{lid}' graduated break values {legend_interior} "
                                 f"differ from paint step stops {paint_stops}"),
                        layer_id=lid, source_id=sid,
                    ))

    # ── CLASSIFICATION_CARDINALITY ──────────────────────────────────────────
    # breaks / categories / colors / labels / paint stops must all agree on the
    # class count.
    if has_legend:
        ltype = legend_spec.get("type")
        colors = _legend_colors(legend_spec)
        if ltype == "graduated":
            breaks = [b for b in (legend_spec.get("breaks") or []) if _is_num(b)]
            labels = legend_spec.get("labels") or []
            expected = max(1, len(breaks) - 1) if len(breaks) >= 2 else len(colors)
            if len(breaks) >= 2 and len(colors) != expected:
                report.findings.append(CartographyFinding(
                    check="CLASSIFICATION_CARDINALITY", severity="error",
                    message=(f"Layer '{lid}' graduated: {len(colors)} colors vs "
                             f"{expected} classes (breaks={len(breaks)})"),
                    layer_id=lid, source_id=sid,
                ))
            if isinstance(labels, list) and labels and len(labels) != expected:
                report.findings.append(CartographyFinding(
                    check="CLASSIFICATION_CARDINALITY", severity="warning",
                    message=(f"Layer '{lid}' graduated: {len(labels)} labels vs "
                             f"{expected} classes"),
                    layer_id=lid, source_id=sid,
                ))
            if color_spec is not None and color_spec.get("method") == "step":
                paint_color_count = len(_paint_output_colors(color_spec))
                if paint_color_count and paint_color_count != expected:
                    report.findings.append(CartographyFinding(
                        check="CLASSIFICATION_CARDINALITY", severity="error",
                        message=(f"Layer '{lid}' graduated: paint has {paint_color_count} "
                                 f"colors vs {expected} legend classes"),
                        layer_id=lid, source_id=sid,
                    ))
        elif ltype == "categorical":
            cats = legend_spec.get("categories") or []
            if color_spec is not None and color_spec.get("method") == "match":
                paint_keys = {c[0] for c in (color_spec.get("cases") or []) if isinstance(c, (list, tuple)) and c}
                legend_keys = {c.get("key") for c in cats if isinstance(c, dict) and c.get("key") is not None}
                if paint_keys != legend_keys and (paint_keys or legend_keys):
                    report.findings.append(CartographyFinding(
                        check="CLASSIFICATION_CARDINALITY", severity="error",
                        message=(f"Layer '{lid}' categorical: legend keys {sorted(legend_keys, key=str)} "
                                 f"!= paint keys {sorted(paint_keys, key=str)}"),
                        layer_id=lid, source_id=sid,
                    ))

    # ── DIVERGENT_DOMAIN ────────────────────────────────────────────────────
    if has_legend and legend_spec.get("type") == "divergent":
        center, mn, mx = legend_spec.get("center"), legend_spec.get("min"), legend_spec.get("max")
        if _is_num(center) and _is_num(mn) and _is_num(mx):
            if not (mn <= center <= mx):
                report.findings.append(CartographyFinding(
                    check="DIVERGENT_DOMAIN", severity="error",
                    message=(f"Layer '{lid}' divergent center {center} outside "
                             f"domain [{mn}, {mx}]"),
                    layer_id=lid, source_id=sid,
                ))
            elif center == mn or center == mx:
                report.findings.append(CartographyFinding(
                    check="DIVERGENT_DOMAIN", severity="warning",
                    message=(f"Layer '{lid}' divergent center {center} at a domain "
                             f"edge — one arm of the ramp is empty"),
                    layer_id=lid, source_id=sid,
                ))

    # ── PALETTE_CARDINALITY ─────────────────────────────────────────────────
    # More classes than palette swatches → silent color cycling.
    if has_legend and legend_spec.get("type") == "graduated":
        pal = legend_spec.get("palette")
        psize = palette_size(pal) if isinstance(pal, str) else 0
        n_colors = len(_legend_colors(legend_spec))
        if psize and n_colors > psize:
            report.findings.append(CartographyFinding(
                check="PALETTE_CARDINALITY", severity="warning",
                message=(f"Layer '{lid}' graduated: {n_colors} classes but palette "
                         f"'{pal}' has {psize} colors (silent cycling)"),
                layer_id=lid, source_id=sid,
            ))

    # ── NO_DATA_SEMANTICS ───────────────────────────────────────────────────
    # A numeric classification without a no-data rule sends null/missing values
    # to the lowest class via to-number coercion. Only fires when the profile
    # actually reports nulls (null_count); otherwise NOT_EVALUATED.
    if has_legend and legend_spec.get("type") in ("graduated", "continuous", "divergent"):
        lfield = thematic_field(legend_spec)
        finfo = fields_profile.get(lfield) if lfield else None
        if isinstance(finfo, dict):
            null_count = finfo.get("null_count")
            if _is_num(null_count) and null_count > 0 and not legend_spec.get("nodata"):
                report.findings.append(CartographyFinding(
                    check="NO_DATA_SEMANTICS", severity="warning",
                    message=(f"Layer '{lid}' numeric field '{lfield}' has {int(null_count)} "
                             f"null/missing values but legend_spec declares no no-data rule"),
                    layer_id=lid, source_id=sid,
                ))
            elif null_count is None:
                report.findings.append(CartographyFinding(
                    check="NO_DATA_SEMANTICS", severity="info",
                    message=(f"Layer '{lid}' no-data handling not evaluable "
                             f"(profile lacks null_count for '{lfield}')"),
                    layer_id=lid, source_id=sid, evaluated=False,
                ))

    # ── CLASSIFICATION_DOMAIN_COVERAGE ──────────────────────────────────────
    # Graduated breaks must span the data; otherwise features fall through to
    # the default color or get clamped.
    if has_legend and legend_spec.get("type") == "graduated":
        breaks = [b for b in (legend_spec.get("breaks") or []) if _is_num(b)]
        lfield = thematic_field(legend_spec)
        finfo = fields_profile.get(lfield) if lfield else None
        if (len(breaks) >= 2 and isinstance(finfo, dict)
                and _is_num(finfo.get("min")) and _is_num(finfo.get("max"))):
            fmin, fmax = float(finfo["min"]), float(finfo["max"])
            b_lo, b_hi = float(breaks[0]), float(breaks[-1])
            if b_hi < fmin or b_lo > fmax:
                report.findings.append(CartographyFinding(
                    check="CLASSIFICATION_DOMAIN_COVERAGE", severity="error",
                    message=(f"Layer '{lid}' breaks [{b_lo}, {b_hi}] do not overlap "
                             f"data range [{fmin}, {fmax}]"),
                    layer_id=lid, source_id=sid,
                ))
            else:
                rng = (fmax - fmin) or 1.0
                if b_lo > fmin + 0.25 * rng or b_hi < fmax - 0.25 * rng:
                    report.findings.append(CartographyFinding(
                        check="CLASSIFICATION_DOMAIN_COVERAGE", severity="warning",
                        message=(f"Layer '{lid}' breaks [{b_lo}, {b_hi}] do not fully "
                                 f"cover data range [{fmin}, {fmax}]"),
                        layer_id=lid, source_id=sid,
                    ))

    # ── CATEGORICAL_DOMAIN_CONSISTENCY ──────────────────────────────────────
    # Data values present in the source should appear as legend categories.
    if has_legend and legend_spec.get("type") == "categorical" and isinstance(profile, dict):
        lfield = thematic_field(legend_spec)
        finfo = fields_profile.get(lfield) if lfield else None
        if isinstance(finfo, dict):
            sample = finfo.get("sampleValues") or finfo.get("values") or []
            # Normalize both sides to str: builders coerce category keys with
            # str(), but profile sampleValues may be ints (numeric-coded cats).
            legend_keys = {str(c.get("key")) for c in (legend_spec.get("categories") or [])
                           if isinstance(c, dict) and c.get("key") is not None}
            missing = [v for v in sample if str(v) not in legend_keys]
            if missing:
                report.findings.append(CartographyFinding(
                    check="CATEGORICAL_DOMAIN_CONSISTENCY", severity="warning",
                    message=(f"Layer '{lid}' data values {missing} for field '{lfield}' "
                             f"have no legend category"),
                    layer_id=lid, source_id=sid,
                ))


def evaluate_cartography_semantics(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> CartographyReport:
    """Run deterministic cartographic semantic checks.

    ``source_profiles`` maps source_id -> profile (from profile_geojson_source).
    When a profile is absent for a source, geometry/field/stops checks for that
    source are reported as ``not_evaluated`` (info), never as a fake pass.
    """
    report = CartographyReport()
    source_profiles = source_profiles or {}
    sources = mapspec.get("sources", {}) or {}
    layers = mapspec.get("layers", []) or []

    source_keys = set(sources.keys())

    for layer in layers:
        lid = layer.get("id")
        sid = layer.get("source")
        profile = source_profiles.get(sid) if sid else None

        # 1. Source/layer reference consistency (error).
        if sid not in source_keys:
            report.findings.append(CartographyFinding(
                check="SOURCE_LAYER_REF", severity="error",
                message=f"Layer '{lid}' references missing source '{sid}'",
                layer_id=lid, source_id=sid,
            ))
            continue  # no point checking further against a missing source

        # 2. Empty-data not success (error): a zero-feature source is no data.
        if profile is not None and profile.get("featureCount", 1) == 0:
            report.findings.append(CartographyFinding(
                check="EMPTY_DATA", severity="error",
                message=f"Layer '{lid}' source '{sid}' has zero features (no data)",
                layer_id=lid, source_id=sid,
            ))

        # 3. Geometry type vs layer type (warning).
        ltype = layer.get("type")
        if profile is not None:
            geom_types = profile.get("geometryTypes") or []
            mismatched = [
                g for g in geom_types
                if g in _GEOM_LAYER_TYPE and ltype not in _GEOM_LAYER_TYPE[g]
            ]
            if mismatched and ltype:
                report.findings.append(CartographyFinding(
                    check="GEOMETRY_LAYER_TYPE", severity="warning",
                    message=(
                        f"Layer '{lid}' type '{ltype}' mismatches geometry "
                        f"{mismatched} from source '{sid}'"
                    ),
                    layer_id=lid, source_id=sid,
                ))
        elif ltype:
            report.findings.append(CartographyFinding(
                check="GEOMETRY_LAYER_TYPE", severity="info",
                message=f"Layer '{lid}' geometry/layer-type not evaluable (no profile for '{sid}')",
                layer_id=lid, source_id=sid, evaluated=False,
            ))

        # 4-6. Paint field existence / numeric / stops-range checks.
        fields_profile = (profile or {}).get("fields") or {}
        for prop, spec in _paint_methods(layer.get("paint") or {}):
            fname = spec.get("field")
            method = spec.get("method")
            if not fname:
                continue
            if profile is None:
                report.findings.append(CartographyFinding(
                    check="PAINT_FIELD_EXISTS", severity="info",
                    message=f"Layer '{lid}' paint '{prop}' field '{fname}' not evaluable (no profile)",
                    layer_id=lid, source_id=sid, evaluated=False,
                ))
                continue
            field_info = fields_profile.get(fname)
            if field_info is None:
                report.findings.append(CartographyFinding(
                    check="PAINT_FIELD_EXISTS", severity="error",
                    message=(
                        f"Layer '{lid}' paint '{prop}' references field '{fname}' "
                        f"absent from source '{sid}'"
                    ),
                    layer_id=lid, source_id=sid,
                ))
                continue
            # interpolate/step require a numeric field.
            if method in ("interpolate", "step") and field_info.get("type") != "number":
                report.findings.append(CartographyFinding(
                    check="INTERPOLATE_NUMERIC_FIELD", severity="warning",
                    message=(
                        f"Layer '{lid}' paint '{prop}' uses {method} on non-numeric "
                        f"field '{fname}' (type {field_info.get('type')})"
                    ),
                    layer_id=lid, source_id=sid,
                ))
            # stops domain should overlap the field's [min, max].
            if method in ("interpolate", "step") and field_info.get("type") == "number":
                stops = spec.get("stops") or []
                fmin, fmax = field_info.get("min"), field_info.get("max")
                if stops and fmin is not None and fmax is not None:
                    stop_inputs = [s[0] for s in stops if isinstance(s, (list, tuple)) and s]
                    if stop_inputs:
                        s_lo, s_hi = min(stop_inputs), max(stop_inputs)
                        # No overlap between [s_lo,s_hi] and [fmin,fmax] → out of range.
                        if s_hi < fmin or s_lo > fmax:
                            report.findings.append(CartographyFinding(
                                check="STOPS_DATA_RANGE", severity="warning",
                                message=(
                                    f"Layer '{lid}' paint '{prop}' stops [{s_lo}, {s_hi}] "
                                    f"do not overlap field '{fname}' data range [{fmin}, {fmax}]"
                                ),
                                layer_id=lid, source_id=sid,
                            ))

        # 4b-10. ADR-0052 thematic consistency: paint ↔ legend equivalence,
        # cardinality, domain coverage, no-data, divergent, palette, categorical
        # domain. Each NOT_EVALUATED when its evidence is absent.
        _check_thematic_consistency(report, lid, sid, layer, profile)

    # 7. Legend / style field consistency (warning).
    legend = (mapspec.get("layout") or {}).get("legend") or {}
    legend_field = legend.get("field") if isinstance(legend, dict) else None
    if legend_field:
        paint_fields = set()
        for layer in layers:
            for _prop, spec in _paint_methods(layer.get("paint") or {}):
                if spec.get("field"):
                    paint_fields.add(spec.get("field"))
        if paint_fields and legend_field not in paint_fields:
            report.findings.append(CartographyFinding(
                check="LEGEND_FIELD_CONSISTENCY", severity="warning",
                message=(
                    f"Legend field '{legend_field}' not used by any layer paint "
                    f"(used: {sorted(paint_fields)})"
                ),
            ))

    return report
