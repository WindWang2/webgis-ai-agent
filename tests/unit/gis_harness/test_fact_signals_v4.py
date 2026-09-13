"""V4 fact_signals 通用化 + 组件权威收编单测（ADR-0151 / AC-02 P5+P6）。"""
from __future__ import annotations

from app.services.gis_harness.components import build_default_components
from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.planner import fact_signals
from app.services.gis_harness.recipes import EligibilityContext


def _ctx(**kw) -> EligibilityContext:
    return EligibilityContext(**kw)


class TestFactSignals:
    def test_absent_facts_empty_projection(self) -> None:
        out = fact_signals(EligibilityContext.from_profile(None))
        assert out["evidence"] == {} and out["conflicts"] == []

    def test_raster_expectation_vs_vector_fact(self) -> None:
        ctx = _ctx(n=100, geometry="polygon")
        intent = MapRequestIntent(
            query="栅格分布", geometry_expectation="raster")
        out = fact_signals(ctx, intent=intent)
        codes = [c["code"] for c in out["conflicts"]]
        assert "FACT_GEOMETRY_MISMATCH" in codes
        assert out["evidence"]["geometry"] == "polygon"

    def test_geographic_crs_conflicts_for_aggregation(self) -> None:
        ctx = _ctx(n=100, geometry="point",
                   spatial={"crs": "EPSG:4326", "crs_class": "geographic"})
        intent = MapRequestIntent(
            query="各区数量", task="administrative_statistic")
        out = fact_signals(ctx, intent=intent)
        codes = [c["code"] for c in out["conflicts"]]
        assert "FACT_PROJECTION_REQUIRED" in codes

    def test_zero_inflated_conflict_for_density(self) -> None:
        ctx = _ctx(n=100, geometry="point",
                   distribution={"zero_ratio": 0.8})
        intent = MapRequestIntent(
            query="密度", task="analytical_density",
            cartography_intents=["aggregate_grid"])
        out = fact_signals(ctx, intent=intent)
        codes = [c["code"] for c in out["conflicts"]]
        assert "FACT_ZERO_INFLATED_DISTRIBUTION" in codes

    def test_no_conflict_without_intent(self) -> None:
        ctx = _ctx(n=100, geometry="point",
                   spatial={"crs_class": "geographic"})
        out = fact_signals(ctx, intent=None)
        assert out["conflicts"] == []
        assert out["evidence"]["crsClass"] == "geographic"

    def test_conflicts_bounded(self) -> None:
        ctx = _ctx(n=3, geometry="polygon",
                   spatial={"crs_class": "geographic"},
                   distribution={"zero_ratio": 0.9})
        intent = MapRequestIntent(
            query="密度", task="analytical_density",
            geometry_expectation="raster",
            cartography_intents=["aggregate_grid"])
        out = fact_signals(ctx, intent=intent)
        assert len(out["conflicts"]) <= 4

    def test_finalize_attaches_warnings(self) -> None:
        """finalize 把 fact 冲突并入 methodology_warnings（披露面）。"""
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        intent = MapRequestIntent(
            query="看分布", task="distribution_overview")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = {
            "geometryTypes": ["Point"], "featureCount": 100,
            "crs": "EPSG:4326", "crsClass": "geographic",
        }
        fin = planner.finalize_with_profile(plan, profile)
        assert fin.data_fact_signals.get("crsClass") == "geographic"
        codes = {w.get("code") for w in fin.methodology_warnings}
        # distribution_overview 非聚合任务族 —— 投影冲突不误报
        assert "FACT_PROJECTION_REQUIRED" not in codes


class TestComponentAuthorityConsolidated:
    """P6：build_default_components 的唯一权威是模型库（兼容分支已删）。"""

    def test_graduated_alias_resolves_to_legend(self) -> None:
        comps = build_default_components(primary_cartography="graduated")
        types = [c.type for c in comps]
        assert "legend" in types
        assert "continuous_colorbar" not in types

    def test_legacy_heatmap_vocab_resolves_via_model(self) -> None:
        for carto, expected in (
            ("density_overview", "continuous_colorbar"),
            ("raster_surface", "continuous_colorbar"),
            ("categorical_thematic", "categorical_legend"),
            ("administrative_aggregation", "legend"),
        ):
            comps = build_default_components(primary_cartography=carto)
            assert expected in [c.type for c in comps], carto

    def test_unknown_vocab_honest_default_no_legend(self) -> None:
        """未收录词汇：诚实缺省（title/north/scale/attribution），不猜图例。"""
        comps = build_default_components(primary_cartography="no_such_model")
        types = [c.type for c in comps]
        assert "title" in types and "north_arrow" in types
        assert not set(types) & {"legend", "continuous_colorbar", "categorical_legend"}
