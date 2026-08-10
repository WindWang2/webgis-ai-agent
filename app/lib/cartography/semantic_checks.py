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


def _paint_methods(paint: Dict[str, Any]):
    """Yield (prop_name, method_dict) for every data-driven paint property."""
    if not isinstance(paint, dict):
        return
    for prop, spec in paint.items():
        if isinstance(spec, dict) and spec.get("method") in ("interpolate", "step", "match"):
            yield prop, spec


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
