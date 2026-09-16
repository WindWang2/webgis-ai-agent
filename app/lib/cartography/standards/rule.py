"""CartographicRule — the declarative atom of the standards layer (ADR-0200).

A rule never measures anything itself. ``kind`` selects one bounded check
whose *measurement* is delegated to the single existing engine implementation
(component_composer / context_matrix / semantic-checks evidence / source
profile statistics). ``fix_hint`` never mutates: it routes back to an existing
mutation vocabulary (quality_loop AUTO_SAFE operations, component autofill) or
is an explicit advisory.

Vocabularies declared here are frozen module constants; cross-module contract
tests (tests/cartography/test_standards_contract_v1.py) pin them to the
engines they name so the declaration cannot silently drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, FrozenSet, Optional, Tuple

#: Obligation axes (ADR-0200 §profile). ``purpose`` answers "why is this map
#: being made", ``audience`` answers "who reads it", ``medium`` answers
#: "where is it consumed". Empty axis = wildcard.
MAP_PURPOSES: Tuple[str, ...] = ("exploration", "analysis", "publication", "briefing")
MAP_AUDIENCES: Tuple[str, ...] = ("public", "technical", "executive", "education")
MAP_MEDIUMS: Tuple[str, ...] = ("screen", "mobile", "print")

#: Bounded data-semantics vocabulary a rule can key on (derived from source
#: profile field statistics; never from feature bodies).
DATA_SEMANTICS: Tuple[str, ...] = (
    "count", "rate", "density", "category", "continuous",
    "temporal", "uncertainty",
)

#: Bounded rule kinds. Each kind maps to exactly one measurement delegation
#: in standards/evaluate.py — adding a kind without a delegation is a
#: StandardsRuleError at rule construction.
RULE_KINDS: Tuple[str, ...] = (
    "required_components",       # delegates to component_composer.required_components_for
    "source_disclosure",         # attribution present + non-placeholder source
    "legend_present",            # thematic encoding carries a legend (component or legend_spec)
    "legend_unit_disclosure",    # graduated/continuous legend states title + unit
    "count_vs_rate",             # choropleth painting a raw-count-looking field
    "cvd_safe_palette",          # palette separable under CVD simulation
    "print_legible_palette",     # palette separable after print desaturation
    "classification_declared",   # graduated legend declares its classification method
    "label_density_declared",    # dense point layers declare a label budget
    "time_disclosure",           # temporal data declares mapspec time evidence
    "uncertainty_disclosure",    # uncertainty data carries an uncertainty surface
    "thematic_profile_declared", # thematic layers declare cartographic_profile
)

RULE_SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")

#: Where a fix hint may route. ``quality_loop`` operations must be members of
#: the AUTO_SAFE whitelist the desired-state repair executor actually applies;
#: ``component_autofill`` targets component types the required-components
#: completer already produces; ``advisory`` is explicitly non-mutating.
FIX_ROUTES: Tuple[str, ...] = ("quality_loop", "component_autofill", "advisory")

#: Mirror of the AUTO_SAFE operation whitelist in quality_loop._apply_repairs.
#: The contract test probes the executor behaviorally with each operation, so
#: this tuple cannot drift from the real whitelist without a red test.
QUALITY_LOOP_OPERATIONS: Tuple[str, ...] = (
    "normalize_opacity",
    "refresh_style_from_legend",
    "set_layer_visibility",
    "change_palette",
    "set_map_legend_visibility",
    "resolve_floating_layout",
)

_COMPONENT_TYPES_WITH_AUTOFILL: FrozenSet[str] = frozenset({
    # component_composer.required_components_for can complete these
    "title", "scale_bar", "north_arrow", "attribution",
    "legend", "graticule", "inset_map",
    # advisory-completion family (methodology/uncertainty surfaces are
    # template-backed; the autofill route carries the component type and the
    # composer/前端补全器 owns whether it can instantiate)
    "methodology_note", "uncertainty_panel",
})

_SEMVER = re.compile(r"\d+\.\d+\.\d+")

_RULE_ID = re.compile(r"[A-Z][A-Z0-9_]*(\.[A-Z][A-Z0-9_]*)*")


class StandardsRuleError(ValueError):
    """Fail-closed construction error for rule/pack declarations."""


def _frozen_tuple(value: Any, vocabulary: Tuple[str, ...], axis: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise StandardsRuleError(f"{axis} 必须是序列")
    items = tuple(value)
    for item in items:
        if not isinstance(item, str) or item not in vocabulary:
            raise StandardsRuleError(
                f"{axis} 含未知值 {item!r}（合法：{', '.join(vocabulary)}）")
    return tuple(sorted(set(items)))


@dataclass(frozen=True)
class RuleApplicability:
    """``applies_when`` — the axes that gate a rule. Empty field = wildcard.

    ``data_semantics`` is matched against the StandardsContext's derived
    semantics set (a map carrying any of the listed semantics qualifies when
    the field is non-empty).
    """

    purposes: Tuple[str, ...] = ()
    audiences: Tuple[str, ...] = ()
    mediums: Tuple[str, ...] = ()
    data_semantics: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "purposes", _frozen_tuple(self.purposes, MAP_PURPOSES, "purposes"))
        object.__setattr__(self, "audiences", _frozen_tuple(self.audiences, MAP_AUDIENCES, "audiences"))
        object.__setattr__(self, "mediums", _frozen_tuple(self.mediums, MAP_MEDIUMS, "mediums"))
        object.__setattr__(
            self, "data_semantics",
            _frozen_tuple(self.data_semantics, DATA_SEMANTICS, "data_semantics"),
        )

    def matches(
        self,
        *,
        purpose: str,
        audience: str,
        medium: str,
        data_semantics: FrozenSet[str],
    ) -> bool:
        if self.purposes and purpose not in self.purposes:
            return False
        if self.audiences and audience not in self.audiences:
            return False
        if self.mediums and medium not in self.mediums:
            return False
        if self.data_semantics and not (data_semantics & set(self.data_semantics)):
            return False
        return True

    def explain(self) -> Dict[str, Any]:
        """Deterministic, human-auditable applicability record."""
        return {
            "purposes": list(self.purposes) or "any",
            "audiences": list(self.audiences) or "any",
            "mediums": list(self.mediums) or "any",
            "data_semantics": list(self.data_semantics) or "any",
        }


def _validate_fix_hint(fix_hint: Any) -> Optional[Dict[str, Any]]:
    if fix_hint is None:
        return None
    if not isinstance(fix_hint, dict):
        raise StandardsRuleError("fix_hint 必须是 dict")
    route = fix_hint.get("route")
    if route not in FIX_ROUTES:
        raise StandardsRuleError(
            f"fix_hint.route 未知：{route!r}（合法：{', '.join(FIX_ROUTES)}）")
    if route == "quality_loop":
        operation = fix_hint.get("operation")
        if operation not in QUALITY_LOOP_OPERATIONS:
            raise StandardsRuleError(
                f"quality_loop 路由的 operation {operation!r} 不在 AUTO_SAFE 词表")
    elif route == "component_autofill":
        component_type = fix_hint.get("component_type")
        if component_type not in _COMPONENT_TYPES_WITH_AUTOFILL:
            raise StandardsRuleError(
                f"component_autofill 路由的 component_type {component_type!r} "
                "不在可补全词表")
    else:  # advisory must be honest about being non-mutating
        if fix_hint.get("operation") or fix_hint.get("component_type"):
            raise StandardsRuleError("advisory 路由不得携带 operation/component_type")
    return dict(fix_hint)


@dataclass(frozen=True)
class CartographicRule:
    """One declarative cartographic obligation.

    ``rule_id`` is the stable join key for violations, evidence and catalogs
    (UPPER_DOT case, e.g. ``CORE.LEGEND_PRESENT``). ``requires``/``conflicts_with``
    name sibling rule ids and are validated by :class:`RuleGraph` at pack
    build time — a rule alone is just data; the graph makes it a system.
    """

    rule_id: str
    kind: str
    severity: str
    message: str
    applies_when: RuleApplicability = field(default_factory=RuleApplicability)
    requires: Tuple[str, ...] = ()
    conflicts_with: Tuple[str, ...] = ()
    fix_hint: Optional[Dict[str, Any]] = None
    references: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not _RULE_ID.fullmatch(self.rule_id):
            raise StandardsRuleError(
                f"rule_id {self.rule_id!r} 必须是 UPPER.DOT 词（如 CORE.LEGEND_PRESENT）")
        if self.kind not in RULE_KINDS:
            raise StandardsRuleError(
                f"kind {self.kind!r} 未知（合法：{', '.join(RULE_KINDS)}）")
        if self.severity not in RULE_SEVERITIES:
            raise StandardsRuleError(
                f"severity {self.severity!r} 非法（合法：{', '.join(RULE_SEVERITIES)}）")
        if not isinstance(self.message, str) or not self.message:
            raise StandardsRuleError("message 必须是非空字符串")
        if not isinstance(self.applies_when, RuleApplicability):
            raise StandardsRuleError("applies_when 必须是 RuleApplicability")
        for name in ("requires", "conflicts_with", "references"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise StandardsRuleError(f"{name} 必须是非空字符串序列")
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "conflicts_with", tuple(self.conflicts_with))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "fix_hint", _validate_fix_hint(self.fix_hint))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "applies_when": self.applies_when.explain(),
            "requires": list(self.requires),
            "conflicts_with": list(self.conflicts_with),
            "fix_hint": dict(self.fix_hint) if self.fix_hint else None,
            "references": list(self.references),
        }

    def explain(self, *, effective_severity: str) -> Dict[str, Any]:
        """Obligation record for QA reports (stable + explainable)."""
        return {
            "rule_id": self.rule_id,
            "kind": self.kind,
            "severity": effective_severity,
            "message": self.message,
            "applies_when": self.applies_when.explain(),
            "references": list(self.references),
        }


__all__ = [
    "DATA_SEMANTICS",
    "FIX_ROUTES",
    "MAP_AUDIENCES",
    "MAP_MEDIUMS",
    "MAP_PURPOSES",
    "QUALITY_LOOP_OPERATIONS",
    "RULE_KINDS",
    "RULE_SEVERITIES",
    "CartographicRule",
    "RuleApplicability",
    "StandardsRuleError",
]
