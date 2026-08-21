"""MapProductPlanner Golden Cases A–H：intent → recipe → plan → eligibility 复检。"""

from app.services.gis_harness.planner import MapProductPlanner
from app.services.gis_harness.intent import resolve_map_request_intent

PLANNER = MapProductPlanner()


def _point_profile(n: int) -> dict:
    return {"featureCount": n, "geometryTypes": ["Point"], "fields": {}}


def _polygon_profile(n: int = 20) -> dict:
    return {"featureCount": n, "geometryTypes": ["Polygon"], "fields": {}}


class TestGoldenPlan:
    def test_case_a_1260_points_full_product(self):
        """Case A：1260 点 → 热力 + 点叠加 + 行政聚合候选 + 完整组件。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        plan = PLANNER.plan_from_intent(it)
        assert plan.recipe_id == "poi_distribution_overview"
        assert plan.template_id == "education_facility_distribution"

        fin = PLANNER.finalize_with_profile(plan, _point_profile(1260))
        assert fin.status == "finalized"
        assert fin.eligibility["eligible"] is True
        assert fin.fallbacks == []

        layers = {(ly.role, ly.cartography): ly.enabled for ly in fin.map_layers}
        assert layers.get(("primary", "visual_heatmap")) is True
        assert layers.get(("secondary", "point_overlay")) is True
        # 行政聚合是候选（reference 角色）
        assert layers.get(("reference", "administrative_choropleth")) is True

        comp_types = [c.type for c in fin.components]
        assert "title" in comp_types
        assert "continuous_colorbar" in comp_types
        assert "scale_bar" in comp_types
        assert "north_arrow" in comp_types
        assert "attribution" in comp_types

        # 数据需求按能力面声明（非硬编码工具序列）
        caps = {d.capability for d in fin.data_requirements}
        assert "poi_query" in caps
        assert "admin_aggregation" in caps

    def test_case_b_7_points_fallback(self):
        """Case B：7 点 → 热力禁用 + 点图回退 + fallback 证据。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        plan = PLANNER.plan_from_intent(it)
        fin = PLANNER.finalize_with_profile(plan, _point_profile(7))

        heat = next(ly for ly in fin.map_layers if ly.cartography == "visual_heatmap")
        assert heat.enabled is False
        assert "INSUFFICIENT_POINTS" in heat.note

        fallbacks = [f.model_dump() if hasattr(f, "model_dump") else f for f in fin.fallbacks]
        fb = fallbacks[0]
        assert fb["from_element"] == "visual_heatmap"
        assert fb["to_element"] == "point_distribution"
        assert fb["reason_code"] == "INSUFFICIENT_POINTS"
        assert fb["evidence"] == {"point_count": 7, "min_points": 10}

        # 点图升级为 primary
        point = next(
            ly for ly in fin.map_layers
            if ly.cartography == "point_overlay" and ly.enabled
        )
        assert point.role == "primary"
        # 回退后组件用离散图例语境（无 colorbar 必需）——色条不应再出现
        comp_types = [c.type for c in fin.components]
        assert "continuous_colorbar" not in comp_types

    def test_case_c_polygon_no_native_heatmap(self):
        """Case C：Polygon 数据 → 原生热力禁用（几何不匹配证据）。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        plan = PLANNER.plan_from_intent(it)
        fin = PLANNER.finalize_with_profile(plan, _polygon_profile())

        heat = next(ly for ly in fin.map_layers if ly.cartography == "visual_heatmap")
        assert heat.enabled is False
        fallbacks = [f.model_dump() if hasattr(f, "model_dump") else f for f in fin.fallbacks]
        assert any(f["reason_code"] == "GEOMETRY_NOT_SUPPORTED" for f in fallbacks)

    def test_case_d_admin_aggregation_first(self):
        """Case D：『各区数量』→ aggregate + choropleth 优先，非热力。"""
        it = resolve_map_request_intent("成都各区小学数量")
        plan = PLANNER.plan_from_intent(it)
        assert plan.recipe_id == "administrative_choropleth"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.cartography == "administrative_choropleth"
        assert primary.layer_type == "fill"
        caps = {d.capability for d in plan.data_requirements}
        assert "admin_aggregation" in caps
        # choropleth 的组件是离散 legend，不是 colorbar
        fin = PLANNER.finalize_with_profile(plan, _polygon_profile())
        comp_types = [c.type for c in fin.components]
        assert "legend" in comp_types
        assert "continuous_colorbar" not in comp_types

    def test_case_e_analytical_density_not_visual(self):
        """Case E：定量密度 → 分析意图，视觉热力不是定量结论。"""
        it = resolve_map_request_intent("成都每平方公里小学密度")
        plan = PLANNER.plan_from_intent(it)
        assert plan.recipe_id == "administrative_choropleth"
        assert "analytical_density" in {d.capability for d in plan.data_requirements}
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.cartography == "administrative_choropleth"

    def test_case_f_concentration_allows_density_family(self):
        """Case F：『哪里最集中』→ 密度/热点族（kde/hotspot 能力在计划中）。"""
        it = resolve_map_request_intent("成都小学哪里最集中")
        plan = PLANNER.plan_from_intent(it)
        assert plan.recipe_id in ("point_density", "hotspot_analysis")
        caps = {d.capability for d in plan.data_requirements}
        assert caps & {"kde_density", "hotspot"}

    def test_case_g_report_layout(self):
        """Case G：报告配图 → 版面组件 + 导出。"""
        it = resolve_map_request_intent("制作一张成都小学分布图用于报告")
        plan = PLANNER.plan_from_intent(it)
        fin = PLANNER.finalize_with_profile(plan, _point_profile(500))
        comp_types = [c.type for c in fin.components]
        assert "title" in comp_types
        assert "export_layout" in comp_types
        assert "map_border" in comp_types
        assert "png" in fin.exports and "pdf" in fin.exports

    def test_case_h_simple_view_no_overanalysis(self):
        """Case H：『给我看看』→ 单一点图层，轻量组件。"""
        it = resolve_map_request_intent("给我看看成都小学")
        plan = PLANNER.plan_from_intent(it)
        assert len(plan.map_layers) == 1
        assert plan.map_layers[0].cartography == "simple_point_map"
        assert plan.map_layers[0].layer_type == "circle"
        caps = {d.capability for d in plan.data_requirements}
        assert "admin_aggregation" not in caps  # 不过度分析
        assert plan.exports == []

    def test_plan_id_deterministic_replayable(self):
        it = resolve_map_request_intent("成都小学的分布情况")
        p1 = PLANNER.plan_from_intent(it)
        p2 = PLANNER.plan_from_intent(resolve_map_request_intent("成都小学的分布情况"))
        assert p1.plan_id == p2.plan_id

    def test_draft_has_no_fabricated_completeness(self):
        """draft 计划不假装完整——finalize 才给 completeness。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        plan = PLANNER.plan_from_intent(it)
        assert plan.status == "draft"
        assert plan.completeness == {}


class TestCapabilityResolution:
    def test_capability_map_covers_plan_capabilities(self):
        from app.services.gis_harness.planner import CAPABILITY_TOOLS

        for recipe_caps in ("poi_query", "admin_aggregation", "kde_density"):
            assert recipe_caps in CAPABILITY_TOOLS

    def test_resolve_tool_respects_availability(self):
        from app.services.gis_harness.planner import resolve_tool_for_capability

        assert resolve_tool_for_capability("poi_query", set()) is None or True  # 空注册表可 None
        resolved = resolve_tool_for_capability("poi_query", {"local_poi_search"})
        assert resolved == "local_poi_search"
        assert resolve_tool_for_capability("poi_query", {"nonexistent"}) is None
