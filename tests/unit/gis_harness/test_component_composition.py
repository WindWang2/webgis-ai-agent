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
    # ALGORITHM_TAXONOMY（死元数据）已删除：域分组事实源 = 域包模块 +
    # descriptor.category（真实注册内容断言，防再漂移）。
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    reg = get_algorithm_registry()
    assert any(a.category == "geometry_processing"
               for a in (reg.get(i) for i in reg.all_ids))
    assert any(a.category == "terrain_analysis"
               for a in (reg.get(i) for i in reg.all_ids))
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


# ══════════════════════════════════════════════════════════════════
# Component Library Phase-2：renderer 矩阵 / 组合校验 / conflicts 执行 /
# inset_map(planned) / Product→Composition 接线
# ══════════════════════════════════════════════════════════════════

def test_renderer_registry_matches_descriptors():
    """descriptor.renderer_support/exporter_support 与机器真值矩阵零漂移."""
    from app.lib.cartography.component_renderers import get_component_renderer_registry
    renderer_reg = get_component_renderer_registry()
    assert renderer_reg.count >= 15
    assert not renderer_reg.validate_against_descriptors()
    # 组件 registry 汇总校验也包含矩阵对账
    assert not get_component_registry().validate()


def test_renderer_registry_honesty():
    """曾被谎报的支持不再出现（P6 更新）：map_border 全链路落地（live
    CSS 框 + 导出 strokeRect）；graticule live 渲染器已落地（P3：与导出侧
    共享 graticule-math 语义）；legend 族有导出器。"""
    from app.lib.cartography.component_renderers import get_component_renderer_registry
    r = get_component_renderer_registry()
    assert r.has_renderer("graticule")             # P3：live graticule.tsx
    assert r.has_renderer("map_border")           # P6：live map-border.tsx
    assert r.has_exporter("map_border", "png")    # P6：drawChromeMapBorder
    assert r.has_exporter("graticule", "png")     # P6：spec 组件 → _drawGraticules
    assert r.has_renderer("inset_map")            # v2 P1：live inset-map.tsx
    assert r.has_exporter("inset_map", "png")     # v2 P1：drawChromeInset
    for t in ("legend", "categorical_legend", "continuous_colorbar"):
        assert r.has_renderer(t)
        # ADR-0081：图例族经 spec 组件导出（共享 resolver 路径）
        assert r.has_exporter(t, "png")
    for t in ("title", "north_arrow", "scale_bar"):
        assert r.has_exporter(t, "png")
    assert r.has_exporter("export_layout", "pdf")
    assert not r.has_renderer("export_layout")
    # v2 注记框架：annotation 导出真值（此前矩阵谎报 exporters=[]）
    assert r.has_exporter("annotation", "png")


def test_resolver_legend_family_model_aware():
    """多类型图例槽位按 map_model 解析（required 槽位同样生效）."""
    resolver = get_component_resolver()
    # proportional_symbol 配分级图例（descriptor 兼容），不是 categorical_legend
    sel = resolver.resolve(
        composition_template_id="composition.standard_analysis",
        map_model_id="proportional_symbol", output_target="interactive",
    )
    assert "legend" in sel.selected
    assert "categorical_legend" not in sel.selected
    assert "continuous_colorbar" not in sel.selected


def test_resolver_enforces_conflicts():
    """v2：图例族 type 级互斥废除 —— 同批 selection 中 legend 与 colorbar
    并存合法（各自绑定不同语义层）；descriptor.conflicts 执行路径由仍在
    冲突表中的类型对驱动（机制本身不得被误删）。"""

    from app.services.gis_harness.component_resolver import ComponentSelection

    resolver = get_component_resolver()
    comp_reg = get_component_registry()

    # 图例族共存：type 级冲突废除后双双保留
    sel = ComponentSelection(
        selected=["legend", "continuous_colorbar", "title"],
        component_templates={"legend": "legend/academic", "continuous_colorbar": "colorbar/horizontal"},
    )
    resolver._enforce_conflicts(sel, comp_reg)
    assert "continuous_colorbar" in sel.selected
    assert "legend" in sel.selected
    assert "conflict_resolution_applied" not in sel.reason_codes

    # 仍登记为互斥的类型对：剔除机制继续成立（临时 descriptor 验证）
    from app.lib.cartography.component_registry import MapComponentDescriptor

    comp_reg.register(MapComponentDescriptor(
        id="_tmp_a", category="t", type="_tmp_a", conflicts=["_tmp_b"], priority=10))
    comp_reg.register(MapComponentDescriptor(id="_tmp_b", category="t", type="_tmp_b", priority=20))
    try:
        sel2 = ComponentSelection(selected=["_tmp_a", "_tmp_b"])
        resolver._enforce_conflicts(sel2, comp_reg)
        assert "_tmp_a" in sel2.selected
        assert "_tmp_b" not in sel2.selected
        assert any(r.get("reason") == "conflict_with:_tmp_a" for r in sel2.rejected)
        assert "conflict_resolution_applied" in sel2.reason_codes
    finally:
        for tmp in ("_tmp_a", "_tmp_b"):
            comp_reg._by_id.pop(tmp, None)
            comp_reg._by_type.pop(tmp, None)


def test_resolver_selection_stays_conflict_free_for_models():
    """各 map model 的常规解析结果不含互斥图例对（回归防线）."""
    resolver = get_component_resolver()
    for model in ("visual_heatmap", "administrative_choropleth",
                  "categorical_thematic", "proportional_symbol", "aggregate_grid"):
        sel = resolver.resolve(map_model_id=model, output_target="interactive")
        legends = set(sel.selected) & {"legend", "categorical_legend", "continuous_colorbar"}
        assert len(legends) <= 1, (model, legends)


def test_resolver_records_reason_when_wired_composition_discarded():
    """不兼容的接线 composition 被丢弃时必须留下 reason code（不静默降级）."""
    resolver = get_component_resolver()
    sel = resolver.resolve(
        composition_template_id="composition.density_map",
        map_model_id="point_overlay",  # 热力降级为点图后的模型
        output_target="interactive",
    )
    assert sel.composition_template_id != "composition.density_map"
    assert "wired_composition_incompatible_model" in sel.reason_codes
    assert any(r.get("reason") == "wired_composition_incompatible_model" for r in sel.rejected)
    assert "continuous_colorbar" not in sel.selected


def test_resolver_rejects_incompatible_wired_composition():
    """显式 composition 与 map model 不兼容（降级场景）→ 自动回退选模板."""
    resolver = get_component_resolver()
    sel = resolver.resolve(
        composition_template_id="composition.density_map",
        map_model_id="point_overlay",  # 热力降级为点图后的模型
        output_target="interactive",
    )
    assert sel.composition_template_id != "composition.density_map"
    assert "continuous_colorbar" not in sel.selected


def test_resolver_rejects_planned_inset():
    """v2：inset_map 渲染器落地转 native —— 槽位仍受 required_context 门控
    （无 inset_context 不空选；bbox 由 Agent 填充，空 bbox 渲染端自弃）。"""
    from app.lib.cartography.composition_templates import get_composition_template_registry
    acad = get_composition_template_registry().get("composition.academic_map")
    assert any(s.id == "inset_map" for s in acad.component_slots)
    # 无 inset_context → 槽位不选出（missing_context）
    sel = get_component_resolver().resolve(
        composition_template_id="composition.academic_map",
        map_model_id="visual_heatmap", output_target="pdf",
    )
    assert "inset_map" not in sel.selected
    assert any(r.get("reason", "").startswith("missing_context") for r in sel.rejected)
    # 有 inset_context → 选出（native）
    sel_ctx = get_component_resolver().resolve(
        composition_template_id="composition.academic_map",
        map_model_id="visual_heatmap", output_target="pdf",
        available_context=["inset_context"],
    )
    assert "inset_map" in sel_ctx.selected


def test_inset_map_component_type_supported():
    """inset_map 进入 ComponentType union（schema 承载），CartographyComponent 可构造."""
    from app.services.gis_harness.components import ComponentType
    assert "inset_map" in set(ComponentType.__args__)
    c = CartographyComponent(id="inset-1", type="inset_map", position="top-right")
    assert c.type == "inset_map"


def test_composer_descriptor_driven_defaults():
    """composer 实例默认值（priority/position）源自 descriptor 目录."""
    resolver = get_component_resolver()
    composer = get_component_composer()
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive")
    comps = composer.compose(sel, title_text="T")
    by_type = {c.type: c for c in comps}
    assert by_type["title"].priority == 10
    assert by_type["title"].position == "top-center"
    assert by_type["continuous_colorbar"].priority == 15
    assert by_type["north_arrow"].priority == 30
    assert by_type["attribution"].priority == 50


def test_composition_validation_required_and_forbidden():
    from app.lib.cartography.composition_validation import validate_component_composition

    # density_map：colorbar required 缺席 → error
    comps = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="n", type="north_arrow", position="top-right", priority=30),
        CartographyComponent(id="s", type="scale_bar", position="bottom-right", priority=20),
        CartographyComponent(id="a", type="attribution", position="bottom-left", priority=50),
    ]
    res = validate_component_composition(
        comps, composition_template_id="composition.density_map",
        map_model_id="visual_heatmap", output_target="interactive",
    )
    assert not res.ok
    assert any(v.code == "required_slot_missing" for v in res.errors)

    # minimal_interactive：map_border forbidden 在场 → error
    comps2 = comps + [CartographyComponent(id="mb", type="map_border", position="none", priority=70)]
    res2 = validate_component_composition(
        comps2, composition_template_id="composition.minimal_interactive",
        output_target="interactive",
    )
    assert any(v.code == "forbidden_component_present" for v in res2.errors)


def test_composition_validation_conflicts_and_planned():
    from app.lib.cartography.composition_validation import validate_component_composition

    # v2：未绑定的 legend 与 colorbar 并存不再冲突（type 级互斥废除）；
    # 同一 layerId 上图例族竞争才是 binding 级冲突。
    comps = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="lg", type="legend", position="bottom-left", priority=16),
        CartographyComponent(id="cb", type="continuous_colorbar", position="bottom-right", priority=15),
    ]
    res = validate_component_composition(comps)
    assert not any(v.code == "conflicting_components" for v in res.errors)
    assert not any(v.code == "binding_conflict" for v in res.errors)

    # 同一 layerId：离散图例 + 连续色条 → binding_conflict
    comps_bound = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="lg", type="legend", position="bottom-left", priority=16,
                             options={"layerId": "heat-1"}),
        CartographyComponent(id="cb", type="continuous_colorbar", position="bottom-right", priority=15,
                             options={"layerId": "heat-1"}),
    ]
    res_bound = validate_component_composition(comps_bound)
    assert any(v.code == "binding_conflict" for v in res_bound.errors)
    # 不同 layerId：各自图例合法（Scenario A：heatmap→colorbar + choropleth→legend）
    comps_multi = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="cb", type="continuous_colorbar", position="bottom-right", priority=15,
                             options={"layerId": "heat-1"}),
        CartographyComponent(id="lg2", type="legend", position="bottom-left", priority=16,
                             options={"layerId": "district-fill"}),
    ]
    res_multi = validate_component_composition(comps_multi)
    assert not any(v.code == "binding_conflict" for v in res_multi.errors)

    # 同一 layerId 同型重复 → binding_conflict
    comps_dup = [
        CartographyComponent(id="lg", type="legend", position="bottom-left", priority=16,
                             options={"layerId": "fill-1"}),
        CartographyComponent(id="lg2", type="legend", position="bottom-left", priority=16,
                             options={"layerId": "fill-1"}),
    ]
    res_dup = validate_component_composition(comps_dup)
    assert any(v.code == "binding_conflict" for v in res_dup.errors)

    # planned 组件进入最终地图 → error（机制锁：临时把 inset_map 打回
    # planned；seed 目录 v2 已无 planned 组件 —— 渲染器落地转 native）
    from app.lib.cartography.component_registry import get_component_registry
    desc = get_component_registry().get("inset_map")
    assert desc is not None
    original_status = desc.runtime_status
    desc.runtime_status = "planned"
    try:
        comps2 = [
            CartographyComponent(id="t", type="title", position="top-center", priority=10),
            CartographyComponent(id="im", type="inset_map", position="top-right", priority=65),
        ]
        res2 = validate_component_composition(comps2)
        assert any(v.code == "planned_component_present" for v in res2.errors)
    finally:
        desc.runtime_status = original_status


def test_composition_validation_orphan_and_position():
    from app.lib.cartography.composition_validation import validate_component_composition

    comps = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(
            id="cb", type="continuous_colorbar", position="top-center", priority=15,
            options={"layerId": "ghost-layer"}),
    ]
    res = validate_component_composition(comps, layer_ids=["real-layer"])
    assert any(v.code == "orphan_layer_binding" for v in res.errors)
    assert any(v.code == "position_not_allowed" for v in res.errors)


def test_composition_validation_passes_golden_density():
    """成都小学 Golden 场景：组合产物通过校验（warning 允许）."""
    from app.lib.cartography.composition_validation import validate_component_composition

    resolver = get_component_resolver()
    composer = get_component_composer()
    sel = resolver.resolve(
        map_model_id="visual_heatmap", output_target="interactive",
        available_context=["statistics"],
    )
    comps = composer.compose(
        sel, title_text="成都小学分布",
        layer_bindings={"primary": "heatmap-1"},
    )
    res = validate_component_composition(
        comps, composition_template_id=sel.composition_template_id,
        map_model_id="visual_heatmap", layer_ids=["heatmap-1"],
        output_target="interactive",
    )
    assert res.ok, [v.detail for v in res.errors]


def test_product_templates_wired_to_compositions():
    """7 个 seed 产品模板全部声明 composition_template_id 且引用存在."""
    from app.services.gis_harness.product_templates import get_product_template_registry
    from app.lib.cartography.composition_templates import get_composition_template_registry

    products = get_product_template_registry()
    compositions = get_composition_template_registry()
    wired = [t for t in products.values() if t.composition_template_id]
    assert len(wired) == len(products.values())  # 全量接线
    for t in wired:
        assert compositions.has(t.composition_template_id), t.id


def test_product_wiring_effective_for_primary_cartography():
    """接线有效性：每个产品的接线组合在主表达模型下不被丢弃（防静默降级）."""
    from app.services.gis_harness.product_templates import get_product_template_registry

    resolver = get_component_resolver()
    products = get_product_template_registry()
    for t in products.values():
        primary = next(
            (r.resolved_map_model for r in t.layer_roles if r.role == "primary"),
            None,
        )
        if not primary or not t.composition_template_id:
            continue
        sel = resolver.resolve(
            composition_template_id=t.composition_template_id,
            map_model_id=primary,
            output_target="interactive",
        )
        assert sel.composition_template_id == t.composition_template_id, (
            f"{t.id}: wired {t.composition_template_id} discarded for {primary} "
            f"(discards={sel.rejected})")


def test_composition_validation_model_compatibility():
    """限定型组件与主表达模型错配（categorical_legend 挂 heatmap）→ error."""
    from app.lib.cartography.composition_validation import validate_component_composition

    comps = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="cl", type="categorical_legend", position="bottom-left", priority=17),
    ]
    res = validate_component_composition(comps, map_model_id="visual_heatmap")
    assert any(v.code == "model_not_compatible" for v in res.errors)


def test_composition_validation_cardinality():
    """max_count=1 的槽位出现两个同类实例 → cardinality_exceeded."""
    from app.lib.cartography.composition_validation import validate_component_composition

    comps = [
        CartographyComponent(id="t", type="title", position="top-center", priority=10),
        CartographyComponent(id="s1", type="scale_bar", position="bottom-right", priority=20),
        CartographyComponent(id="s2", type="scale_bar", position="bottom-center", priority=20),
    ]
    res = validate_component_composition(
        comps, composition_template_id="composition.density_map",
        map_model_id="visual_heatmap", output_target="interactive",
    )
    assert any(v.code == "cardinality_exceeded" for v in res.errors)


def test_composition_registry_rejects_empty_allowed_required_slot():
    """required/forbidden 槽位空类型清单 = 注册期错误（防手写模板踩坑）."""
    from app.lib.cartography.composition_templates import (
        ComponentSlot,
        MapCompositionTemplate,
        CompositionTemplateRegistry,
    )

    reg = CompositionTemplateRegistry()
    reg.register(MapCompositionTemplate(
        id="bad.required_empty", name="bad",
        component_slots=[ComponentSlot(id="x", cardinality="required", required=True)],
    ))
    issues = reg.validate()
    assert any("requires non-empty allowed_component_types" in i for i in issues)


def test_renderer_drift_checker_negative(monkeypatch):
    """对账器自身有效：撒谎的 renderer_support 必须被抓出（防检查器空转）."""
    from app.lib.cartography import component_registry as cr_module
    from app.lib.cartography.component_renderers import ComponentRendererRegistry
    from app.lib.cartography.component_registry import (
        ComponentRegistry,
        MapComponentDescriptor,
    )

    lying_reg = ComponentRegistry()
    lying_reg.register(MapComponentDescriptor(
        id="annotation", category="annotation.text", type="annotation",
        renderer_support=["interactive", "svg"],  # 矩阵真值 renderers=["interactive"]
        exporter_support=["png"],                  # 矩阵真值 exporters=[]
    ))
    monkeypatch.setattr(cr_module, "get_component_registry", lambda: lying_reg)
    drift = ComponentRendererRegistry().validate_against_descriptors()
    assert any("renderer_support" in i for i in drift)
    assert any("exporter_support" in i for i in drift)


def test_registry_validation_includes_component_system():
    """全库交叉校验覆盖组件体系且当前零违规."""
    from app.services.gis_harness.registry_validation import validate_gis_library
    assert validate_gis_library() == []
