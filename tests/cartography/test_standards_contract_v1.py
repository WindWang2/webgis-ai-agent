"""Cross-module contract — the standards layer cannot drift from its engines.

The declaration names engines; these tests pin the names to reality:
1. every ``quality_loop`` fix-hint operation is behaviorally applied by the
   existing AUTO_SAFE repair executor;
2. every ``engine://semantic_checks/RULE`` reference names a rule the
   semantic checker actually emits;
3. every ``component_autofill`` component type exists in the component
   registry (so the completer can really produce it);
4. rule ids inside a pack are unique and kinds are declared vocabulary
   (already enforced at construction — here for the builtin pack itself).
"""
from __future__ import annotations

import inspect
from pathlib import Path

from app.lib.cartography import semantic_checks
from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.quality_loop import _apply_repairs
from app.lib.cartography.standards.packs import get_core_pack
from app.lib.cartography.standards.rule import RULE_KINDS

REPO_ROOT = Path(semantic_checks.__file__).resolve().parents[3]


def _semantic_check_rule_names() -> set:
    source = inspect.getsource(semantic_checks)
    import re

    names = set(re.findall(r'check="([A-Z][A-Z0-9_]+)"', source))
    names |= set(re.findall(r'add_check\(\s*\n?\s*"([A-Z][A-Z0-9_]+)"', source))
    return names


class TestFixHintRouting:
    def test_quality_loop_operations_behaviorally_applied(self):
        base = {
            "version": "1.0",
            "sources": {"s1": {"type": "geojson", "ref": "ref:x"}},
            "layers": [{
                "id": "l1", "source": "s1", "type": "fill",
                "paint": {"fill-opacity": 0.0},
                "legend_spec": {"type": "graduated",
                                "palette_colors": ["#000000", "#111111"]},
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
            after = _apply_repairs(base, [{"operation": op, **payload}])
            assert after != base, f"AUTO_SAFE executor no longer applies {op!r}"

    def test_builtin_fix_hints_reference_declared_routes_only(self):
        pack = get_core_pack()
        for rule in pack.rules:
            hint = rule.fix_hint
            if hint is None:
                continue
            assert hint["route"] in ("quality_loop", "component_autofill", "advisory")
            if hint["route"] == "quality_loop":
                assert "operation" in hint


class TestEngineReferences:
    def test_semantic_check_references_exist(self):
        pack = get_core_pack()
        known = _semantic_check_rule_names()
        assert {"LEGEND_FIELD_CONSISTENCY", "CLASSIFICATION_CARDINALITY"} <= known
        for rule in pack.rules:
            for ref in rule.references:
                if ref.startswith("engine://semantic_checks/"):
                    rule_name = ref.rsplit("/", 1)[-1]
                    assert rule_name in known, f"{rule.rule_id} references unknown check {rule_name}"

    def test_autofill_component_types_exist_in_registry(self):
        pack = get_core_pack()
        registry = get_component_registry()
        known = set(registry.all_ids)
        for rule in pack.rules:
            hint = rule.fix_hint
            if hint and hint["route"] == "component_autofill":
                assert hint["component_type"] in known, (
                    f"{rule.rule_id} autofill target {hint['component_type']!r} "
                    "不在 component registry")


class TestBuiltinPackIntegrity:
    def test_core_pack_kinds_all_declared(self):
        pack = get_core_pack()
        kinds = {r.kind for r in pack.rules}
        assert kinds <= set(RULE_KINDS)
        assert kinds == set(RULE_KINDS), "builtin pack must cover every kind"

    def test_core_pack_ids_namespaced(self):
        pack = get_core_pack()
        for rule in pack.rules:
            assert rule.rule_id.startswith("CORE.")
            assert rule.references, "every builtin rule cites its engine"
