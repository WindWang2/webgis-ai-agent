"""TemplateCatalog / TemplateSelector —— 统一模板门面与确定性选择器单测。"""

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.template_catalog import get_template_catalog
from app.services.gis_harness.template_selector import TemplateSelector


class TestTemplateCatalog:
    def test_unified_lookup_product_and_style(self):
        catalog = get_template_catalog()
        assert catalog.get_product_template("education_facility_distribution") is not None
        assert catalog.get_product_template("nope") is None
        style = catalog.get_style_template("tmpl_th_heatmap")
        assert style is not None and style["kind"] == "thematic"
        assert catalog.get_style_template("") is None

    def test_find_product_candidates_deterministic(self):
        catalog = get_template_catalog()
        candidates = catalog.find_product_candidates("poi_distribution_overview")
        ids = [t.id for t in candidates]
        assert "poi_distribution_overview" in ids
        assert "education_facility_distribution" in ids
        assert catalog.find_product_candidates("no_such_recipe") == []

    def test_style_candidates_by_map_model(self):
        catalog = get_template_catalog()
        heat = catalog.find_style_candidates(map_model="visual_heatmap")
        assert any(e["id"] == "tmpl_th_heatmap" for e in heat)
        choro = catalog.find_style_candidates(map_model="administrative_choropleth")
        assert any(e["id"] == "tmpl_th_pop_choro" for e in choro)

    def test_style_candidates_geometry_filter(self):
        catalog = get_template_catalog()
        point_styles = catalog.find_style_candidates(geometry="point")
        assert point_styles  # symbology point 族
        poly_only = catalog.find_style_candidates(
            map_model="administrative_choropleth", geometry="point")
        assert poly_only == []  # choropleth 族声明 polygon

    def test_inferred_compatibility_does_not_mutate_registry(self):
        catalog = get_template_catalog()
        from app.schemas.template_registry import get_template_registry
        entry = get_template_registry().get("tmpl_th_heatmap")
        assert "compatibility" not in entry  # 注册表数据未被改写
        compat = catalog.style_compatibility(entry)
        assert "visual_heatmap" in compat.compatible_map_models

    def test_explicit_compatibility_validated_by_registry(self):
        from app.schemas.template_registry import (
            reset_template_registry, get_template_registry,
        )
        reset_template_registry()
        registry = get_template_registry()
        registry.add_user_template({
            "id": "tmpl_bad_compat", "kind": "thematic", "name": "bad",
            "keywords": [], "payload": {"variant": "heatmap"},
            "compatibility": {"compatible_map_models": ["ghost_model"]},
        })
        issues = registry.validate()
        try:
            assert any("ghost_model" in i for i in issues)
        finally:
            registry.remove("tmpl_bad_compat")

    def test_resolve_composite(self):
        catalog = get_template_catalog()
        expanded = catalog.resolve_composite("composite_population_density_analysis")
        assert set(expanded) >= {"basemap", "symbology", "thematic", "layout"}
        assert catalog.resolve_composite("nope") == {}

    def test_catalog_validate_clean(self):
        assert get_template_catalog().validate() == []


class TestTemplateSelectorProduct:
    def selector(self) -> TemplateSelector:
        return TemplateSelector()

    def test_subject_match_wins_education_template(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        selection = self.selector().select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        assert selection.status == "selected"
        assert selection.template_id == "education_facility_distribution"

    def test_generic_subject_gets_default_template(self):
        intent = resolve_map_request_intent("成都餐厅的分布情况")
        selection = self.selector().select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        assert selection.template_id == "poi_distribution_overview"

    def test_simple_view_affinity(self):
        intent = resolve_map_request_intent("给我看看成都小学")
        selection = self.selector().select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        assert selection.template_id == "simple_poi_view"

    def test_no_candidates_returns_none(self):
        intent = resolve_map_request_intent("成都小学哪里最集中")
        selection = self.selector().select_product(
            intent=intent, recipe_id="point_density")
        assert selection.status == "none"
        assert selection.decision["reason"] == "no_product_template_for_recipe"

    def test_export_overlap_scores(self):
        intent = resolve_map_request_intent("成都小学分布报告")
        selection = self.selector().select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        assert selection.status == "selected"
        # 候选排名有界且带 reason
        assert len(selection.candidates) <= 8
        assert all(c.reasons for c in selection.candidates)

    def test_decision_evidence_explains_rejection(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        selection = self.selector().select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        assert selection.decision["reason"]
        assert "rejected" in selection.decision

    def test_profile_geometry_soft_penalty(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        selector = self.selector()
        no_profile = selector.select_product(
            intent=intent, recipe_id="poi_distribution_overview")
        polygon_profile = selector.select_product(
            intent=intent, recipe_id="poi_distribution_overview",
            profile={"geometryTypes": ["Polygon"], "featureCount": 20})
        # 面数据上热力主模板被软罚分，但不被否决（finalize 阶段才硬裁决）
        heat_score_no_profile = next(
            c.score for c in no_profile.candidates
            if c.template_id == "education_facility_distribution")
        heat_score_polygon = next(
            c.score for c in polygon_profile.candidates
            if c.template_id == "education_facility_distribution")
        assert heat_score_polygon < heat_score_no_profile

    def test_deterministic(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        selector = self.selector()
        a = selector.select_product(intent=intent, recipe_id="poi_distribution_overview")
        b = selector.select_product(intent=intent, recipe_id="poi_distribution_overview")
        assert a.model_dump() == b.model_dump()


class TestTemplateSelectorStyle:
    def test_heatmap_style_for_visual_heatmap(self):
        selector = TemplateSelector()
        selection = selector.select_style_for_layer(map_model="visual_heatmap")
        assert selection.status == "selected"
        assert selection.template_id  # 有样式候选
        assert len(selection.candidates) <= 3

    def test_none_for_unknown_model(self):
        selection = TemplateSelector().select_style_for_layer(map_model="ghost")
        assert selection.status == "none"


# ── P7：模板/配方整合守卫 ────────────────────────────────────────────


def test_seed_templates_no_structural_near_duplicates():
    """P7：非 deprecated 种子模板不得结构重复（同 recipe + composition +
    图层角色签名）—— school/hospital/earthquake 式垂直模板复发的守卫。
    组件集差异属于 composition 模板职责，不构成新产品模板。"""
    from app.services.gis_harness.product_templates import SEED_PRODUCT_TEMPLATES

    sigs = {}
    for tpl in SEED_PRODUCT_TEMPLATES:
        if tpl.deprecated:
            continue
        sig = (
            tpl.recipe_id,
            tpl.composition_template_id,
            tuple(
                (r.role, r.cartography, r.source_capability)
                for r in tpl.layer_roles
            ),
        )
        assert sig not in sigs, (
            f"structural near-duplicate product templates: "
            f"{sigs[sig].id} vs {tpl.id}"
        )
        sigs[sig] = tpl


def test_deprecated_template_resolves_but_not_selected():
    """deprecated 模板：旧持久会话的 template_id 仍可解析（迁移兼容），
    但 find_for_recipe 兜底不再选中它。"""
    from app.services.gis_harness.product_templates import (
        get_product_template_registry,
    )

    reg = get_product_template_registry()
    # 兼容解析（alias/migration 语义）
    assert reg.get("poi_density_overview") is not None
    assert reg.get("poi_density_overview").deprecated is True
    # 新计划选择走非 deprecated 泛型模板
    chosen = reg.find_for_recipe("poi_distribution_overview")
    assert chosen is not None and not chosen.deprecated
    assert chosen.id == "poi_distribution_overview"
