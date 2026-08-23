"""端到端（planner 级）编排 Golden Cases（任务 §29 Case A–G）。

锁定的链路：Intent → Recipe → Capability（CapabilityRegistry）→
Algorithm（AlgorithmRegistry/Resolver）→ Artifact 语义 → MapModel →
Template（TemplateSelector）→ MapProductPlan → eligibility 回退。
"""

from app.lib.gis.algorithm_resolver import get_algorithm_resolver
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner


def _plan(query: str, **kwargs):
    intent = resolve_map_request_intent(query)
    planner = MapProductPlanner()
    plan = planner.plan_from_intent(intent, **kwargs)
    return intent, planner, plan


def _selection(plan, capability):
    return next(
        (r for r in plan.algorithm_selections if r.capability == capability), None)


class TestCaseADistributionOverview:
    def test_case_a_成都小学的分布情况(self):
        intent, planner, plan = _plan("成都小学的分布情况")
        assert intent.task == "distribution_overview"
        assert plan.recipe_id == "poi_distribution_overview"
        # 教育主体 → education 产品模板（subject 亲和）
        assert plan.template_id == "education_facility_distribution"
        cartos = [(ly.role, ly.cartography, ly.layer_type) for ly in plan.map_layers]
        assert ("primary", "visual_heatmap", "heatmap") in cartos
        assert any(c == ("secondary", "point_overlay", "circle") for c in cartos)
        assert any(c[1] == "administrative_choropleth" and c[2] == "fill" for c in cartos)
        # capability → algorithm → tool 裁决证据
        sel = _selection(plan, "poi_query")
        assert sel and sel.status == "resolved"
        assert sel.algorithm == "poi.query.local"
        assert sel.tool == "query_local_poi"
        # 模板选择证据可解释
        assert plan.template_selection["template_id"] == "education_facility_distribution"
        assert "subject_match" in plan.template_selection["decision"]["reason"]
        # 终稿组件：colorbar/title/scale/north
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 1260})
        comp_types = {c.type for c in final.components}
        assert {"continuous_colorbar", "title", "scale_bar", "north_arrow"} <= comp_types


class TestCaseBAdminStatistic:
    def test_case_b_成都各区小学数量(self):
        intent, planner, plan = _plan("成都各区小学数量")
        assert intent.task == "administrative_statistic"
        assert plan.recipe_id == "administrative_choropleth"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.cartography == "administrative_choropleth"
        assert primary.layer_type == "fill"
        # 主表达不得是 heatmap
        assert all(ly.layer_type != "heatmap" for ly in plan.map_layers)
        sel = _selection(plan, "admin_aggregation")
        assert sel and sel.algorithm == "spatial.aggregate.admin"
        assert sel.tool == "spatial_aggregate"
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 500})
        comp_types = {c.type for c in final.components}
        assert "legend" in comp_types and "continuous_colorbar" not in comp_types


class TestCaseCAnalyticalDensity:
    def test_case_c_成都每平方公里小学密度(self):
        intent, planner, plan = _plan("成都每平方公里小学密度")
        assert intent.task == "analytical_density"
        assert plan.recipe_id == "administrative_choropleth"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.layer_type == "fill"
        # analytical_density 能力在计划里，且不得由视觉热力承载
        sel = _selection(plan, "analytical_density")
        assert sel and sel.status == "resolved"
        assert sel.algorithm == "density.analytical.mixed"
        assert all(ly.layer_type != "heatmap" for ly in plan.map_layers)


class TestCaseDConcentration:
    def test_case_d_成都小学哪里最集中(self):
        intent, _, plan = _plan("成都小学哪里最集中")
        assert intent.task == "concentration_analysis"
        assert plan.recipe_id in ("point_density", "hotspot_analysis")
        caps = {r.capability for r in plan.data_requirements}
        assert caps & {"kde_density", "hotspot"}
        kde_sel = _selection(plan, "kde_density")
        assert kde_sel and kde_sel.algorithm == "spatial.kde.contours"


class TestCaseESimpleView:
    def test_case_e_给我看看成都小学(self):
        intent, planner, plan = _plan("给我看看成都小学")
        assert intent.task == "simple_view"
        assert plan.template_id == "simple_poi_view"
        assert len(plan.map_layers) == 1
        assert plan.map_layers[0].cartography == "simple_point_map"
        assert plan.map_layers[0].layer_type == "circle"
        caps = {r.capability for r in plan.data_requirements}
        assert "admin_aggregation" not in caps  # 不过度分析
        assert plan.exports == []


class TestCaseFGridBinning:
    def test_case_f_成都小学六边形网格统计_prefers_h3(self):
        intent, planner, plan = _plan("成都小学六边形网格统计")
        assert "grid_binning" in intent.analysis_intents
        assert plan.recipe_id == "grid_density_aggregate"
        assert plan.template_id == "grid_density_product"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.cartography == "aggregate_grid"
        assert primary.source_capability == "grid_binning"
        sel = _selection(plan, "grid_binning")
        assert sel and sel.algorithm == "spatial.grid.h3"
        assert sel.tool == "h3_binning"

    def test_case_f_h3_unavailable_falls_back_to_fishnet(self):
        resolver = get_algorithm_resolver()
        res = resolver.resolve(
            "grid_binning", available_tools={"fishnet_grid", "query_local_poi"})
        assert res.status == "resolved"
        assert res.tool == "fishnet_grid"
        assert res.algorithm == "spatial.grid.fishnet"
        assert any("tool_unavailable:spatial.grid.h3" in x for x in res.rejected)
        # planner 层同样回退（available_tools 视图）
        intent = resolve_map_request_intent("成都小学六边形网格统计")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(
            intent, available_tools={"fishnet_grid", "query_local_poi"})
        sel = _selection(plan, "grid_binning")
        assert sel.tool == "fishnet_grid"

    def test_case_f_small_sample_grid_disabled_keeps_reason(self):
        intent, planner, plan = _plan("成都小学六边形网格统计")
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 12})
        grid_fb = next(
            (fb for fb in final.fallbacks if fb.from_element == "aggregate_grid"), None)
        assert grid_fb is not None
        assert grid_fb.reason_code == "INSUFFICIENT_POINTS"
        assert grid_fb.evidence["min_points"] == 20
        assert grid_fb.evidence["point_count"] == 12


class TestCaseGSmallSample:
    def test_case_g_7_points_heatmap_rejected_to_point_map(self):
        intent, planner, plan = _plan("成都小学的分布情况")
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 7})
        heat = next(
            ly for ly in final.map_layers if ly.cartography == "visual_heatmap")
        assert heat.enabled is False
        fb = next(
            (fb for fb in final.fallbacks if fb.from_element == "visual_heatmap"), None)
        assert fb is not None
        assert fb.reason_code == "INSUFFICIENT_POINTS"
        assert fb.evidence["point_count"] == 7
        assert fb.evidence["min_points"] == 10
        primary = next(ly for ly in final.map_layers if ly.role == "primary")
        assert primary.cartography == "point_overlay"
        # 算法级证据同源解释：7 点跑不了视觉热力（resolver 硬门槛）
        resolver_density = get_algorithm_resolver().resolve(
            "density_surface",
            profile={"geometryTypes": ["Point"], "featureCount": 7},
            available_tools={"heatmap_data"})
        assert resolver_density.status == "unavailable"
        assert resolver_density.rejected == [
            "insufficient_features:density.visual.heatmap:7<10"]
        # 回退后组件集不再带 colorbar
        comp_types = {c.type for c in final.components}
        assert "continuous_colorbar" not in comp_types


class TestOrchestrationEvidence:
    def test_plan_carries_registry_evidence(self):
        _, _, plan = _plan("成都小学的分布情况")
        assert plan.algorithm_selections, "algorithm selection evidence missing"
        assert plan.template_selection.get("template_id")
        assert plan.map_model_selection, "map model evidence missing"
        for entry in plan.map_model_selection:
            assert entry["map_model"] == entry["cartography"] or entry["map_model"]
        heat_entry = next(
            e for e in plan.map_model_selection if e["cartography"] == "visual_heatmap")
        assert heat_entry["map_model"] == "visual_heatmap"
        assert heat_entry["layer_type"] == "heatmap"
        assert heat_entry["source"] == "map_model_registry"

    def test_planner_has_no_hardcoded_tool_names(self):
        """planner 主规划代码不得再硬编码具体工具名（registry 裁决）。"""
        import inspect
        from app.services.gis_harness import planner as planner_mod
        source = inspect.getsource(planner_mod)
        for ghost in ("query_local_poi", "h3_binning", "fishnet_grid",
                      "kde_contours", "heatmap_data", "spatial_aggregate"):
            assert ghost not in source, (
                f"planner 硬编码工具名 {ghost} —— 应由 AlgorithmRegistry 裁决")
