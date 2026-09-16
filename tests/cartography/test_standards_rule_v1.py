"""Standards core contracts — CartographicRule / RuleGraph / StandardsPack (v1).

TDD red file for the declarative cartographic standards layer (ADR-0200).
Exercises the public seams only; no private attribute pokes.
"""
from __future__ import annotations

import pytest

from app.lib.cartography.standards.rule import (
    DATA_SEMANTICS,
    FIX_ROUTES,
    MAP_AUDIENCES,
    MAP_MEDIUMS,
    MAP_PURPOSES,
    QUALITY_LOOP_OPERATIONS,
    RULE_KINDS,
    RULE_SEVERITIES,
    CartographicRule,
    RuleApplicability,
    StandardsRuleError,
)
from app.lib.cartography.standards.graph import RuleGraph, RuleGraphError
from app.lib.cartography.standards.pack import (
    StandardsPack,
    StandardsPackError,
    StandardsRegistry,
)


def _rule(rule_id: str = "CORE.LEGEND_PRESENT", *, kind: str = "legend_present",
          requires: tuple = (), conflicts: tuple = (),
          severity: str = "error") -> CartographicRule:
    return CartographicRule(
        rule_id=rule_id,
        kind=kind,
        severity=severity,
        message="Thematic layer present but no legend.",
        applies_when=RuleApplicability(purposes=("publication", "analysis")),
        requires=requires,
        conflicts_with=conflicts,
        fix_hint={"route": "component_autofill", "component_type": "legend"},
        references=("ADR-0200",),
    )


class TestVocabularies:
    def test_axes_are_bounded(self):
        assert MAP_PURPOSES == ("exploration", "analysis", "publication", "briefing")
        assert MAP_MEDIUMS == ("screen", "mobile", "print")
        assert set(MAP_AUDIENCES) == {"public", "technical", "executive", "education"}

    def test_quality_loop_operations_match_existing_auto_safe_whitelist(self):
        # The declared vocabulary must equal the operations quality_loop's
        # repair executor actually accepts (behavioral probe, not source read).
        from app.lib.cartography.quality_loop import _apply_repairs

        base = {
            "version": "1.0",
            "sources": {"s1": {"type": "geojson", "ref": "ref:x"}},
            "layers": [{
                "id": "l1", "source": "s1", "type": "fill",
                "paint": {"fill-opacity": 0.0},
                "legend_spec": {"type": "graduated", "colors": ["#000", "#111"]},
            }],
            "layout": {"components": [{
                "id": "c1", "type": "legend", "enabled": True,
                "placement": {"mode": "floating", "x": 2.0, "y": 2.0,
                              "width": 0.2, "height": 0.2},
            }]},
        }
        probes = {
            "normalize_opacity": {"layer_id": "l1", "property": "fill-opacity", "value": 1.0},
            "refresh_style_from_legend": {"layer_id": "l1", "property": "fill-opacity", "value": 0.5},
            "set_layer_visibility": {"layer_id": "l1", "visible": True},
            "change_palette": {"layer_id": "l1", "value": {"colors": ["#123456", "#654321"]}},
            "set_map_legend_visibility": {"value": True},
            "resolve_floating_layout": {"placements": [{"component_id": "c1", "x": 0.1, "y": 0.1}]},
        }
        for op, payload in probes.items():
            repair = {"operation": op, **payload}
            after = _apply_repairs(base, [repair])
            assert after != base, f"quality_loop no longer applies {op!r}"

    def test_rule_kinds_cover_the_standards_surface(self):
        assert set(RULE_KINDS) == {
            "required_components", "source_disclosure", "legend_present",
            "legend_unit_disclosure", "count_vs_rate", "cvd_safe_palette",
            "print_legible_palette", "classification_declared",
            "label_density_declared", "time_disclosure",
            "uncertainty_disclosure", "thematic_profile_declared",
        }
        assert set(RULE_SEVERITIES) == {"info", "warning", "error"}
        assert set(FIX_ROUTES) == {"quality_loop", "component_autofill", "advisory"}
        assert {"count", "rate", "density", "temporal", "uncertainty"} <= set(DATA_SEMANTICS)


class TestCartographicRule:
    def test_frozen_and_deterministic_projection(self):
        rule = _rule()
        with pytest.raises(Exception):
            rule.severity = "info"  # type: ignore[misc]
        assert rule.to_dict() == rule.to_dict()

    def test_invalid_kind_rejected(self):
        with pytest.raises(StandardsRuleError):
            CartographicRule(
                rule_id="X", kind="not_a_kind", severity="error",
                message="m", applies_when=RuleApplicability(),
            )

    def test_invalid_severity_rejected(self):
        with pytest.raises(StandardsRuleError):
            CartographicRule(
                rule_id="X", kind="legend_present", severity="fatal",
                message="m", applies_when=RuleApplicability(),
            )

    def test_fix_hint_route_must_be_known(self):
        with pytest.raises(StandardsRuleError):
            CartographicRule(
                rule_id="X", kind="legend_present", severity="error", message="m",
                applies_when=RuleApplicability(),
                fix_hint={"route": "direct_mutation", "operation": "change_palette"},
            )

    def test_quality_loop_fix_hint_operation_must_be_auto_safe(self):
        with pytest.raises(StandardsRuleError):
            CartographicRule(
                rule_id="X", kind="cvd_safe_palette", severity="error", message="m",
                applies_when=RuleApplicability(),
                fix_hint={"route": "quality_loop", "operation": "rewrite_geometry"},
            )

    def test_applicability_matching_and_explanation(self):
        rule = _rule()
        assert rule.applies_when.matches(
            purpose="publication", audience="public", medium="print",
            data_semantics=frozenset({"count"}))
        assert not rule.applies_when.matches(
            purpose="exploration", audience="public", medium="screen",
            data_semantics=frozenset())
        empty = RuleApplicability()
        assert empty.matches(
            purpose="briefing", audience="executive", medium="mobile",
            data_semantics=frozenset({"temporal"}))
        assert rule.applies_when.explain()["purposes"] == ["analysis", "publication"]


class TestRuleGraph:
    def test_topological_order_and_stable_ties(self):
        a = _rule("CORE.A.LEGEND", kind="legend_present")
        b = _rule("CORE.B.UNIT", kind="legend_unit_disclosure", requires=("CORE.A.LEGEND",))
        c = _rule("CORE.C.CVD", kind="cvd_safe_palette", requires=("CORE.A.LEGEND",))
        graph = RuleGraph.build((b, a, c))
        order = [r.rule_id for r in graph.evaluation_order()]
        assert order.index("CORE.A.LEGEND") < order.index("CORE.B.UNIT")
        assert order.index("CORE.A.LEGEND") < order.index("CORE.C.CVD")
        # deterministic across constructions
        assert order == [r.rule_id for r in RuleGraph.build((c, b, a)).evaluation_order()]

    def test_cycle_is_fail_closed(self):
        a = _rule("CORE.A", requires=("CORE.B",))
        b = _rule("CORE.B", requires=("CORE.A",))
        with pytest.raises(RuleGraphError):
            RuleGraph.build((a, b))

    def test_unknown_dependency_rejected(self):
        with pytest.raises(RuleGraphError):
            RuleGraph.build((_rule("CORE.A", requires=("CORE.GHOST",)),))

    def test_conflicts_are_undirected_and_validated(self):
        a = _rule("CORE.A", conflicts=("CORE.B",))
        b = _rule("CORE.B")
        graph = RuleGraph.build((a, b))
        assert graph.conflicts_with("CORE.A") == frozenset({"CORE.B"})
        assert graph.conflicts_with("CORE.B") == frozenset({"CORE.A"})
        with pytest.raises(RuleGraphError):
            RuleGraph.build((_rule("CORE.A", conflicts=("CORE.GHOST",)),))
        with pytest.raises(RuleGraphError):
            RuleGraph.build((_rule("CORE.A", requires=("CORE.B",), conflicts=("CORE.B",)),
                             _rule("CORE.B")))

    def test_conflict_evaluation_emits_dual_disclosure(self):
        a = _rule("CORE.A", conflicts=("CORE.B",))
        b = _rule("CORE.B")
        graph = RuleGraph.build((a, b))
        violations = graph.conflict_violations(applied={"CORE.A", "CORE.B"})
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_id == "RULE_CONFLICT"
        assert set(v.evidence["rule_ids"]) == {"CORE.A", "CORE.B"}
        assert graph.conflict_violations(applied={"CORE.A"}) == ()


class TestStandardsPack:
    def test_versioned_fingerprint_stable(self):
        pack = StandardsPack.build(pack_id="core", version="1.0.0",
                                   rules=(_rule(), _rule("CORE.B.UNIT")))
        other = StandardsPack.build(pack_id="core", version="1.0.0",
                                    rules=(_rule("CORE.B.UNIT"), _rule()))
        assert pack.fingerprint == other.fingerprint
        assert pack.fingerprint.startswith("stdpack-sha256:")
        with pytest.raises(StandardsPackError):
            StandardsPack.build(pack_id="core", version="1.0", rules=(_rule(),))

    def test_registry_resolution_exact_and_latest(self):
        registry = StandardsRegistry()
        v1 = StandardsPack.build(pack_id="core", version="1.0.0", rules=(_rule(),))
        v110 = StandardsPack.build(pack_id="core", version="1.1.0", rules=(_rule(),))
        registry.register(v1)
        registry.register(v110)
        assert registry.resolve("core", "1.0.0") is v1
        assert registry.resolve("core") is v110
        assert registry.resolve("core", "9.9.9", required=False) is None
        with pytest.raises(StandardsPackError):
            registry.resolve("ghost")
        with pytest.raises(StandardsPackError):
            registry.register(v1)  # duplicate id+version fail-closed

    def test_builtin_core_pack_loads_from_registry(self):
        from app.lib.cartography.standards.packs import get_core_pack

        pack = get_core_pack()
        assert pack.pack_id == "core"
        assert pack.version.count(".") == 2
        assert len(pack.rules) >= 12
        # every builtin rule kind is represented at least once
        assert set(RULE_KINDS) <= {r.kind for r in pack.rules}
        # graph construction of the builtin set is fail-closed clean
        order = [r.rule_id for r in pack.graph().evaluation_order()]
        assert sorted(order) == sorted(r.rule_id for r in pack.rules)
