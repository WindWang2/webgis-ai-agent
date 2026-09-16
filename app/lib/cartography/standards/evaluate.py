"""Deterministic standards evaluation (ADR-0200).

Each rule kind has exactly one checker; every checker *delegates* to the
single existing engine implementation (component_composer, context_matrix /
palettes primitives, thematic_spec, source profiles) and never re-implements
classification/layout/CVD math. Where the evidence a checker needs is absent
the obligation is reported ``not_evaluated`` — never a fake pass, per the
repo-wide contract.

Statuses: ``satisfied`` / ``violated`` / ``not_evaluated`` (evidence missing
or blocked by a failed dependency) / ``not_applicable`` (vacuous precondition,
e.g. legend obligations on a map with no thematic layer).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from app.lib.cartography.component_composer import required_components_for
from app.lib.cartography.context_matrix import evaluate_cell
from app.lib.cartography.defaults import DEFAULT_CLASS_COUNT
from app.lib.cartography.palettes import (
    grayscale_ramp_separation,
    min_adjacent_delta_e,
    print_desaturate,
    simulate_cvd,
)
from app.lib.cartography.quality_loop import cartographic_projection
from app.lib.cartography.standards.graph import StandardsViolation
from app.lib.cartography.standards.profile import ProfileSpec
from app.lib.cartography.standards.rule import CartographicRule
from app.lib.cartography.thematic_spec import thematic_field

# Single-source detection of a thematic paint spec. quality_loop already
# imports this private helper from semantic_checks for the same reason: it is
# THE definition of "thematic color spec"; duplicating the shape test here
# would be a second parser that drifts.
from app.lib.cartography.semantic_checks import _thematic_color_spec
# Raw-color fallback thresholds come from the same SymbologyConstraints the
# context matrix judges with (context_matrix imports these same names).
from app.lib.cartography.symbology import (
    SymbologyConstraints,
    _context_min_delta_e,
)

#: Point layers at or above this feature count must declare a label budget.
#: Constant (not env-tunable) so QA reports stay replayable; changes are a
#: pack version bump, not a deploy flag.
LABEL_DENSITY_FEATURE_THRESHOLD = 200

_CVD_CONTEXTS = ("cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia")

_CONTINUOUS_LEGEND_TYPES = ("graduated", "continuous", "divergent")
_LEGEND_COMPONENT_TYPES = frozenset({"legend", "categorical_legend", "continuous_colorbar"})
_UNCERTAINTY_COMPONENT_TYPES = frozenset({"uncertainty_panel", "methodology_note"})

_RATE_TOKENS = ("per_", "_per_", "rate", "ratio", "pct", "percent", "share", "percapita")
_DENSITY_TOKENS = ("density",)
_COUNT_TOKENS = (
    "count", "total", "num_", "_num", "quantity", "population", "cases", "sum_",
)
_TEMPORAL_TOKENS = ("date", "time", "year", "month", "day", "timestamp", "period", "decade")
_UNCERTAINTY_TOKENS = (
    "uncertain", "confidence", "moe", "margin", "error", "stddev", "std_dev",
    "variance", "_ci", "ci_",
)


class StandardsContext:
    """Bounded, deterministic measurement context for one evaluation.

    Reads source profiles and structural flags only — never feature bodies.
    The credential-safe ``cartographic_projection`` is kept for callers that
    need to persist review context; checkers read the original mapspec
    structure directly and serialize only bounded evidence refs.
    """

    def __init__(
        self,
        mapspec: Dict[str, Any],
        source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.mapspec = mapspec
        self.projection = cartographic_projection(mapspec)
        sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
        embedded = {
            str(sid): source.get("profile")
            for sid, source in sources.items()
            if isinstance(source, dict) and isinstance(source.get("profile"), dict)
        }
        self.profiles: Dict[str, Dict[str, Any]] = {
            **embedded, **(source_profiles or {}),
        }
        self.layers: List[Dict[str, Any]] = [
            layer for layer in (mapspec.get("layers") or [])
            if isinstance(layer, dict)
        ]
        self.components: List[Dict[str, Any]] = [
            component for component in (
                ((mapspec.get("layout") or {}).get("components") or [])
                if isinstance(mapspec.get("layout"), dict) else []
            )
            if isinstance(component, dict) and component.get("enabled") is not False
        ]
        self.component_types = frozenset(
            str(c.get("type") or "") for c in self.components)
        flags = {
            "has_thematic_layer": self._has_thematic_layer(),
            "has_projection_info": self._has_projection_info(),
            "has_data_source": self._has_data_source(),
            "has_statistics_panel": "statistics_panel" in self.component_types,
            "has_location_context": "inset_map" in self.component_types,
        }
        self.content_flags = flags
        self.data_semantics: FrozenSet[str] = self._derive_semantics()

    # ── structural flags ─────────────────────────────────────────────────

    def _has_thematic_layer(self) -> bool:
        for layer in self.layers:
            if isinstance(layer.get("legend_spec"), dict):
                return True
            if _thematic_color_spec(layer.get("paint") or {}) is not None:
                return True
        return False

    def _has_projection_info(self) -> bool:
        for profile in self.profiles.values():
            if not isinstance(profile, dict):
                continue
            crs = profile.get("crs")
            if crs and profile.get("crs_status") == "explicit":
                return True
        return False

    def _has_data_source(self) -> bool:
        for component in self.components:
            if str(component.get("type") or "") != "attribution":
                continue
            options = (
                component.get("options")
                if isinstance(component.get("options"), dict) else {}
            )
            text = str(options.get("text") or component.get("text") or "")
            if text and "待补充" not in text:
                return True
        return False

    # ── data semantics (from profile field metadata only) ────────────────

    def _derive_semantics(self) -> FrozenSet[str]:
        semantics: set = set()
        saw_categorical = False
        saw_numeric = False
        for profile in self.profiles.values():
            if not isinstance(profile, dict):
                continue
            if profile.get("hasTimeField") is True:
                semantics.add("temporal")
            fields = profile.get("fields")
            if not isinstance(fields, dict):
                continue
            for raw_name, meta in fields.items():
                name = str(raw_name).lower()
                ftype = (
                    meta.get("type")
                    if isinstance(meta, dict) else meta
                )
                numeric = ftype == "number"
                saw_numeric = saw_numeric or numeric
                saw_categorical = saw_categorical or (ftype is not None and not numeric)
                if any(tok in name for tok in _RATE_TOKENS):
                    semantics.add("rate")
                if any(tok in name for tok in _DENSITY_TOKENS):
                    semantics.add("density")
                if any(tok in name for tok in _COUNT_TOKENS):
                    semantics.add("count")
                if any(tok in name for tok in _TEMPORAL_TOKENS):
                    semantics.add("temporal")
                if any(tok in name for tok in _UNCERTAINTY_TOKENS):
                    semantics.add("uncertainty")
        if saw_categorical:
            semantics.add("category")
        if saw_numeric:
            semantics.add("continuous")
        return frozenset(semantics)

    # ── shared measurements ──────────────────────────────────────────────

    def thematic_layers(self) -> List[Dict[str, Any]]:
        return [
            layer for layer in self.layers
            if isinstance(layer.get("legend_spec"), dict)
            or _thematic_color_spec(layer.get("paint") or {}) is not None
        ]


@dataclass
class _Outcome:
    status: str  # satisfied | violated | not_evaluated | not_applicable
    violations: Tuple[StandardsViolation, ...] = ()
    blocked_by: str = ""
    note: str = ""


def _ref_layer(layer: Dict[str, Any], path: str) -> str:
    return f"mapspec://layers/{layer.get('id')}/{path}"


def _violation(
    rule: CartographicRule, severity: str, message: str, *,
    layer: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    evidence_refs: Tuple[str, ...] = (),
    fix_hint: Optional[Dict[str, Any]] = None,
) -> StandardsViolation:
    return StandardsViolation(
        rule_id=rule.rule_id,
        kind=rule.kind,
        severity=severity,
        message=message,
        layer_id=str((layer or {}).get("id") or ""),
        evidence={"evidence_refs": list(evidence_refs), **(evidence or {})},
        fix_hint=dict(fix_hint or rule.fix_hint or {}),
    )


# ── checkers (one per kind; each delegates to its engine) ─────────────────


def _check_required_components(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    canvas = {"print": "a4_portrait", "screen": "screen_16_9", "mobile": "screen_16_9"}[
        profile.medium
    ]
    plan = required_components_for(canvas, ctx.content_flags)
    present = ctx.component_types
    missing = [req for req in plan.required if req.type not in present]
    violations = tuple(
        _violation(
            rule, severity,
            f"必配组件缺失：{req.type}（{req.reason}）",
            evidence={
                "component_type": req.type,
                "plan_purpose": plan.purpose,
                "engine": "component_composer.required_components_for",
                "advisory": req.advisory,
            },
            evidence_refs=(f"engine://component_composer/required_components_for/{plan.purpose}",),
            fix_hint={
                "route": "component_autofill",
                "component_type": req.type,
                "options": dict(req.placeholder_options or {}),
            },
        )
        for req in missing
    )
    return _Outcome(
        status="violated" if violations else "satisfied",
        violations=violations,
        note=f"plan={plan.purpose} missing={[m.type for m in missing]}",
    )


def _check_source_disclosure(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    attributions = [
        c for c in ctx.components if str(c.get("type") or "") == "attribution"
    ]
    if not attributions:
        return _Outcome(
            status="violated",
            violations=(_violation(
                rule, severity,
                "缺少 attribution 组件：数据来源未披露（署名不可省略）。",
                evidence={"engine": "standards.content_flags.has_data_source=False"},
                evidence_refs=("mapspec://layout/components",),
            ),),
        )
    for component in attributions:
        options = (
            component.get("options")
            if isinstance(component.get("options"), dict) else {}
        )
        text = str(options.get("text") or component.get("text") or "")
        if text and "待补充" not in text:
            return _Outcome(status="satisfied", note="attribution 携带真实署名")
    return _Outcome(
        status="violated",
        violations=(_violation(
            rule, severity,
            "attribution 仍为占位文本：数据来源待补充（出版物不得以占位署名面世）。",
            evidence={"engine": "standards.content_flags.has_data_source=False"},
            evidence_refs=("mapspec://layout/components",),
        ),),
    )


def _legend_present(ctx: StandardsContext) -> bool:
    layout = ctx.mapspec.get("layout") if isinstance(ctx.mapspec.get("layout"), dict) else {}
    legend_panel = (
        layout.get("legend") if isinstance(layout.get("legend"), dict) else None
    )
    if legend_panel is not None and legend_panel.get("visible") is not False:
        return True
    if ctx.component_types & _LEGEND_COMPONENT_TYPES:
        return True
    return any(isinstance(layer.get("legend_spec"), dict) for layer in ctx.layers)


def _check_legend_present(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    if not ctx.content_flags["has_thematic_layer"]:
        return _Outcome(status="not_applicable", note="无专题编码层")
    if _legend_present(ctx):
        return _Outcome(status="satisfied", note="图例组件/面板/legend_spec 至少一项在场")
    return _Outcome(
        status="violated",
        violations=(_violation(
            rule, severity,
            "专题编码在场但无图例（组件、面板、legend_spec 均缺失）。",
            evidence={"engine": "standards.structural.legend_present"},
            evidence_refs=("mapspec://layout",),
        ),),
    )


def _check_legend_unit_disclosure(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    layout = ctx.mapspec.get("layout") if isinstance(ctx.mapspec.get("layout"), dict) else {}
    legend_panel = layout.get("legend") if isinstance(layout.get("legend"), dict) else {}
    panel_title = str(legend_panel.get("title") or "")
    violations: List[StandardsViolation] = []
    checked = 0
    for layer in ctx.layers:
        legend_spec = layer.get("legend_spec")
        if not isinstance(legend_spec, dict):
            continue
        if str(legend_spec.get("type") or "") not in _CONTINUOUS_LEGEND_TYPES:
            continue
        checked += 1
        title = str(legend_spec.get("title") or "") or panel_title
        unit = legend_spec.get("unit")
        if title and unit:
            continue
        violations.append(_violation(
            rule, severity,
            f"图层 {layer.get('id')} 的 {legend_spec.get('type')} 图例缺 "
            f"{'title' if not title else ''}{'与' if not title and not unit else ''}"
            f"{'unit' if not unit else ''} 口径声明。",
            layer=layer,
            evidence={
                "has_title": bool(title), "has_unit": bool(unit),
                "engine": "thematic_spec.legend_spec(title/unit)",
            },
            evidence_refs=(_ref_layer(layer, "legend_spec"),),
        ))
    if checked == 0:
        return _Outcome(status="not_applicable", note="无分级/连续/发散图例")
    return _Outcome(
        status="violated" if violations else "satisfied", violations=tuple(violations))


def _check_count_vs_rate(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    violations: List[StandardsViolation] = []
    checked = 0
    unevaluated = 0
    for layer in ctx.layers:
        legend_spec = layer.get("legend_spec")
        if not isinstance(legend_spec, dict):
            continue
        if str(legend_spec.get("type") or "") not in _CONTINUOUS_LEGEND_TYPES:
            continue
        if str(layer.get("type") or "") not in ("fill", "fill-extrusion"):
            continue
        field = thematic_field(legend_spec)
        if not field:
            continue
        profile_ = ctx.profiles.get(str(layer.get("source") or ""))
        fields = profile_.get("fields") if isinstance(profile_, dict) else None
        meta = fields.get(field) if isinstance(fields, dict) else None
        ftype = meta.get("type") if isinstance(meta, dict) else meta
        if ftype != "number":
            if meta is not None:
                # non-numeric field cannot be a count — satisfied vacuously
                continue
            # fail-closed: this layer's field type is unknown, but the missing
            # evidence must not hide other layers' violations — record and go on.
            unevaluated += 1
            continue
        checked += 1
        normalized = field.lower()
        is_rate = any(tok in normalized for tok in _RATE_TOKENS + _DENSITY_TOKENS)
        is_count = any(tok in normalized for tok in _COUNT_TOKENS)
        if is_count and not is_rate:
            violations.append(_violation(
                rule, severity,
                f"字段 {field} 名称具有计数语义且被直接分级的面填充——"
                "choropleth 应呈现归一化比率/密度而非原始计数。",
                layer=layer,
                evidence={
                    "field": field,
                    "count_tokens": [t for t in _COUNT_TOKENS if t in normalized],
                    "engine": "profile-field-name semantics + geometry(fill)",
                },
                evidence_refs=(
                    _ref_layer(layer, "legend_spec"),
                    f"profile://sources/{layer.get('source')}/fields/{field}",
                ),
            ))
    if violations:
        return _Outcome(
            status="violated", violations=tuple(violations),
            note=f"{unevaluated} layer(s) lacked field-type evidence" if unevaluated else "",
        )
    if unevaluated:
        return _Outcome(
            status="not_evaluated",
            note=f"字段类型无 profile 证据（fail-closed，不推断）× {unevaluated}",
        )
    if checked == 0:
        return _Outcome(status="not_applicable", note="无带字段证据的面分级层")
    return _Outcome(status="satisfied", violations=())


def _palette_cells(
    ctx: StandardsContext, layer: Dict[str, Any], contexts: Sequence[str],
) -> Tuple[List[Dict[str, Any]], str]:
    """Measure palette separability via context_matrix (same-source constants).

    Prefers the declared palette name (evaluate_cell path). Falls back to raw
    legend/paint colors via the same palettes primitives the matrix uses —
    still the shared CVD/print transforms, not a re-implementation.
    """
    legend_spec = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else {}
    palette = str(legend_spec.get("palette") or "")
    colors = legend_spec.get("palette_colors") or legend_spec.get("colors")
    color_list = [c for c in colors if isinstance(c, str)] if isinstance(colors, list) else []
    cells: List[Dict[str, Any]] = []
    mode = ""
    if palette:
        mode = "palette_name"
        k = len(color_list) if color_list else DEFAULT_CLASS_COUNT
        for context in contexts:
            cell = evaluate_cell(palette, context, k=k)
            cells.append({
                "context": context,
                "verdict": cell["verdict"],
                "min_metric": cell["min_metric"],
                "threshold": cell["threshold"],
            })
    elif len(color_list) >= 2:
        mode = "raw_colors"
        constraints = SymbologyConstraints()
        for context in contexts:
            if context == "print":
                metric = grayscale_ramp_separation(print_desaturate(list(color_list)))
                threshold = float(constraints.min_gray_delta_l)
                cells.append({
                    "context": context,
                    "min_metric": None if metric is None else round(float(metric), 4),
                    "threshold": round(threshold, 4),
                    "verdict": "unavailable" if metric is None else (
                        "pass" if metric >= threshold else "fail"),
                })
            else:
                sim = [simulate_cvd(c, context) for c in color_list]
                if any(s is None for s in sim):
                    cells.append({"context": context, "verdict": "unavailable"})
                    continue
                metric = min_adjacent_delta_e(sim)  # type: ignore[arg-type]
                threshold = _context_min_delta_e(context, constraints)
                cells.append({
                    "context": context,
                    "min_metric": round(float(metric), 4),
                    "threshold": round(float(threshold), 4),
                    "verdict": "pass" if metric >= threshold else "fail",
                })
    return cells, mode


def _check_palette_context(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str, contexts: Sequence[str], label: str,
) -> _Outcome:
    violations: List[StandardsViolation] = []
    checked = 0
    unevaluated = 0
    for layer in ctx.thematic_layers():
        legend_spec = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else None
        if legend_spec is None and _thematic_color_spec(layer.get("paint") or {}) is None:
            continue
        cells, mode = _palette_cells(ctx, layer, contexts)
        if not cells:
            unevaluated += 1
            continue
        checked += 1
        failing = [c for c in cells if c["verdict"] == "fail"]
        if not failing:
            continue
        violations.append(_violation(
            rule, severity,
            f"图层 {layer.get('id')} 色带在 {label} 上下文不可分辨："
            + ", ".join(f"{c['context']}(ΔE≥{c.get('min_metric')}/{c.get('threshold')})" for c in failing),
            layer=layer,
            evidence={
                "mode": mode,
                "cells": cells,
                "engine": "context_matrix.evaluate_cell/palettes primitives",
            },
            evidence_refs=(
                _ref_layer(layer, "legend_spec"),
                *(
                    f"engine://context_matrix/cell/"
                    f"{'raw_colors' if mode == 'raw_colors' else str((legend_spec or {}).get('palette') or '')}"
                    f"/{c['context']}"
                    for c in cells
                ),
            ),
        ))
    if checked == 0:
        if unevaluated:
            return _Outcome(status="not_evaluated", note="色带证据缺失（palette/colors 均不可得）")
        return _Outcome(status="not_applicable", note="无专题色带层")
    return _Outcome(
        status="violated" if violations else "satisfied", violations=tuple(violations))


def _check_classification_declared(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    violations: List[StandardsViolation] = []
    checked = 0
    for layer in ctx.layers:
        legend_spec = layer.get("legend_spec")
        if not isinstance(legend_spec, dict):
            continue
        if str(legend_spec.get("type") or "") != "graduated":
            continue
        checked += 1
        if legend_spec.get("method"):
            continue
        violations.append(_violation(
            rule, severity,
            f"图层 {layer.get('id')} 的分级图例未声明分类方法（复核不可复现）。",
            layer=layer,
            evidence={"engine": "thematic_spec.legend_spec(method)"},
            evidence_refs=(_ref_layer(layer, "legend_spec"),),
        ))
    if checked == 0:
        return _Outcome(status="not_applicable", note="无 graduated 图例")
    return _Outcome(
        status="violated" if violations else "satisfied", violations=tuple(violations))


def _check_label_density_declared(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    violations: List[StandardsViolation] = []
    checked = 0
    for layer in ctx.layers:
        if str(layer.get("type") or "") not in ("circle", "symbol"):
            continue
        profile_ = ctx.profiles.get(str(layer.get("source") or ""))
        feature_count = profile_.get("featureCount") if isinstance(profile_, dict) else None
        if not isinstance(feature_count, (int, float)) or isinstance(feature_count, bool):
            continue
        if feature_count < LABEL_DENSITY_FEATURE_THRESHOLD:
            continue
        checked += 1
        label = layer.get("label") if isinstance(layer.get("label"), dict) else {}
        mode = str(label.get("mode") or "all")
        declared = mode in ("top_n", "hover_only") or label.get("topN") or label.get("maxLabels")
        if declared:
            continue
        violations.append(_violation(
            rule, severity,
            f"图层 {layer.get('id')} 含 {int(feature_count)} 要素但标注未声明预算"
            f"（mode/topN；阈值 {LABEL_DENSITY_FEATURE_THRESHOLD}）。",
            layer=layer,
            evidence={
                "feature_count": int(feature_count),
                "threshold": LABEL_DENSITY_FEATURE_THRESHOLD,
                "engine": "label_plan budget declaration",
            },
            evidence_refs=(
                _ref_layer(layer, "label"),
                f"profile://sources/{layer.get('source')}/featureCount",
            ),
        ))
    if checked == 0:
        return _Outcome(status="not_applicable", note="无高密度点层")
    return _Outcome(
        status="violated" if violations else "satisfied", violations=tuple(violations))


def _check_time_disclosure(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    temporal_sources = sorted(
        sid for sid, p in ctx.profiles.items()
        if isinstance(p, dict) and p.get("hasTimeField") is True
    )
    if not temporal_sources and "temporal" not in ctx.data_semantics:
        return _Outcome(status="not_applicable", note="无时间语义数据")
    time_block = ctx.mapspec.get("time")
    if isinstance(time_block, dict) and time_block:
        return _Outcome(status="satisfied", note="mapspec.time 在场")
    refs = [f"profile://sources/{sid}/hasTimeField" for sid in temporal_sources]
    refs.append("mapspec://time")
    return _Outcome(
        status="violated",
        violations=(_violation(
            rule, severity,
            "数据携带时间语义但 spec.time 缺失：时间口径（时点/区间）不可追溯。",
            evidence={
                "temporal_sources": temporal_sources,
                "engine": "profile.hasTimeField + data_semantics",
            },
            evidence_refs=tuple(refs),
        ),),
    )


def _check_uncertainty_disclosure(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    if "uncertainty" not in ctx.data_semantics:
        return _Outcome(status="not_applicable", note="无不确定性语义字段")
    if ctx.component_types & _UNCERTAINTY_COMPONENT_TYPES:
        return _Outcome(status="satisfied", note="不确定性呈现面在场")
    return _Outcome(
        status="violated",
        violations=(_violation(
            rule, severity,
            "数据含不确定性语义但缺少 uncertainty_panel/methodology_note 呈现面。",
            evidence={"engine": "profile field-name semantics"},
            evidence_refs=("mapspec://layout/components",),
        ),),
    )


def _check_thematic_profile_declared(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    if not ctx.content_flags["has_thematic_layer"]:
        return _Outcome(status="not_applicable", note="无专题编码层")
    declared = str(ctx.mapspec.get("cartographic_profile") or "")
    if declared in ("thematic_map", "statistical_map"):
        return _Outcome(status="satisfied", note=f"cartographic_profile={declared}")
    return _Outcome(
        status="violated",
        violations=(_violation(
            rule, severity,
            "专题层在场但 cartographic_profile 未声明为 thematic_map/statistical_map。",
            evidence={"declared": declared or None, "engine": "semantic_checks._review_profile"},
            evidence_refs=("mapspec://cartographic_profile",),
        ),),
    )


_CHECKERS = {
    "required_components": _check_required_components,
    "source_disclosure": _check_source_disclosure,
    "legend_present": _check_legend_present,
    "legend_unit_disclosure": _check_legend_unit_disclosure,
    "count_vs_rate": _check_count_vs_rate,
    "classification_declared": _check_classification_declared,
    "label_density_declared": _check_label_density_declared,
    "time_disclosure": _check_time_disclosure,
    "uncertainty_disclosure": _check_uncertainty_disclosure,
    "thematic_profile_declared": _check_thematic_profile_declared,
}


def check_palette_for_medium(
    rule: CartographicRule, ctx: StandardsContext,
    profile: ProfileSpec, severity: str,
) -> _Outcome:
    """Dispatch for the two palette kinds (CVD always; print joins on medium)."""
    if rule.kind == "cvd_safe_palette":
        return _check_palette_context(rule, ctx, profile, severity, _CVD_CONTEXTS, "CVD")
    if rule.kind == "print_legible_palette":
        return _check_palette_context(rule, ctx, profile, severity, ("print",), "print")
    raise StandardsRuleDispatchError(f"palette dispatch 不认识 kind {rule.kind!r}")


class StandardsRuleDispatchError(ValueError):
    """A rule kind reached evaluation without a checker (pack/vocab drift)."""


_CHECKERS["cvd_safe_palette"] = check_palette_for_medium
_CHECKERS["print_legible_palette"] = check_palette_for_medium


__all__ = [
    "LABEL_DENSITY_FEATURE_THRESHOLD",
    "StandardsContext",
    "StandardsRuleDispatchError",
]
