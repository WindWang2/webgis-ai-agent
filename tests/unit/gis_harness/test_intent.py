"""MapRequestIntent 确定性 resolver 契约测试（Golden Cases 意图层）。"""

from app.services.gis_harness.intent import (
    MapRequestIntent,
    merge_intent_hints,
    resolve_map_request_intent,
)


class TestGoldenCaseIntent:
    def test_case_a_distribution_overview(self):
        """Case A『成都小学的分布情况』→ 分布概览任务 + 完整意图族。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        assert it.task == "distribution_overview"
        assert it.scope.name == "成都" and it.scope.level == "city"
        assert it.subject.type == "poi" and it.subject.category == "小学"
        assert it.geometry_expectation == "point"
        assert "density_overview" in it.cartography_intents
        assert "point_overlay" in it.cartography_intents
        assert "administrative_choropleth" in it.cartography_intents
        assert "spatial_distribution" in it.analysis_intents
        assert "administrative_summary" in it.analysis_intents
        assert "map" in it.output_intents and "statistics" in it.output_intents
        assert it.confidence >= 0.8
        assert it.matched_rules  # 可审计

    def test_case_d_administrative_statistic(self):
        """Case D『成都各区小学数量』→ 行政聚合统计，而非热力。"""
        it = resolve_map_request_intent("成都各区小学数量")
        assert it.task == "administrative_statistic"
        assert it.group_by == "district"
        assert it.cartography_intents[0] == "administrative_choropleth"
        assert "administrative_aggregation" in it.analysis_intents
        # 热力不是首选表达
        assert "density_overview" not in it.cartography_intents[:1]

    def test_case_e_analytical_density(self):
        """Case E『成都每平方公里小学密度』→ 定量密度意图，非视觉热力。"""
        it = resolve_map_request_intent("成都每平方公里小学密度")
        assert it.task == "analytical_density"
        assert it.measure == "density"
        assert "analytical_density" in it.analysis_intents
        assert "administrative_choropleth" in it.cartography_intents
        assert "density_overview" not in it.cartography_intents

    def test_case_e_hint_cannot_downgrade(self):
        """定量密度不可被 LLM hint 降级为视觉任务（护栏）。"""
        base = resolve_map_request_intent("成都每平方公里小学密度")
        merged = merge_intent_hints(base, {"task": "distribution_overview"})
        assert merged.task == "analytical_density"
        assert any("rejected" in h for h in merged.hint_applied)

    def test_case_f_concentration(self):
        """Case F『成都小学哪里最集中』→ 集中/热点语义。"""
        it = resolve_map_request_intent("成都小学哪里最集中")
        assert it.task == "concentration_analysis"
        assert "kde_density" in it.analysis_intents or "hotspot" in it.analysis_intents

    def test_case_g_report_product(self):
        """Case G『制作一张成都小学分布图用于报告』→ 报告产品意图。"""
        it = resolve_map_request_intent("制作一张成都小学分布图用于报告")
        assert it.report_product is True
        assert "png" in it.export_intents and "pdf" in it.export_intents
        assert "export" in it.output_intents

    def test_case_h_simple_view(self):
        """Case H『给我看看成都小学』→ 轻量查看，不过度分析。"""
        it = resolve_map_request_intent("给我看看成都小学")
        assert it.task == "simple_view"
        assert it.cartography_intents == ["simple_point_map"]
        assert it.confidence <= 0.75  # 不装作高置信

    def test_proximity(self):
        it = resolve_map_request_intent("成都小学周边500米内的公交站")
        assert it.task == "proximity_analysis"
        assert "proximity_buffer" in it.analysis_intents

    def test_determinism(self):
        """同一输入 → 同一输出（可回放）。"""
        a = resolve_map_request_intent("成都小学的分布情况")
        b = resolve_map_request_intent("成都小学的分布情况")
        assert a.model_dump() == b.model_dump()

    def test_serializable_typed(self):
        it = resolve_map_request_intent("北京医院分布情况")
        dumped = it.model_dump()
        restored = MapRequestIntent.model_validate(dumped)
        assert restored == it

    def test_unknown_query_defaults_with_assumptions(self):
        it = resolve_map_request_intent("嗯")
        assert it.task == "distribution_overview"
        assert it.confidence <= 0.7
        assert it.assumptions  # 假设显式记录，不静默

    def test_district_scope(self):
        it = resolve_map_request_intent("锦江区小学分布")
        assert it.scope.name == "锦江区" and it.scope.level == "district"

    def test_groupby_not_scope(self):
        """『各区』是分组信号，不应被误判为 scope。"""
        it = resolve_map_request_intent("成都各区小学数量")
        assert it.scope.name == "成都"
        assert it.group_by == "district"


class TestRecipeSelection:
    def test_case_a_selects_poi_distribution_recipe(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        it = resolve_map_request_intent("成都小学的分布情况")
        candidates = get_recipe_registry().select_candidates(it)
        assert candidates[0].id == "poi_distribution_overview"

    def test_case_d_prefers_admin_choropleth(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        it = resolve_map_request_intent("成都各区小学数量")
        candidates = get_recipe_registry().select_candidates(it)
        assert candidates[0].id == "administrative_choropleth"

    def test_case_e_prefers_admin_choropleth_not_heatmap(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        it = resolve_map_request_intent("成都每平方公里小学密度")
        candidates = get_recipe_registry().select_candidates(it)
        assert candidates[0].id == "administrative_choropleth"

    def test_case_f_allows_density_or_hotspot(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        it = resolve_map_request_intent("成都小学哪里最集中")
        candidates = get_recipe_registry().select_candidates(it)
        assert candidates[0].id in ("point_density", "hotspot_analysis")

    def test_registry_indexed_o1(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        reg = get_recipe_registry()
        for rid in reg.all_ids:
            assert rid in reg
            assert reg.get(rid) is not None
        assert reg.get("nonexistent") is None

    def test_seed_recipes_cover_required_list(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        required = {
            "poi_distribution_overview", "point_density",
            "administrative_choropleth", "categorical_distribution",
            "hotspot_analysis", "proximity_analysis",
            "accessibility_analysis", "raster_distribution",
        }
        assert required <= set(get_recipe_registry().all_ids)
