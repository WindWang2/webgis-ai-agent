"""
Unit and Integration Tests for DecisionEngine and ScenarioComparisonEngine.
Verifies Data-Grounded Spatial Decision Intelligence V2 end-to-end.
"""
import pytest
import json
from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
    TargetAreaSpec,
)
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine


@pytest.mark.asyncio
async def test_decision_engine_subway_scenario():
    """Test DecisionEngine on subway station scenario with geocoded target area."""
    engine = DecisionEngine()
    
    result = await engine.evaluate_decision(
        scenario_text="新建地铁站，评估周边房价与交通影响",
        target_area_text="杭州市西湖区",
        parameters={"growth_pct": 10},
    )

    assert isinstance(result, SpatialDecisionResult)
    assert result.type == "spatial_decision_result"
    assert result.scenario.scenario_type == "subway"
    assert result.target_area.resolved_name != ""
    assert result.target_area.confidence > 0.0
    # NO hardcoded Beijing fallback [116.4, 39.9]
    assert result.target_area.center != (116.4, 39.9) or "杭州" not in result.target_area.query
    
    # Check metrics and uncertainty ranges
    assert "housing_price" in result.metrics
    hp_metric = result.metrics["housing_price"]
    assert hp_metric.simulated != 100.0 or hp_metric.baseline != 100.0  # Not hardcoded 100.0 default
    assert hp_metric.range is not None
    assert hp_metric.range.min_val <= hp_metric.simulated <= hp_metric.range.max_val

    # Check spatial impact zones (direct & indirect)
    assert len(result.spatial_impacts) >= 1
    assert result.simulation_geojson["type"] == "FeatureCollection"
    assert len(result.simulation_geojson["features"]) >= 1

    # Check evidence chain & recommendations
    assert len(result.evidence_chain) >= 1
    assert len(result.recommendations) >= 1
    assert result.confidence >= 0.0


@pytest.mark.asyncio
async def test_decision_engine_all_six_scenarios():
    """Verify that all 6 required scenario types run real data-grounded evaluation."""
    engine = DecisionEngine()
    
    scenarios_to_test = [
        ("subway", "新建地铁站影响评估", "北京市海淀区中关村"),
        ("school", "新建实验小学及学区规划", "上海市徐汇区"),
        ("hospital", "新建三甲医院建设分析", "广州市天河区"),
        ("park", "新建中央公园绿地项目", "深圳市南山区"),
        ("population_growth", "区域人口增长20%承载力预测", "成都高新区", {"growth_pct": 20}),
        ("traffic_restriction", "主要干道实施高峰限行政策", "武汉市武昌区"),
    ]

    for expected_type, text, area, *extra_params in scenarios_to_test:
        params = extra_params[0] if extra_params else {}
        res = await engine.evaluate_decision(
            scenario_text=text,
            target_area_text=area,
            parameters=params,
        )
        assert res.scenario.scenario_type == expected_type
        assert res.confidence > 0.0
        assert len(res.metrics) > 0
        assert len(res.rules_applied) > 0


@pytest.mark.asyncio
async def test_scenario_comparison_engine():
    """Test multi-scenario comparison (Baseline vs Scenario A vs Scenario B vs Scenario C)."""
    engine = DecisionEngine()
    cmp_engine = ScenarioComparisonEngine()

    res_a = await engine.evaluate_decision(
        scenario_text="方案A：新建地铁站",
        target_area_text="杭州市余杭区",
    )
    res_b = await engine.evaluate_decision(
        scenario_text="方案B：新建中央公园",
        target_area_text="杭州市余杭区",
    )
    res_c = await engine.evaluate_decision(
        scenario_text="方案C：新建实验小学",
        target_area_text="杭州市余杭区",
    )

    cmp_result = cmp_engine.compare_scenarios(
        results=[res_a, res_b, res_c]
    )

    assert isinstance(cmp_result, ScenarioComparisonResult)
    assert len(cmp_result.scenarios) == 3
    assert len(cmp_result.metric_matrix) > 0
    assert len(cmp_result.affected_area_comparison) == 3
    assert cmp_result.recommended_scenario_id in [res_a.scenario.scenario_id, res_b.scenario.scenario_id, res_c.scenario.scenario_id]
    assert cmp_result.recommendation_rationale != ""
    assert cmp_result.comparison_geojson["type"] == "FeatureCollection"
    assert len(cmp_result.comparison_geojson["features"]) > 0


@pytest.mark.asyncio
async def test_unresolvable_target_area_returns_correction_hint():
    """Verify that unresolvable target area returns structured correction hint instead of Beijing fallback."""
    engine = DecisionEngine()
    
    result = await engine.evaluate_decision(
        scenario_text="新建地铁站",
        target_area_text="未知不存在的虚幻地区xyz123456",
    )

    assert result.target_area.confidence == 0.0
    assert result.target_area.correction_hint is not None
    assert "无法解析" in result.target_area.correction_hint or "建议" in result.target_area.correction_hint
