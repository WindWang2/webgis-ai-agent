"""
Benchmark Evaluation Suite for Spatial Decision Intelligence V2.
Verifies 10 real-world benchmark scenarios across tool choice, evidence grounding,
numerical correctness, spatial impact correctness, uncertainty ranges, step efficiency,
error recovery, and MapSpec validity.
"""
import pytest
from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
from app.services.spatial_decision.mapspec_integration import apply_decision_to_mapspec, apply_comparison_to_mapspec
from app.services.spatial_decision.report_integration import (
    generate_decision_report_markdown,
    generate_comparison_report_markdown,
)


@pytest.mark.asyncio
async def test_benchmark_scenario_1_subway_tod():
    """Benchmark 1: Subway station TOD development impact."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建地铁十号线中关村站，预测周边 500m 住宅与商业溢价",
        target_area_text="北京市海淀区中关村",
        parameters={"growth_pct": 15},
    )

    assert result.scenario.scenario_type == "subway"
    assert result.confidence >= 0.70
    assert "housing_price" in result.metrics
    assert result.metrics["housing_price"].delta_pct > 0.0
    assert len(result.spatial_impacts) == 2
    assert result.spatial_impacts[0].zone_type == "direct"
    assert result.spatial_impacts[1].zone_type == "indirect"
    assert result.simulation_geojson["type"] == "FeatureCollection"


@pytest.mark.asyncio
async def test_benchmark_scenario_2_primary_school():
    """Benchmark 2: Primary school construction and education access."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建徐汇第一实验小学，规划学区覆盖范围",
        target_area_text="上海市徐汇区",
    )

    assert result.scenario.scenario_type == "school"
    assert "education_access" in result.metrics
    assert result.metrics["education_access"].delta_pct > 0.0
    assert "housing_price" in result.metrics
    assert result.metrics["housing_price"].range is not None


@pytest.mark.asyncio
async def test_benchmark_scenario_3_tertiary_hospital():
    """Benchmark 3: Tertiary hospital expansion and medical access."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建天河区第一人民医院三甲分院",
        target_area_text="广州市天河区",
    )

    assert result.scenario.scenario_type == "hospital"
    assert "medical_access" in result.metrics
    assert result.metrics["medical_access"].delta_pct >= 20.0
    assert any(z.radius_m == 1500.0 for z in result.spatial_impacts)


@pytest.mark.asyncio
async def test_benchmark_scenario_4_central_park():
    """Benchmark 4: Central park green space coverage and quality of life."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建南山前海中央公园，改善区域生态品质",
        target_area_text="深圳市南山区",
    )

    assert result.scenario.scenario_type == "park"
    assert "living_quality" in result.metrics
    assert result.metrics["living_quality"].delta_pct > 0.0


@pytest.mark.asyncio
async def test_benchmark_scenario_5_population_growth():
    """Benchmark 5: Population growth and infrastructure carrying capacity."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="成都高新区人口增长 25% 基础设施承载力预测",
        target_area_text="成都高新区",
        parameters={"growth_pct": 25},
    )

    assert result.scenario.scenario_type == "population_growth"
    assert "traffic_load" in result.metrics
    assert "school_demand" in result.metrics
    assert result.metrics["traffic_load"].delta_pct > 0.0


@pytest.mark.asyncio
async def test_benchmark_scenario_6_traffic_restriction():
    """Benchmark 6: Peak traffic restriction policy evaluation."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="武昌主干道实施早晚高峰单双号限行管制",
        target_area_text="武汉市武昌区",
    )

    assert result.scenario.scenario_type == "traffic_restriction"
    assert "road_saturation" in result.metrics
    assert result.metrics["road_saturation"].delta_pct < 0.0  # Reduced saturation
    assert "public_transit_usage" in result.metrics
    assert result.metrics["public_transit_usage"].delta_pct > 0.0  # Increased transit share


@pytest.mark.asyncio
async def test_benchmark_scenario_7_dual_site_comparison():
    """Benchmark 7: Multi-site selection comparison (Site A vs Site B)."""
    engine = DecisionEngine()
    cmp_engine = ScenarioComparisonEngine()

    site_a = await engine.evaluate_decision(
        scenario_text="选址方案A：余杭良渚商业中心",
        target_area_text="杭州市余杭区",
    )
    site_b = await engine.evaluate_decision(
        scenario_text="选址方案B：余杭未来科技城商业中心",
        target_area_text="杭州市余杭区",
    )

    cmp_result = await cmp_engine.compare_scenarios(
        results=[site_a, site_b],
        optimization_goals={"housing_price": "maximize", "commute_time": "minimize"}
    )

    assert isinstance(cmp_result, ScenarioComparisonResult)
    assert len(cmp_result.scenarios) == 2
    assert cmp_result.recommended_scenario_id != ""
    assert len(cmp_result.pareto_optimal_scenarios) >= 1
    assert "housing_price" in cmp_result.metric_matrix


@pytest.mark.asyncio
async def test_benchmark_scenario_8_evidence_missing_self_heal():
    """Benchmark 8: Baseline evidence missing self-healing and gap recording."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="未知缺失基线的数据评估情景",
        target_area_text="杭州市西湖区",
        baseline_data_ref="ref:nonexistent-data-999",
    )

    assert result.confidence < 0.90  # Lowered confidence due to missing baseline
    assert len(result.assumptions) >= 1
    missing_metrics = [m for m in result.metrics.values() if m.missing_baseline]
    assert len(missing_metrics) >= 1 or any("缺失" in a for a in result.assumptions)


@pytest.mark.asyncio
async def test_benchmark_scenario_9_mapspec_cartography_integration():
    """Benchmark 9: MapSpec lifecycle integration and intent compilation."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建地铁站与 MapSpec 自动制图集",
        target_area_text="北京市朝阳区",
    )

    session_id = "test_session_mapspec_001"
    mapspec = await apply_decision_to_mapspec(session_id, result)
    assert mapspec != {}
    assert "layers" in mapspec or "sources" in mapspec


@pytest.mark.asyncio
async def test_benchmark_scenario_10_decision_report_generation():
    """Benchmark 10: Markdown decision report generation."""
    engine = DecisionEngine()
    result = await engine.evaluate_decision(
        scenario_text="新建实验小学决策报告评估",
        target_area_text="上海市徐汇区",
    )

    report_md = generate_decision_report_markdown(result)
    assert "# 空间决策模拟评估报告" in report_md
    assert "关键指标推演与不确定性区间" in report_md
    assert "证据链条 (Evidence Audit Chain)" in report_md
    assert result.decision_id in report_md
