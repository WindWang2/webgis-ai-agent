"""Tests for what-if simulation tool."""
import pytest

from app.tools.what_if_rules import WHAT_IF_RULES, get_rule, list_scenarios
from app.tools.what_if_simulate import (
    WhatIfArgs,
    MetricDelta,
    WhatIfSimulationResult,
    _detect_scenario_type,
    _sample_midpoint,
    _calculate_impact,
    _generate_circle_polygon,
    _generate_simulation_geojson,
    what_if_simulate,
    register_what_if_simulate,
)
from app.tools.registry import ToolRegistry


def test_what_if_rules_structure():
    """Verify rules have required fields."""
    expected_keys = {"subway", "school", "hospital", "population_growth", "traffic_restriction", "park"}
    assert set(WHAT_IF_RULES.keys()) == expected_keys

    for key, rule in WHAT_IF_RULES.items():
        assert "name" in rule
        assert "direct_radius_m" in rule
        assert "indirect_radius_m" in rule
        has_impact = "impact" in rule
        has_impact_per_10pct = "impact_per_10pct" in rule
        assert has_impact or has_impact_per_10pct


def test_what_if_args_validation():
    """Verify args model validates output_format."""
    valid = WhatIfArgs(scenario="test", target_area="test", output_format="layer")
    assert valid.output_format == "layer"

    valid = WhatIfArgs(scenario="test", target_area="test", output_format="comparison")
    assert valid.output_format == "comparison"

    valid = WhatIfArgs(scenario="test", target_area="test", output_format="report")
    assert valid.output_format == "report"

    with pytest.raises(ValueError):
        WhatIfArgs(scenario="test", target_area="test", output_format="invalid")


def test_calculate_impact_subway():
    """Verify subway housing price impact is within bounds."""
    impact = _calculate_impact("subway", {})
    assert "housing_price" in impact
    direct = impact["housing_price"]["direct"]
    assert 0.15 <= direct <= 0.25
    indirect = impact["housing_price"]["indirect"]
    assert 0.05 <= indirect <= 0.10

    # Verify midpoint sampling
    assert direct == _sample_midpoint((0.15, 0.25))
    assert indirect == _sample_midpoint((0.05, 0.10))


def test_calculate_impact_population_growth():
    """Verify population growth scales with percentage."""
    impact_10 = _calculate_impact("population_growth", {"growth_pct": 10})
    impact_20 = _calculate_impact("population_growth", {"growth_pct": 20})
    impact_5 = _calculate_impact("population_growth", {"growth_pct": 5})

    # All expected metrics should be present
    expected_metrics = {"housing_demand", "traffic_load", "school_demand", "hospital_demand", "commercial_demand"}
    assert set(impact_10.keys()) == expected_metrics

    # 20% should be exactly double 10%
    for metric in expected_metrics:
        assert impact_20[metric] == pytest.approx(impact_10[metric] * 2, rel=1e-9)

    # 5% should be exactly half 10%
    for metric in expected_metrics:
        assert impact_5[metric] == pytest.approx(impact_10[metric] * 0.5, rel=1e-9)


def test_generate_simulation_geojson():
    """Verify GeoJSON has direct + indirect zones for spatial scenarios."""
    impact = _calculate_impact("subway", {})
    geojson = _generate_simulation_geojson("subway", [116.4, 39.9], impact)

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2

    zones = [f["properties"]["zone"] for f in geojson["features"]]
    assert "direct" in zones
    assert "indirect" in zones

    # Direct zone should be a simple polygon
    direct_feature = [f for f in geojson["features"] if f["properties"]["zone"] == "direct"][0]
    assert direct_feature["geometry"]["type"] == "Polygon"
    assert len(direct_feature["geometry"]["coordinates"]) == 1
    assert len(direct_feature["geometry"]["coordinates"][0]) == 33  # 32 points + closing point
    assert "housing_price" in direct_feature["properties"]
    assert direct_feature["properties"]["radius_m"] == 500

    # Indirect zone should be a polygon with a hole
    indirect_feature = [f for f in geojson["features"] if f["properties"]["zone"] == "indirect"][0]
    assert indirect_feature["geometry"]["type"] == "Polygon"
    assert len(indirect_feature["geometry"]["coordinates"]) == 2  # outer + inner hole
    assert len(indirect_feature["geometry"]["coordinates"][0]) == 33
    assert len(indirect_feature["geometry"]["coordinates"][1]) == 33
    assert indirect_feature["properties"]["radius_m"] == 1500


def test_generate_simulation_geojson_non_spatial():
    """Verify GeoJSON for non-spatial scenario uses Point."""
    impact = _calculate_impact("traffic_restriction", {})
    geojson = _generate_simulation_geojson("traffic_restriction", [116.4, 39.9], impact)

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["geometry"]["type"] == "Point"
    assert geojson["features"][0]["properties"]["zone"] == "general"


@pytest.mark.asyncio
async def test_what_if_simulate_tool_output():
    """Verify tool returns structured result via registry dispatch."""
    registry = ToolRegistry()
    register_what_if_simulate(registry)

    result = await registry.dispatch(
        "what_if_simulate",
        {
            "scenario": "新建地铁站",
            "target_area": "北京市朝阳区",
            "parameters": {},
        },
    )

    assert isinstance(result, dict)
    assert result["type"] == "what_if_simulation"
    assert result["scenario"] == "新建地铁站"
    assert result["target_area"] == "北京市朝阳区"
    assert "simulation_ref_id" in result
    assert len(result["simulation_ref_id"]) > 0
    assert "impact_summary" in result
    assert result["impact_summary"]["scenario_name"] == "新建地铁站"
    assert "metrics" in result
    assert len(result["metrics"]) > 0

    # Verify MetricDelta structure
    for metric_name, metric_data in result["metrics"].items():
        assert "baseline" in metric_data
        assert "simulated" in metric_data
        assert "delta_pct" in metric_data
        assert metric_data["baseline"] == 100.0

    assert "uncertainty" in result
    assert "rules_applied" in result
    assert len(result["rules_applied"]) > 0
    assert "subway" in result["rules_applied"][0]

    assert "simulation_geojson" in result
    assert result["simulation_geojson"]["type"] == "FeatureCollection"
    assert len(result["simulation_geojson"]["features"]) == 2


def test_detect_scenario_type():
    """Verify Chinese keyword detection maps correctly."""
    assert _detect_scenario_type("新建地铁站") == "subway"
    assert _detect_scenario_type("地铁线路规划") == "subway"
    assert _detect_scenario_type("轨道交通建设") == "subway"
    assert _detect_scenario_type("学校选址") == "school"
    assert _detect_scenario_type("学区划分") == "school"
    assert _detect_scenario_type("医院建设") == "hospital"
    assert _detect_scenario_type("人口增长预测") == "population_growth"
    assert _detect_scenario_type("交通限行政策") == "traffic_restriction"
    assert _detect_scenario_type("公园规划") == "park"
    # Default fallback
    assert _detect_scenario_type("未知场景") == "subway"


def test_list_scenarios():
    """Verify list_scenarios returns all scenarios."""
    scenarios = list_scenarios()
    assert len(scenarios) == 6
    types = {s["type"] for s in scenarios}
    assert types == {"subway", "school", "hospital", "population_growth", "traffic_restriction", "park"}
    for s in scenarios:
        assert "name" in s
        assert "direct_radius_m" in s
        assert "indirect_radius_m" in s


def test_what_if_simulate_population_growth():
    """Verify population growth scenario produces correct scaled metrics."""
    result = what_if_simulate(
        scenario="人口增长",
        target_area="测试区域",
        parameters={"growth_pct": 20},
    )
    assert result["type"] == "what_if_simulation"
    assert len(result["metrics"]) == 5
    # All deltas should be doubled compared to 10% baseline
    for metric_data in result["metrics"].values():
        assert metric_data["delta_pct"] > 0


def test_what_if_simulate_result_model_validation():
    """Verify WhatIfSimulationResult model validates correctly."""
    result = WhatIfSimulationResult(
        scenario="测试",
        target_area="测试区域",
        simulation_ref_id="abc123",
        impact_summary={"affected_metrics": ["housing_price"]},
        metrics={
            "housing_price": MetricDelta(baseline=100.0, simulated=120.0, delta_pct=20.0)
        },
        uncertainty="测试不确定性",
        rules_applied=["subway: 新建地铁站"],
        simulation_geojson={"type": "FeatureCollection", "features": []},
    )
    assert result.type == "what_if_simulation"
    dumped = result.model_dump()
    assert dumped["metrics"]["housing_price"]["baseline"] == 100.0
