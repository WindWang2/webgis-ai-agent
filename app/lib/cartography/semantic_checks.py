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
import math
import re
from typing import Any, Dict, List, Optional

from app.lib.cartography.thematic_spec import palette_size, spec_to_paint, thematic_field

# Layer "type" expected for a given geometry type. A Point layer painted as
# "fill" is almost certainly a configuration defect. Heatmap layers consume
# point sources（MapLibre heatmap 的密度累积定义在点要素上），与 circle 同为
# Point 的合法呈现 —— 不加会误杀 heatmap_data 授权的图层。
_GEOM_LAYER_TYPE = {
    "Point": {"circle", "symbol", "heatmap"},
    "MultiPoint": {"circle", "symbol", "heatmap"},
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
    evidence_class: str = "deterministic"
    evidence: Dict[str, Any] = field(default_factory=dict)
    repairability: str = "not_repairable"
    suggested_fix: Optional[Dict[str, Any]] = None

    @property
    def status(self) -> str:
        if not self.evaluated:
            return "not_evaluated"
        if self.severity == "error":
            return "fail"
        return "warning"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "rule": self.check,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "layer_id": self.layer_id,
            "source_id": self.source_id,
            "evaluated": self.evaluated,
            "evidence_class": self.evidence_class,
            "evidence": self.evidence,
            "repairability": self.repairability,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class CartographyCheck:
    """One bounded, machine-readable cartographic assertion.

    ``findings`` remains the legacy non-pass projection. Positive evidence lives
    here so an empty findings list can never be mistaken for proof of quality.
    """

    rule: str
    status: str  # pass | fail | warning | not_evaluated
    severity: str
    message: str
    layer_id: Optional[str] = None
    source_id: Optional[str] = None
    evidence_class: str = "deterministic"
    evidence: Dict[str, Any] = field(default_factory=dict)
    repairability: str = "not_repairable"
    suggested_fix: Optional[Dict[str, Any]] = None

    @property
    def evaluated(self) -> bool:
        return self.status != "not_evaluated"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "check": self.rule,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "layer_id": self.layer_id,
            "source_id": self.source_id,
            "evaluated": self.evaluated,
            "evidence_class": self.evidence_class,
            "evidence": self.evidence,
            "repairability": self.repairability,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class CartographyReport:
    findings: List[CartographyFinding] = field(default_factory=list)
    checks: List[CartographyCheck] = field(default_factory=list)
    profiles: List[str] = field(default_factory=list)

    def add_check(
        self,
        rule: str,
        status: str,
        message: str,
        *,
        severity: str = "info",
        layer_id: Optional[str] = None,
        source_id: Optional[str] = None,
        evidence_class: str = "deterministic",
        evidence: Optional[Dict[str, Any]] = None,
        repairability: str = "not_repairable",
        suggested_fix: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.checks.append(CartographyCheck(
            rule=rule,
            status=status,
            severity=severity,
            message=message,
            layer_id=layer_id,
            source_id=source_id,
            evidence_class=evidence_class,
            evidence=evidence or {},
            repairability=repairability,
            suggested_fix=suggested_fix,
        ))

    @property
    def errors(self) -> List[CartographyFinding]:
        return [f for f in self.findings if f.severity == "error" and f.evaluated]

    @property
    def warnings(self) -> List[CartographyFinding]:
        return [f for f in self.findings if f.severity == "warning" and f.evaluated]

    @property
    def ok(self) -> bool:
        """Strict success gate; incomplete evidence is never represented true."""
        return self.complete and self.status in ("pass", "warning")

    @property
    def no_deterministic_failures(self) -> bool:
        """Compatibility diagnostic distinct from successful evaluation."""
        return not any(
            check.status == "fail" and check.evidence_class == "deterministic"
            for check in self._all_checks()
        )

    def _all_checks(self) -> List[CartographyCheck]:
        checks = list(self.checks)
        represented = {
            (c.rule, c.layer_id, c.source_id, c.message) for c in checks
        }
        for finding in self.findings:
            key = (
                finding.check,
                finding.layer_id,
                finding.source_id,
                finding.message,
            )
            if key in represented:
                continue
            checks.append(CartographyCheck(
                rule=finding.check,
                status=finding.status,
                severity=finding.severity,
                message=finding.message,
                layer_id=finding.layer_id,
                source_id=finding.source_id,
                evidence_class=finding.evidence_class,
                evidence=finding.evidence,
                repairability=finding.repairability,
                suggested_fix=finding.suggested_fix,
            ))
        return checks

    @property
    def status(self) -> str:
        deterministic = [
            c for c in self._all_checks() if c.evidence_class == "deterministic"
        ]
        if any(c.status == "fail" for c in deterministic):
            return "fail"
        if any(c.status == "warning" for c in deterministic):
            return "warning"
        evaluated = [c for c in deterministic if c.evaluated]
        if not evaluated:
            return "not_evaluated"
        if any(c.status == "not_evaluated" for c in deterministic):
            return "warning"
        return "pass"

    @property
    def complete(self) -> bool:
        """Whether every applicable deterministic check produced evidence."""
        deterministic = [
            c for c in self._all_checks() if c.evidence_class == "deterministic"
        ]
        return bool(deterministic) and all(c.evaluated for c in deterministic)

    def to_dict(self) -> Dict[str, Any]:
        checks = self._all_checks()
        passed = self.ok
        return {
            # Serialized ``ok`` is the success signal used by legacy JSON
            # consumers. Missing deterministic evidence must therefore be
            # false even when no explicit deterministic failure exists.
            "ok": passed,
            "no_deterministic_failures": self.no_deterministic_failures,
            "status": self.status,
            # A warning may be a real evaluated warning or a partial review
            # containing NOT_EVALUATED checks. Only the former may be called a
            # pass-with-warnings; missing evidence never becomes success.
            "complete": self.complete,
            "passed": passed,
            "evaluated_count": sum(1 for c in checks if c.evaluated),
            "error_count": sum(
                1 for check in checks
                if check.status == "fail" and check.evidence_class == "deterministic"
            ),
            "warning_count": sum(
                1 for check in checks
                if check.status == "warning"
            ),
            "profiles": self.profiles,
            "checks": [c.to_dict() for c in checks],
            "findings": [f.to_dict() for f in self.findings],
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
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(float(v))
    )


def _is_supported_color(value: Any) -> bool:
    """Validate colors accepted by MapLibre's CSS color grammar.

    Pillow intentionally rejects CSS4 alpha floats in ``rgba()`` even though
    MapLibre accepts them, so handle the bounded rgb/rgba forms explicitly and
    retain Pillow for named/hex/hsl colors.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    color = value.strip()
    functional = re.fullmatch(r"rgba?\(([^)]*)\)", color, flags=re.IGNORECASE)
    if functional:
        parts = [part.strip() for part in functional.group(1).split(",")]
        expected = 4 if color.lower().startswith("rgba") else 3
        if len(parts) != expected:
            return False

        def _channel(part: str) -> bool:
            try:
                if part.endswith("%"):
                    return 0.0 <= float(part[:-1]) <= 100.0
                return 0.0 <= float(part) <= 255.0
            except ValueError:
                return False

        if not all(_channel(part) for part in parts[:3]):
            return False
        if expected == 4:
            try:
                alpha = parts[3]
                return (
                    0.0 <= float(alpha[:-1]) <= 100.0
                    if alpha.endswith("%")
                    else 0.0 <= float(alpha) <= 1.0
                )
            except ValueError:
                return False
        return True
    try:
        from PIL import ImageColor

        ImageColor.getcolor(color, "RGBA")
        return True
    except (ImportError, TypeError, ValueError):
        return False


def _valid_bbox(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(_is_num(v) for v in value)
        and float(value[0]) <= float(value[2])
        and float(value[1]) <= float(value[3])
    )


def _source_is_addressable(source: Dict[str, Any]) -> bool:
    """Whether the current runtime has an actual carrier for this source."""
    source_type = source.get("type")
    if source_type == "raster":
        return bool(source.get("imageRef") or source.get("url") or source.get("ref"))
    if source_type in {"data_fabric", "wms", "wmts", "pmtiles"}:
        return bool(
            source.get("catalog_item_id")
            or source.get("ref_id")
            or source.get("ref")
            or source.get("url")
            or source.get("dataPath")
        )
    return bool(
        source.get("inlineData") is not None
        or source.get("ref_id")
        or source.get("ref")
        or source.get("url")
        or source.get("dataPath")
    )


def _is_geographic_crs(crs: Any) -> bool:
    if not isinstance(crs, str) or not crs.strip():
        return False
    normalized = crs.upper().replace(" ", "")
    return normalized in {
        "EPSG:4326",
        "CRS:84",
        "OGC:CRS84",
        "URN:OGC:DEF:CRS:EPSG::4326",
        "HTTP://WWW.OPENGIS.NET/DEF/CRS/EPSG/0/4326",
        "HTTPS://WWW.OPENGIS.NET/DEF/CRS/EPSG/0/4326",
        "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
        "HTTP://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
        "HTTPS://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
    }


def _constant_opacities(paint: Any):
    if not isinstance(paint, dict):
        return
    for name, value in paint.items():
        if str(name).lower() == "opacity" or str(name).lower().endswith("-opacity"):
            if not isinstance(value, dict) and not isinstance(value, (list, tuple)):
                yield str(name), value


def _classification_integrity(legend_spec: Any) -> tuple[str, Dict[str, Any], str]:
    """Return (status, evidence, message) for the canonical legend contract."""
    if not isinstance(legend_spec, dict):
        return "fail", {"legend_type": None}, "Thematic layer has no legend_spec"
    ltype = legend_spec.get("type")
    if ltype == "graduated":
        breaks = legend_spec.get("breaks") or []
        finite = isinstance(breaks, list) and all(_is_num(v) for v in breaks)
        increasing = finite and len(breaks) >= 2 and all(
            float(breaks[i]) < float(breaks[i + 1])
            for i in range(len(breaks) - 1)
        )
        # #782: 构建器自身的合法退化形态 —— 常量字段的单类 graduated
        # spec（#618-19 归一化为 [v, v]）不是断点错误，不得让评审失败。
        single_class = (
            finite and len(breaks) == 2
            and float(breaks[0]) == float(breaks[1])
        )
        labels = legend_spec.get("labels") or []
        labels_valid = not labels or all(str(v).strip() for v in labels)
        colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []
        invalid_color_indexes = [
            index for index, color in enumerate(colors)
            if not _is_supported_color(color)
        ] if isinstance(colors, list) else [0]
        colors_valid = bool(colors) and not invalid_color_indexes
        evidence = {
            "legend_type": ltype,
            "break_count": len(breaks),
            "strictly_increasing": bool(increasing),
            "single_class_degenerate": bool(single_class),
            "labels_non_empty": bool(labels_valid),
            "colors_valid": colors_valid,
            "invalid_color_indexes": invalid_color_indexes[:16],
        }
        if (not increasing and not single_class) or not labels_valid or not colors_valid:
            return (
                "fail",
                evidence,
                "Graduated legend has invalid breaks, labels, or palette colors",
            )
        return "pass", evidence, "Graduated classification is structurally coherent"
    if ltype in ("continuous", "divergent"):
        mn, mx = legend_spec.get("min"), legend_spec.get("max")
        # #782: min==max 是栅格/连续面的合法退化域（常量或全 nodata 栅格，
        # raster_cartography_converter 刻意产出），不是非法域；min>max 仍失败。
        degenerate = _is_num(mn) and _is_num(mx) and float(mn) == float(mx)
        valid = _is_num(mn) and _is_num(mx) and (float(mn) < float(mx) or degenerate)
        center = legend_spec.get("center")
        center_valid = (
            ltype != "divergent"
            or (_is_num(center) and float(mn) <= float(center) <= float(mx))
        )
        colors = legend_spec.get("palette_colors") or legend_spec.get("colors") or []
        invalid_color_indexes = [
            index for index, color in enumerate(colors)
            if not _is_supported_color(color)
        ] if isinstance(colors, list) else [0]
        colors_valid = len(colors) >= 2 and not invalid_color_indexes
        evidence = {
            "legend_type": ltype,
            "min": mn,
            "max": mx,
            "domain_valid": bool(valid),
            "degenerate_single_class": bool(degenerate),
            "center_valid": bool(center_valid),
            "colors_valid": colors_valid,
            "invalid_color_indexes": invalid_color_indexes[:16],
        }
        if not valid or not center_valid or not colors_valid:
            return (
                "fail",
                evidence,
                "Continuous legend has an invalid numeric domain or palette colors",
            )
        return "pass", evidence, "Continuous classification domain is valid"
    if ltype == "categorical":
        categories = legend_spec.get("categories") or []
        keys = [c.get("key") for c in categories if isinstance(c, dict)]
        labels_valid = bool(categories) and all(
            isinstance(c, dict) and str(c.get("label") or "").strip()
            for c in categories
        )
        unique = len(keys) == len(set(map(str, keys)))
        invalid_color_keys = [
            c.get("key") if isinstance(c, dict) else None
            for c in categories
            if not isinstance(c, dict) or not _is_supported_color(c.get("color"))
        ]
        colors_valid = bool(categories) and not invalid_color_keys
        evidence = {
            "legend_type": ltype,
            "category_count": len(categories),
            "keys_unique": unique,
            "labels_non_empty": labels_valid,
            "colors_valid": colors_valid,
            "invalid_color_keys": invalid_color_keys[:16],
        }
        if not labels_valid or not unique or not colors_valid:
            return (
                "fail",
                evidence,
                "Categorical legend has duplicate keys, empty labels, or invalid colors",
            )
        return "pass", evidence, "Categorical classification is structurally coherent"
    return (
        "fail",
        {"legend_type": ltype},
        f"Unsupported legend type: {ltype!r}",
    )


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


def _thematic_color_property(paint: Dict[str, Any]) -> Optional[str]:
    """Return the authoritative paint key paired with the thematic spec."""
    methods = list(_paint_methods(paint))
    for prop, _spec in methods:
        if prop == "color":
            return prop
    return methods[0][0] if methods else None


def _review_profile(
    mapspec: Dict[str, Any], layer: Dict[str, Any], source: Dict[str, Any]
) -> str:
    """Select a small typed rule profile without introducing a rule DSL."""
    allowed = {
        "general_analysis",
        "thematic_map",
        "statistical_map",
        "raster_result",
        "network_result",
    }
    explicit = layer.get("cartographic_profile") or mapspec.get("cartographic_profile")
    if explicit in allowed:
        return str(explicit)
    if layer.get("type") == "raster" or source.get("type") in ("image", "raster"):
        return "raster_result"
    if isinstance(layer.get("legend_spec"), dict) or _thematic_color_spec(layer.get("paint") or {}):
        return "thematic_map"
    return "general_analysis"


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

    # A legend is a claim about the active data-driven encoding.  Its mere
    # presence cannot certify a constant paint or a different classification
    # method.  These mappings are deterministic projections in thematic_spec.
    if has_legend:
        legend_type = legend_spec.get("type")
        expected_method = {
            "categorical": "match",
            "graduated": "step",
            "continuous": "interpolate",
            "divergent": "interpolate",
        }.get(legend_type)
        actual_method = color_spec.get("method") if color_spec is not None else None
        legend_field = thematic_field(legend_spec)
        style_field = color_spec.get("field") if color_spec is not None else None
        if color_spec is None or expected_method != actual_method:
            report.findings.append(CartographyFinding(
                check="LEGEND_STYLE_EQUIVALENCE",
                severity="error",
                message=(
                    f"Layer '{lid}' {legend_type!r} legend requires a "
                    f"{expected_method!r} data-driven paint, found {actual_method!r}"
                ),
                layer_id=lid,
                source_id=sid,
                evidence={
                    "legend_type": legend_type,
                    "expected_style_method": expected_method,
                    "actual_style_method": actual_method,
                },
            ))
        elif legend_field and style_field != legend_field:
            report.findings.append(CartographyFinding(
                check="LEGEND_STYLE_EQUIVALENCE",
                severity="error",
                message=(
                    f"Layer '{lid}' legend field '{legend_field}' differs from "
                    f"paint field {style_field!r}"
                ),
                layer_id=lid,
                source_id=sid,
                evidence={
                    "legend_field": legend_field,
                    "style_field": style_field,
                },
            ))

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
    sources = mapspec.get("sources", {}) or {}
    layers = mapspec.get("layers", []) or []
    # Profiles are persisted on MapSpec source descriptors by the ingestion
    # pipeline. Reading them here is O(source metadata); review never resolves a
    # ref or materializes feature bodies. Explicit caller profiles override the
    # embedded snapshot (tests and authoritative runtime collectors use this).
    embedded_profiles = {
        sid: source.get("profile")
        for sid, source in sources.items()
        if isinstance(source, dict) and isinstance(source.get("profile"), dict)
    }
    explicit_profile_ids = set((source_profiles or {}).keys())
    source_profiles = {**embedded_profiles, **(source_profiles or {})}

    source_keys = set(sources.keys())

    for layer in layers:
        lid = layer.get("id")
        sid = layer.get("source")
        profile = source_profiles.get(sid) if sid else None
        source = sources.get(sid) if isinstance(sources.get(sid), dict) else {}
        review_profile = _review_profile(mapspec, layer, source)
        if review_profile not in report.profiles:
            report.profiles.append(review_profile)

        # 1. Source/layer reference consistency (error).
        if sid not in source_keys:
            report.findings.append(CartographyFinding(
                check="SOURCE_LAYER_REF", severity="error",
                message=f"Layer '{lid}' references missing source '{sid}'",
                layer_id=lid, source_id=sid,
            ))
            continue  # no point checking further against a missing source
        report.add_check(
            "SOURCE_LAYER_REF",
            "pass",
            f"Layer '{lid}' is bound to source '{sid}'",
            layer_id=lid,
            source_id=sid,
            evidence={
                "layer_id": lid,
                "source_id": sid,
                "source_exists": True,
            },
        )
        source_addressable = _source_is_addressable(source)
        report.add_check(
            "SOURCE_ADDRESSABILITY",
            "pass" if source_addressable else "fail",
            (
                f"Source '{sid}' has a runtime-addressable data carrier"
                if source_addressable
                else f"Source '{sid}' has metadata but no runtime-addressable data carrier"
            ),
            severity="info" if source_addressable else "error",
            layer_id=lid,
            source_id=sid,
            evidence={
                "source_type": source.get("type"),
                "carrier_kinds": [
                    key for key in (
                        "inlineData", "ref", "ref_id", "url", "dataPath",
                        "imageRef", "catalog_item_id",
                    )
                    if source.get(key) is not None
                ],
            },
            repairability="not_repairable",
        )

        layer_provenance = (
            layer.get("provenance")
            if isinstance(layer.get("provenance"), dict) else {}
        )
        provenance_warnings = layer_provenance.get("warnings")
        raster_conversion_errors = [
            warning for warning in (
                provenance_warnings if isinstance(provenance_warnings, list) else []
            )
            if isinstance(warning, str)
            and warning.startswith("raster_converter_error:")
        ]
        if layer.get("type") == "raster":
            raster_artifact_present = bool(
                source.get("imageRef") or source.get("ref") or source.get("url")
            )
            raster_ready = raster_artifact_present and not raster_conversion_errors
            report.add_check(
                "RASTER_ARTIFACT_READY",
                "pass" if raster_ready else "fail",
                (
                    f"Layer '{lid}' raster conversion failed"
                    if raster_conversion_errors
                    else (
                        f"Layer '{lid}' raster artifact is addressable"
                        if raster_artifact_present
                        else f"Layer '{lid}' has no addressable raster artifact"
                    )
                ),
                severity="info" if raster_ready else "error",
                layer_id=lid,
                source_id=sid,
                evidence={
                    "converter_error_count": len(raster_conversion_errors),
                    "artifact_ref_present": raster_artifact_present,
                },
                repairability="not_repairable",
            )
            raster_bounds = source.get("bounds")
            raster_bounds_valid = _valid_bbox(raster_bounds)
            report.add_check(
                "RASTER_BOUNDS_VALIDITY",
                "pass" if raster_bounds_valid else "fail",
                (
                    f"Layer '{lid}' raster has finite ordered bounds"
                    if raster_bounds_valid
                    else f"Layer '{lid}' raster bounds are missing or invalid"
                ),
                severity="info" if raster_bounds_valid else "error",
                layer_id=lid,
                source_id=sid,
                evidence={"bounds": raster_bounds},
                repairability="not_repairable",
            )

        # Desired visibility is structural evidence, not pixel evidence. A
        # hidden/zero-opacity result is detectable, but only malformed opacity
        # is AUTO_SAFE: valid hidden state may be deliberate unless completion
        # intent explicitly says otherwise.
        layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
        visibility_source = (
            "layer.visible" if "visible" in layer
            else "layout.visibility" if "visibility" in layout
            else "maplibre_default_visible"
        )
        declared_visible = layer.get("visible") is not False and layout.get("visibility") != "none"
        cartographic_intent = (
            layer.get("cartographic_intent")
            if isinstance(layer.get("cartographic_intent"), dict) else {}
        )
        expected_visible = cartographic_intent.get("expected_visible")
        if declared_visible:
            visibility_status = "pass"
            visibility_severity = "info"
            visibility_repairability = "not_repairable"
            visibility_fix = None
        elif expected_visible is True:
            visibility_status = "fail"
            visibility_severity = "error"
            visibility_repairability = "auto_safe"
            visibility_fix = {
                "operation": "set_layer_visibility",
                "layer_id": lid,
                "visible": True,
            }
        elif expected_visible is False:
            visibility_status = "pass"
            visibility_severity = "info"
            visibility_repairability = "not_repairable"
            visibility_fix = None
        else:
            visibility_status = "not_evaluated"
            visibility_severity = "info"
            visibility_repairability = "not_repairable"
            visibility_fix = None
        report.add_check(
            "RESULT_VISIBILITY",
            visibility_status,
            (
                f"Layer '{lid}' is intended to be visible"
                if declared_visible
                else (
                    f"Layer '{lid}' is hidden despite explicit result visibility intent"
                    if expected_visible is True
                    else (
                        f"Layer '{lid}' is intentionally hidden per explicit cartographic intent"
                        if expected_visible is False
                        else f"Layer '{lid}' is hidden but no expected-visible intent is available"
                    )
                )
            ),
            severity=visibility_severity,
            layer_id=lid,
            source_id=sid,
            evidence={
                "layer_id": lid,
                "visible": declared_visible,
                "expected_visible": expected_visible,
                "layout_visibility": layout.get("visibility", "visible"),
                "visibility_source": visibility_source,
                "visibility_contract": (
                    "MapLibre layout.visibility defaults to visible"
                    if visibility_source == "maplibre_default_visible" else None
                ),
                "evidence_scope": "desired_structural_state",
            },
            repairability=visibility_repairability,
            suggested_fix=visibility_fix,
        )
        for opacity_name, opacity in _constant_opacities(layer.get("paint") or {}):
            valid_opacity = _is_num(opacity) and 0.0 <= float(opacity) <= 1.0
            nonzero = valid_opacity and float(opacity) > 0.0
            if valid_opacity and nonzero:
                opacity_status = "pass"
                opacity_severity = "info"
                repairability = "not_repairable"
                suggested_fix = None
                opacity_message = f"Layer '{lid}' {opacity_name} is visible and in range"
            elif valid_opacity:
                opacity_status = "fail" if expected_visible is True else "not_evaluated"
                opacity_severity = "error" if expected_visible is True else "info"
                repairability = "auto_safe" if expected_visible is True else "not_repairable"
                suggested_fix = (
                    {
                        "operation": "normalize_opacity",
                        "layer_id": lid,
                        "property": opacity_name,
                        "value": 0.85 if layer.get("type") == "raster" else 1.0,
                    }
                    if expected_visible is True else None
                )
                opacity_message = (
                    f"Layer '{lid}' {opacity_name} is zero despite explicit visibility intent"
                    if expected_visible is True else
                    f"Layer '{lid}' {opacity_name} is zero but visibility intent is unknown"
                )
            else:
                opacity_status = "fail"
                opacity_severity = "error"
                repairability = "auto_safe"
                suggested_fix = {
                    "operation": "normalize_opacity",
                    "layer_id": lid,
                    "property": opacity_name,
                    "value": 0.85 if layer.get("type") == "raster" else 1.0,
                }
                opacity_message = f"Layer '{lid}' {opacity_name} is not a finite value in [0, 1]"
            report.add_check(
                "OPACITY_VALIDITY",
                opacity_status,
                opacity_message,
                severity=opacity_severity,
                layer_id=lid,
                source_id=sid,
                evidence={"property": opacity_name, "value": opacity},
                repairability=repairability,
                suggested_fix=suggested_fix,
            )

        # CRS and extent truthfulness. Missing provenance is explicitly
        # unevaluated; a legacy/default CRS string without `crs_status=explicit`
        # cannot self-certify spatial truth.
        if profile is None:
            report.add_check(
                "CRS_EVIDENCE",
                "not_evaluated",
                f"Source '{sid}' CRS cannot be evaluated without a profile",
                layer_id=lid,
                source_id=sid,
                evidence={"source_id": sid, "crs": None, "crs_status": "unknown"},
            )
            report.add_check(
                "BBOX_VALIDITY",
                "not_evaluated",
                f"Source '{sid}' extent cannot be evaluated without a profile",
                layer_id=lid,
                source_id=sid,
                evidence={"source_id": sid, "bbox": None},
            )
        else:
            crs = profile.get("crs")
            crs_status = profile.get("crs_status") or "unknown"
            crs_explicit = bool(crs) and crs_status == "explicit"
            report.add_check(
                "CRS_EVIDENCE",
                "pass" if crs_explicit else "not_evaluated",
                (
                    f"Source '{sid}' has explicit CRS '{crs}'"
                    if crs_explicit
                    else f"Source '{sid}' has no authoritative CRS evidence"
                ),
                layer_id=lid,
                source_id=sid,
                evidence={
                    "source_id": sid,
                    "crs": crs if crs_explicit else None,
                    "crs_status": crs_status,
                },
            )
            bbox = profile.get("bbox")
            bbox_valid = _valid_bbox(bbox)
            report.add_check(
                "BBOX_VALIDITY",
                "pass" if bbox_valid else ("not_evaluated" if bbox is None else "fail"),
                (
                    f"Source '{sid}' has a finite ordered extent"
                    if bbox_valid
                    else (
                        f"Source '{sid}' has no extent evidence"
                        if bbox is None
                        else f"Source '{sid}' has an invalid extent"
                    )
                ),
                severity="error" if bbox is not None and not bbox_valid else "info",
                layer_id=lid,
                source_id=sid,
                evidence={"source_id": sid, "bbox": bbox},
            )
            if crs_explicit and bbox_valid and _is_geographic_crs(crs):
                geographic_extent = (
                    -180.0 <= float(bbox[0]) <= 180.0
                    and -180.0 <= float(bbox[2]) <= 180.0
                    and -90.0 <= float(bbox[1]) <= 90.0
                    and -90.0 <= float(bbox[3]) <= 90.0
                )
                report.add_check(
                    "CRS_BBOX_COMPATIBILITY",
                    "pass" if geographic_extent else "fail",
                    (
                        f"Source '{sid}' extent is compatible with geographic CRS '{crs}'"
                        if geographic_extent else
                        f"Source '{sid}' extent contains coordinates impossible for geographic CRS '{crs}'"
                    ),
                    severity="info" if geographic_extent else "error",
                    layer_id=lid,
                    source_id=sid,
                    evidence={"source_id": sid, "crs": crs, "bbox": bbox},
                )

        # 2. Empty-data not success: emit positive/missing evidence as well.
        if layer.get("type") != "raster":
            feature_count = profile.get("featureCount") if profile is not None else None
            feature_count_known = _is_num(feature_count) and float(feature_count) >= 0
            has_features = feature_count_known and float(feature_count) > 0
            report.add_check(
                "RESULT_DATA_PRESENCE",
                "pass" if has_features else "fail" if feature_count_known else "not_evaluated",
                (
                    f"Layer '{lid}' source contains {int(feature_count)} features"
                    if has_features
                    else (
                        f"Layer '{lid}' source '{sid}' has zero features"
                        if feature_count_known
                        else f"Layer '{lid}' feature count is unavailable"
                    )
                ),
                severity="info" if has_features else "error" if feature_count_known else "warning",
                layer_id=lid,
                source_id=sid,
                evidence={"feature_count": feature_count},
                repairability="not_repairable",
            )
            if feature_count_known and not has_features:
                report.findings.append(CartographyFinding(
                    check="EMPTY_DATA",
                    severity="error",
                    message=f"Layer '{lid}' source '{sid}' has zero features (no data)",
                    layer_id=lid,
                    source_id=sid,
                ))

        # 3. Geometry type vs layer type (warning).
        ltype = layer.get("type")
        if ltype == "raster":
            pass
        elif profile is not None:
            geom_types = profile.get("geometryTypes") or []
            supported_geom_types = [g for g in geom_types if g in _GEOM_LAYER_TYPE]
            unsupported_geom_types = [g for g in geom_types if g not in _GEOM_LAYER_TYPE]
            mismatched = [
                g for g in supported_geom_types
                if ltype not in _GEOM_LAYER_TYPE[g]
            ]
            mixed_geometry = len(set(supported_geom_types)) > 1
            geometry_evaluated = bool(supported_geom_types and ltype)
            geometry_complete = (
                geometry_evaluated
                and not unsupported_geom_types
                and not mixed_geometry
            )
            report.add_check(
                "GEOMETRY_LAYER_TYPE",
                (
                    "not_evaluated" if mixed_geometry
                    else "fail" if mismatched
                    else "pass" if geometry_complete
                    else "not_evaluated"
                ),
                (
                    f"Layer '{lid}' type '{ltype}' matches source geometry"
                    if geometry_complete and not mismatched
                    else (
                        (
                            f"Layer '{lid}' source contains mixed geometry types; "
                            "runtime sublayer fan-out must provide type evidence"
                        )
                        if mixed_geometry
                        else f"Layer '{lid}' type '{ltype}' mismatches geometry {mismatched}"
                        if mismatched
                        else (
                            f"Layer '{lid}' includes unsupported geometry types {unsupported_geom_types}"
                            if unsupported_geom_types
                            else f"Layer '{lid}' geometry/layer type evidence is incomplete"
                        )
                    )
                ),
                severity="error" if mismatched and not mixed_geometry else "info",
                layer_id=lid,
                source_id=sid,
                evidence={
                    "layer_type": ltype,
                    "geometry_types": geom_types,
                    "supported_geometry_types": supported_geom_types,
                    "unsupported_geometry_types": unsupported_geom_types,
                    "mixed_geometry": mixed_geometry,
                },
            )
            if mismatched and not mixed_geometry:
                report.findings.append(CartographyFinding(
                    check="GEOMETRY_LAYER_TYPE",
                    severity="error",
                    message=(
                        f"Layer '{lid}' type '{ltype}' mismatches geometry "
                        f"{mismatched} from source '{sid}'"
                    ),
                    layer_id=lid,
                    source_id=sid,
                ))
        elif ltype:
            report.add_check(
                "GEOMETRY_LAYER_TYPE",
                "not_evaluated",
                f"Layer '{lid}' geometry/layer-type not evaluable (no profile for '{sid}')",
                layer_id=lid,
                source_id=sid,
                evidence={"layer_type": ltype, "geometry_types": None},
            )

        # 4-6. Paint field existence / numeric / stops-range checks.
        fields_profile = (profile or {}).get("fields") or {}
        # An explicit caller profile is an authoritative inspection result;
        # embedded descriptor metadata is incomplete unless it says otherwise.
        fields_status = (profile or {}).get("fields_status") or (
            "explicit" if sid in explicit_profile_ids
            else "unknown"
        )
        for prop, raw_spec in (layer.get("paint") or {}).items():
            if isinstance(raw_spec, list):
                report.add_check(
                    "STYLE_EXPRESSION_SUPPORT",
                    "not_evaluated",
                    f"Layer '{lid}' paint '{prop}' uses a native expression not inspected by this rule set",
                    layer_id=lid,
                    source_id=sid,
                    evidence={
                        "property": prop,
                        "expression_operator": raw_spec[0] if raw_spec else None,
                    },
                )
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
                if fields_status == "unknown":
                    report.findings.append(CartographyFinding(
                        check="PAINT_FIELD_EXISTS", severity="info",
                        message=(
                            f"Layer '{lid}' paint '{prop}' field '{fname}' cannot be "
                            "verified from descriptor-only schema metadata"
                        ),
                        layer_id=lid, source_id=sid, evaluated=False,
                        evidence={"field": fname, "fields_status": fields_status},
                    ))
                    continue
                report.findings.append(CartographyFinding(
                    check="PAINT_FIELD_EXISTS", severity="error",
                    message=(
                        f"Layer '{lid}' paint '{prop}' references field '{fname}' "
                        f"absent from source '{sid}'"
                    ),
                    layer_id=lid, source_id=sid,
                ))
                continue
            report.add_check(
                "PAINT_FIELD_EXISTS",
                "pass",
                f"Layer '{lid}' paint field '{fname}' exists in source metadata",
                layer_id=lid,
                source_id=sid,
                evidence={"property": prop, "field": fname},
            )
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
        legend_spec = layer.get("legend_spec")
        color_spec = _thematic_color_spec(layer.get("paint") or {})
        raster_classified = (
            review_profile == "raster_result"
            and (
                isinstance(legend_spec, dict)
                or bool(cartographic_intent.get("requires_legend"))
                or bool((profile or {}).get("classification"))
            )
        )
        requires_legend = color_spec is not None or raster_classified
        if requires_legend:
            has_legend = isinstance(legend_spec, dict)
            report.add_check(
                "THEMATIC_LEGEND",
                "pass" if has_legend else "fail",
                (
                    f"Layer '{lid}' has a legend for its thematic encoding"
                    if has_legend
                    else f"Layer '{lid}' has a thematic encoding but no legend_spec"
                ),
                severity="info" if has_legend else "error",
                layer_id=lid,
                source_id=sid,
                evidence={
                    "layer_type": layer.get("type"),
                    "thematic_style": color_spec is not None,
                    "legend_present": has_legend,
                },
            )
            if has_legend:
                legend_type = legend_spec.get("type")
                field_required = (
                    review_profile != "raster_result"
                    and legend_type in {
                        "categorical", "graduated", "continuous", "divergent",
                    }
                )
                legend_field = thematic_field(legend_spec)
                if field_required:
                    report.add_check(
                        "THEMATIC_FIELD",
                        "pass" if legend_field else "fail",
                        (
                            f"Layer '{lid}' legend and style classify field '{legend_field}'"
                            if legend_field
                            else f"Layer '{lid}' vector thematic legend has no classification field"
                        ),
                        severity="info" if legend_field else "error",
                        layer_id=lid,
                        source_id=sid,
                        evidence={
                            "legend_type": legend_type,
                            "legend_field": legend_field,
                            "style_field": (
                                color_spec.get("field")
                                if isinstance(color_spec, dict) else None
                            ),
                            "raster_exception": False,
                        },
                        repairability=(
                            "not_repairable" if legend_field
                            else "auto_with_semantic_risk"
                        ),
                    )
                class_status, class_evidence, class_message = _classification_integrity(legend_spec)
                report.add_check(
                    "CLASSIFICATION_INTEGRITY",
                    class_status,
                    f"Layer '{lid}': {class_message}",
                    severity="info" if class_status == "pass" else "error",
                    layer_id=lid,
                    source_id=sid,
                    evidence=class_evidence,
                    repairability=(
                        "not_repairable"
                        if class_status == "pass"
                        else "auto_with_semantic_risk"
                    ),
                )

        findings_before = len(report.findings)
        equivalence_before = sum(
            1 for finding in report.findings
            if finding.check == "LEGEND_STYLE_EQUIVALENCE" and finding.layer_id == lid
        )
        _check_thematic_consistency(report, lid, sid, layer, profile)
        equivalence_after = sum(
            1 for finding in report.findings
            if finding.check == "LEGEND_STYLE_EQUIVALENCE" and finding.layer_id == lid
        )
        if equivalence_after > equivalence_before and isinstance(legend_spec, dict):
            canonical_paint, paint_warnings = spec_to_paint(legend_spec)
            paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
            color_property = _thematic_color_property(paint)
            if (
                canonical_paint is not None
                and not paint_warnings
                and color_property is not None
                and isinstance(paint.get(color_property), dict)
            ):
                expected_method = {
                    "categorical": "match",
                    "graduated": "step",
                    "continuous": "interpolate",
                    "divergent": "interpolate",
                }.get(legend_spec.get("type"))
                legend_field = thematic_field(legend_spec)
                style_field = color_spec.get("field") if color_spec is not None else None
                style_method = color_spec.get("method") if color_spec is not None else None
                safe_projection = bool(
                    legend_field
                    and style_field == legend_field
                    and expected_method
                    and style_method == expected_method
                )
                suggested_fix = {
                    "operation": "refresh_style_from_legend",
                    "layer_id": lid,
                    "property": color_property,
                    "value": canonical_paint,
                }
                for finding in report.findings[findings_before:]:
                    if finding.check == "LEGEND_STYLE_EQUIVALENCE":
                        # ADR-0052 makes legend_spec the canonical thematic
                        # classification and derives paint with spec_to_paint;
                        # this repair restores that projection, it does not
                        # invent new breaks/categories.
                        finding.evidence = {
                            **finding.evidence,
                            "authoritative_source": "legend_spec",
                            "legend_field": legend_field,
                            "style_field": style_field,
                            "expected_style_method": expected_method,
                            "actual_style_method": style_method,
                            "semantic_parameters_changed": not safe_projection,
                        }
                        finding.repairability = (
                            "auto_safe" if safe_projection
                            else "auto_with_semantic_risk"
                        )
                        finding.suggested_fix = suggested_fix if safe_projection else None
        if isinstance(legend_spec, dict) and color_spec is not None and equivalence_after == equivalence_before:
            report.add_check(
                "LEGEND_STYLE_EQUIVALENCE",
                "pass",
                f"Layer '{lid}' legend and style use the same classification",
                layer_id=lid,
                source_id=sid,
                evidence={
                    "legend_type": legend_spec.get("type"),
                    "legend_field": thematic_field(legend_spec),
                    "style_field": color_spec.get("field"),
                },
            )

        display_ref = source.get("ref_id") or source.get("ref")
        provenance = layer.get("provenance") if isinstance(layer.get("provenance"), dict) else {}
        # ``source_ref`` identifies the analysis input. It must never satisfy
        # the output-to-map chain; only the display result identity can do so.
        result_ref = provenance.get("result_ref")
        if (
            not display_ref
            and result_ref
            and source.get("imageRef") == result_ref
        ):
            display_ref = source.get("imageRef")
        analysis_origin = bool(
            result_ref
            or provenance.get("algorithm")
            or provenance.get("source_ref")
            or provenance.get("computed_at")
            or provenance.get("item_id")
        )
        if analysis_origin:
            provenance_complete = bool(display_ref and result_ref)
            provenance_matches = bool(
                provenance_complete and display_ref == result_ref
            )
            provenance_status = (
                "pass" if provenance_matches
                else "fail" if provenance_complete
                else "not_evaluated"
            )
            report.add_check(
                "RESULT_MAP_PROVENANCE",
                provenance_status,
                (
                    f"Layer '{lid}' is traceable to result ref '{display_ref}'"
                    if provenance_matches
                    else (
                        f"Layer '{lid}' source and provenance identify different results"
                        if provenance_complete
                        else f"Layer '{lid}' result-to-source identity is incomplete"
                    )
                ),
                severity="error" if provenance_status == "fail" else "warning",
                layer_id=lid,
                source_id=sid,
                evidence={
                    "display_ref": display_ref,
                    # Compatibility alias: historically this evidence key named
                    # the MapSpec source carrier, not analysis input provenance.
                    "source_ref": display_ref,
                    "result_ref": result_ref,
                    "input_ref": provenance.get("source_ref"),
                    "analysis_origin": analysis_origin,
                    "matches": provenance_matches,
                },
            )
        report.add_check(
            "VISUAL_OVERLAP",
            "not_evaluated",
            "Rendered label overlap requires visual evidence",
            layer_id=lid,
            source_id=sid,
            evidence_class="visual",
            evidence={"pixel_evidence_available": False},
        )

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
