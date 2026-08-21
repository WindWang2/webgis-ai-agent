"""模型库 → GIS Harness 注入测试。

覆盖：intent 制图形态信号（格网/气泡）、新 recipe 的选择与资格降级、
planner 图层映射与能力解析、组件派生 —— 全部确定性断言。
"""
import pytest

from app.services.cartography_service import CartographyService  # noqa: F401 — 触发注册
from app.services.gis_harness.components import build_default_components
from app.services.gis_harness.intent import merge_intent_hints, resolve_map_request_intent
from app.services.gis_harness.planner import (
    MapProductPlanner,
    resolve_tool_for_capability,
)
from app.services.gis_harness.recipes import (
    check_eligibility,
    get_recipe_registry,
)


def _profile(count: int, geom="Point") -> dict:
    return {"featureCount": count, "geometryTypes": [geom], "fields": {}}


class TestIntentCartographySignals:
    def test_grid_word_injects_aggregate_grid(self):
        it = resolve_map_request_intent("成都小学按1公里格网统计分布")
        assert "aggregate_grid" in it.cartography_intents
        assert "grid_binning" in it.analysis_intents
        assert "cartography:aggregate_grid" in it.matched_rules

    def test_bubble_word_injects_proportional_symbol(self):
        it = resolve_map_request_intent("用气泡图展示成都小学数量")
        assert "proportional_symbol" in it.cartography_intents
        assert "cartography:proportional_symbol" in it.matched_rules

    def test_hexbin_alias_signal(self):
        it = resolve_map_request_intent("成都咖啡馆 hexbin 蜂窝图")
        assert "aggregate_grid" in it.cartography_intents

    def test_grid_signal_is_additive_to_task_authority(self):
        """形态词只加表达意图，不改任务判定（analytical_density 权威不变）。"""
        it = resolve_map_request_intent("成都每平方公里小学密度按六边形网格统计")
        assert it.task == "analytical_density"
        assert "aggregate_grid" in it.cartography_intents

    def test_hint_cannot_downgrade_analytical_density_with_grid_words(self):
        base = resolve_map_request_intent("成都每平方公里小学密度按六边形网格统计")
        merged = merge_intent_hints(base, {"task": "distribution_overview"})
        assert merged.task == "analytical_density"

    def test_plain_query_has_no_new_signals(self):
        it = resolve_map_request_intent("成都小学的分布情况")
        assert "aggregate_grid" not in it.cartography_intents
        assert "proportional_symbol" not in it.cartography_intents


class TestNewRecipeSelectionAndEligibility:
    def test_registry_now_has_ten_recipes(self):
        reg = get_recipe_registry()
        expected_new = {"grid_density_aggregate", "proportional_symbol_map"}
        assert expected_new <= set(reg.all_ids)
        assert reg.count == 10

    @pytest.mark.parametrize(
        "query,winner", [
            ("成都小学按1公里格网统计分布", "grid_density_aggregate"),
            ("用气泡图展示成都小学数量", "proportional_symbol_map"),
            # 回归锚：Golden Case A/D/E 的首选不受新 recipe 影响
            ("成都小学的分布情况", "poi_distribution_overview"),
            ("成都各区小学数量", "administrative_choropleth"),
            ("成都每平方公里小学密度", "administrative_choropleth"),
        ],
    )
    def test_selection_ranking(self, query, winner):
        intent = resolve_map_request_intent(query)
        candidates = get_recipe_registry().select_candidates(intent)
        assert candidates[0].id == winner

    def test_grid_eligibility_min_points_20(self):
        recipe = get_recipe_registry().get("grid_density_aggregate")
        ok = check_eligibility(recipe, profile=_profile(25))
        assert ok.eligible
        assert not ok.disabled

        bad = check_eligibility(recipe, profile=_profile(7))
        # 元素级降级（模型库 <20 点时噪声>信号）：recipe 仍 eligible，
        # 但 aggregate_grid 被禁且产出 INSUFFICIENT_POINTS 回退（同现有
        # 热力 7 点测试的契约：visual_heatmap 禁用 ≠ recipe 不合格）。
        assert bad.eligible
        disabled = next(d for d in bad.disabled if d.element == "aggregate_grid")
        assert disabled.reason_code == "INSUFFICIENT_POINTS"
        assert disabled.evidence["min_points"] == 20
        fb = next(
            f for f in bad.fallbacks if f.reason_code == "INSUFFICIENT_POINTS"
        )
        assert fb.to_element == "point_distribution"


class TestPlannerWiringForNewModels:
    def setup_method(self):
        self.planner = MapProductPlanner()

    def test_grid_plan_template_and_layers(self):
        intent = resolve_map_request_intent("成都小学按1公里格网统计分布")
        plan = self.planner.plan_from_intent(intent)
        assert plan.recipe_id == "grid_density_aggregate"
        assert plan.template_id == "grid_density_product"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.layer_type == "fill"
        assert primary.source_capability == "grid_binning"
        caps = {r.capability for r in plan.data_requirements}
        assert "grid_binning" in caps

    def test_bubble_plan_template_and_layers(self):
        intent = resolve_map_request_intent("用气泡图展示成都小学数量")
        plan = self.planner.plan_from_intent(intent)
        assert plan.recipe_id == "proportional_symbol_map"
        assert plan.template_id == "proportional_symbol_product"
        primary = next(ly for ly in plan.map_layers if ly.role == "primary")
        assert primary.layer_type == "circle"
        assert primary.cartography == "proportional_symbol"

    def test_capability_resolution_prefers_h3(self):
        available = {"h3_binning", "buffer_analysis", "spatial_stats"}
        assert resolve_tool_for_capability("grid_binning", available) == "h3_binning"
        # h3 缺席时渔网兜底；全缺时诚实返回 None
        assert resolve_tool_for_capability(
            "grid_binning", {"fishnet_grid"}) == "fishnet_grid"
        assert resolve_tool_for_capability("grid_binning", {"buffer_analysis"}) is None

    def test_finalize_disables_grid_layer_below_threshold(self):
        intent = resolve_map_request_intent("成都小学按1公里格网统计分布")
        plan = self.planner.plan_from_intent(intent)
        finalized = self.planner.finalize_with_profile(plan, _profile(7))
        assert finalized.status == "finalized"
        grid_layer = next(
            ly for ly in finalized.map_layers if ly.cartography == "aggregate_grid")
        assert grid_layer.enabled is False
        reasons = {f.reason_code for f in finalized.fallbacks}
        assert "INSUFFICIENT_POINTS" in reasons


class TestComponentsForNewModels:
    @pytest.mark.parametrize("carto,legend_kind", [
        ("aggregate_grid", "legend"),
        ("proportional_symbol", "legend"),
    ])
    def test_graduated_models_get_discrete_legend(self, carto, legend_kind):
        comps = build_default_components(primary_cartography=carto)
        types = [c.type for c in comps]
        assert legend_kind in types
        assert "continuous_colorbar" not in types
