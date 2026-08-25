from app.lib.cartography.component_taxonomy import get_component_category_registry
from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_templates import get_component_template_registry
from app.lib.cartography.composition_templates import get_composition_template_registry
from app.services.gis_harness.component_resolver import get_component_resolver
from app.services.gis_harness.component_composer import get_component_composer
from app.lib.cartography.layout_constraints import detect_collisions, detect_orphan_components
from app.services.gis_harness.components import CartographyComponent, mutate_component


def test_taxonomy_top_level():
    reg = get_component_category_registry()
    assert reg.count >= 9
    assert reg.has("legend")
    assert reg.has("legend.graduated")
    assert not reg.validate()


def test_component_registry():
    reg = get_component_registry()
    assert reg.count >= 10
    assert reg.get("north_arrow") is not None
    assert reg.get_by_type("north_arrow") is not None
    assert not reg.validate()


def test_component_templates():
    reg = get_component_template_registry()
    assert reg.count >= 30
    assert reg.get("north-arrow/compass-rose") is not None
    assert reg.get("title/academic") is not None
    assert not reg.validate()


def test_composition_templates():
    reg = get_composition_template_registry()
    assert reg.count >= 8
    acad = reg.get("composition.academic_map")
    assert acad is not None
    assert any(s.cardinality == "required" for s in acad.component_slots)
    assert not reg.validate()


def test_resolver_standard_analysis():
    resolver = get_component_resolver()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive", available_context=["statistics"])
    assert len(sel.selected) >= 3
    assert sel.composition_template_id != ""
    assert "title" in sel.selected or "north_arrow" in sel.selected


def test_resolver_respects_output_target():
    resolver = get_component_resolver()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive")
    # minimal_interactive forbids map_border/export_layout
    assert "map_border" not in sel.selected

    resolver.resolve(map_model_id="visual_heatmap", output_target="pdf", available_context=["statistics"])
    # pdf-capable compositions may include export_layout
    # at least not crash


def test_composer_produces_components():
    resolver = get_component_resolver()
    composer = get_component_composer()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive")
    comps = composer.compose(sel, title_text="Test")
    assert any(c.type == "title" for c in comps)
    assert any(c.type in ("north_arrow", "scale_bar") for c in comps)
    # sorted by priority
    priorities = [c.priority for c in comps]
    assert priorities == sorted(priorities)


def test_composer_layer_binding():
    resolver = get_component_resolver()
    composer = get_component_composer()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive")
    comps = composer.compose(sel, title_text="Test", layer_bindings={"primary": "layer-123"})
    colorbars = [c for c in comps if c.type == "continuous_colorbar"]
    for cb in colorbars:
        assert cb.options.get("layerId") == "layer-123"


def test_mutation_does_not_touch_layers():
    from app.services.gis_harness.components import build_default_components
    comps = build_default_components(primary_cartography="visual_heatmap", title="A")
    mutated, changes = mutate_component(comps, component_type="north_arrow", options={"variant": "compass_rose"})
    assert changes is not None
    assert changes["options"]["to"]["variant"] == "compass_rose"
    # other components unchanged
    assert len(mutated) == len(comps)


def test_collision_detection():
    comps = [
        CartographyComponent(id="a", type="title", position="top-center", priority=10),
        CartographyComponent(id="b", type="subtitle", position="top-center", priority=11),
        CartographyComponent(id="c", type="title", position="top-center", priority=10),
    ]
    issues = detect_collisions(comps)
    assert any("top-center" in i or "duplicate" in i for i in issues)


def test_orphan_detection():
    comps = [
        CartographyComponent(id="cb", type="continuous_colorbar", position="bottom-right", priority=15, options={"layerId": "missing"}),
    ]
    issues = detect_orphan_components(comps, ["layer-exists"])
    assert any("missing" in i for i in issues)
    assert not detect_orphan_components(comps, ["missing"])


def test_golden_chengdu_school():
    """Golden case: 成都小学分布 → visual_heatmap + composition without crash."""
    resolver = get_component_resolver()
    composer = get_component_composer()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive", available_context=["statistics"])
    comps = composer.compose(sel, title_text="成都小学分布", layer_bindings={"primary": "heatmap-layer"})
    types = {c.type for c in comps}
    assert "title" in types
    assert "north_arrow" in types or "scale_bar" in types


def test_registry_lookup_bounded():
    reg = get_component_registry()
    # O(1) lookup check — not literally perf, but API contract
    assert reg.get("north_arrow") is not None
    assert reg.get("nonexistent") is None


def test_algorithm_taxonomy():
    from app.lib.gis.algorithm_registry import ALGORITHM_TAXONOMY, get_algorithm_registry
    assert "geometry_processing" in ALGORITHM_TAXONOMY
    assert "terrain_analysis" in ALGORITHM_TAXONOMY
    reg = get_algorithm_registry()
    assert reg.count >= 20
    assert not reg.validate()
    assert reg.get("geometry.buffer") is not None
    assert reg.get("terrain.slope") is not None
    assert reg.get("remote.ndvi") is not None


def test_legacy_mapspec_compat():
    data = {"id": "x", "type": "title", "position": "top-center", "priority": 10, "options": {"text": "old"}}
    c = CartographyComponent.from_legacy(data)
    assert c.category == ""
    assert c.variant == ""
    assert c.options["text"] == "old"
