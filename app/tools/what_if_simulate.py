"""What-if scenario simulation engine and tool registration (V2 Data-Grounded)."""
import asyncio
import logging
import math
import uuid
from typing import Literal, Optional, Dict, Any

import numpy as np
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools.what_if_rules import get_rule
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.models import SpatialDecisionResult

logger = logging.getLogger(__name__)


# --- Pydantic models ---

class WhatIfArgs(BaseModel):
    scenario: str = Field(..., description="场景描述，支持中文关键词")
    target_area: str = Field(..., description="目标区域名称")
    parameters: dict = Field(default_factory=dict, description="额外参数，如 growth_pct")
    baseline_data_ref: str = Field(default="", description="基线数据引用 ID")
    output_format: Literal["layer", "comparison", "report"] = Field(
        default="layer", description="输出格式: layer/comparison/report"
    )


class MetricDelta(BaseModel):
    baseline: float = Field(..., description="基线值")
    simulated: float = Field(..., description="模拟后值")
    delta_pct: float = Field(..., description="变化百分比")


class WhatIfSimulationResult(BaseModel):
    type: str = Field(default="what_if_simulation", description="结果类型")
    scenario: str = Field(..., description="场景描述")
    target_area: str = Field(..., description="目标区域")
    simulation_ref_id: str = Field(..., description="模拟结果引用 ID")
    impact_summary: dict = Field(..., description="影响摘要")
    metrics: dict[str, MetricDelta] = Field(..., description="各指标变化")
    uncertainty: str = Field(..., description="不确定性说明")
    rules_applied: list[str] = Field(..., description="应用的规则列表")
    simulation_geojson: dict = Field(..., description="模拟 GeoJSON")


KEYWORD_MAP = {
    "subway": ["地铁", "地铁站", "轨道交通", "地铁线"],
    "school": ["学校", "小学", "中学", "学区", "教育"],
    "hospital": ["医院", "医疗", "诊所", "卫生院"],
    "population_growth": ["人口增长", "人口增加", "人口", "流入"],
    "traffic_restriction": ["限行", "限号", "交通管制", "拥堵费"],
    "park": ["公园", "绿地", "绿化"],
}


def _detect_scenario_type(scenario: str) -> str | None:
    """Detect scenario type from Chinese keywords."""
    for scenario_type, keywords in KEYWORD_MAP.items():
        for kw in keywords:
            if kw in scenario:
                return scenario_type
    return None


def _sample_midpoint(interval: tuple) -> float:
    """Return midpoint of interval for deterministic simulation."""
    return (interval[0] + interval[1]) / 2.0


def _calculate_impact(scenario_type: str, parameters: dict) -> dict:
    """Calculate impact deltas per metric."""
    rule = get_rule(scenario_type)
    if not rule:
        return {}

    impact = {}

    if scenario_type == "population_growth":
        scale = parameters.get("growth_pct", 10) / 10.0
        for metric, interval in rule.get("impact_per_10pct", {}).items():
            impact[metric] = _sample_midpoint(interval) * scale
    elif scenario_type == "traffic_restriction":
        for metric, interval in rule.get("impact", {}).items():
            impact[metric] = _sample_midpoint(interval)
    else:
        for metric, zones in rule.get("impact", {}).items():
            impact[metric] = {}
            for zone, interval in zones.items():
                impact[metric][zone] = _sample_midpoint(interval)

    return impact


def _generate_circle_polygon(center_lng: float, center_lat: float, radius_m: float, num_points: int = 32) -> list:
    """Generate approximate circle polygon in GeoJSON coordinates using numpy."""
    angles = np.linspace(0, 2 * np.pi, num_points + 1)
    lat_rad = math.radians(center_lat)
    lat_factor = 111000.0
    lng_factor = 111000.0 * math.cos(lat_rad)
    dx = radius_m * np.cos(angles) / lng_factor
    dy = radius_m * np.sin(angles) / lat_factor
    return np.column_stack([center_lng + dx, center_lat + dy]).tolist()


def _impact_level_from_deltas(deltas: dict) -> str:
    """Classify impact level from metric deltas."""
    values = []
    for v in deltas.values():
        if isinstance(v, dict):
            values.extend(v.values())
        else:
            values.append(v)
    if not values:
        return "low"
    max_abs = max(abs(v) for v in values)
    if max_abs >= 0.20:
        return "high"
    if max_abs >= 0.10:
        return "medium"
    return "low"


def _generate_simulation_geojson(scenario_type: str, target_center: tuple, impact: dict) -> dict:
    """Generate FeatureCollection with direct zone and indirect zone (ring with hole)."""
    rule = get_rule(scenario_type)
    direct_radius = rule.get("direct_radius_m")
    indirect_radius = rule.get("indirect_radius_m")

    features = []

    if direct_radius is not None and indirect_radius is not None:
        direct_ring = _generate_circle_polygon(
            target_center[0], target_center[1], direct_radius
        )
        direct_props = {
            "zone": "direct",
            "impact_level": _impact_level_from_deltas(impact),
            "radius_m": direct_radius,
        }
        for metric, value in impact.items():
            direct_props[metric] = (
                value.get("direct", value) if isinstance(value, dict) else value
            )

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [direct_ring],
            },
            "properties": direct_props,
        })

        outer_ring = _generate_circle_polygon(
            target_center[0], target_center[1], indirect_radius
        )
        inner_ring_reversed = list(reversed(direct_ring))
        indirect_props = {
            "zone": "indirect",
            "impact_level": _impact_level_from_deltas({
                k: (v.get("indirect", 0.0) if isinstance(v, dict) else 0.0)
                for k, v in impact.items()
            }),
            "radius_m": indirect_radius,
        }
        for metric, value in impact.items():
            indirect_props[metric] = (
                value.get("indirect", 0.0) if isinstance(value, dict) else 0.0
            )

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [outer_ring, inner_ring_reversed],
            },
            "properties": indirect_props,
        })
    else:
        point_props = {
            "zone": "general",
            "impact_level": _impact_level_from_deltas(impact),
            "radius_m": None,
        }
        for metric, value in impact.items():
            point_props[metric] = value
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": target_center,
            },
            "properties": point_props,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _empty_result(
    scenario: str,
    target_area: str,
    scenario_type: str = "error",
    scenario_name: str = "执行错误",
    uncertainty: str = "无法计算影响",
) -> dict:
    """构建统一的空/错误结果，避免重复构造 WhatIfSimulationResult。"""
    return WhatIfSimulationResult(
        type="what_if_simulation",
        scenario=scenario,
        target_area=target_area,
        simulation_ref_id=uuid.uuid4().hex[:12],
        impact_summary={
            "scenario_type": scenario_type,
            "scenario_name": scenario_name,
            "direct_area_km2": 0.0,
            "indirect_area_km2": 0.0,
            "affected_metrics": [],
        },
        metrics={},
        uncertainty=uncertainty,
        rules_applied=[],
        simulation_geojson={"type": "FeatureCollection", "features": []},
    ).model_dump()


async def what_if_simulate_async(
    scenario: str,
    target_area: str,
    parameters: dict = None,
    baseline_data_ref: str = "",
    output_format: str = "layer",
    session_id: str = "",
) -> dict:
    """Core async What-if scenario simulation powered by DecisionEngine V2."""
    if parameters is None:
        parameters = {}

    scenario_type = _detect_scenario_type(scenario)
    if scenario_type is None:
        return _empty_result(
            scenario,
            target_area,
            scenario_type="unknown",
            scenario_name="未知场景",
            uncertainty="无法计算影响：未识别的场景类型",
        )

    try:
        engine = DecisionEngine()
        dec_result: SpatialDecisionResult = await engine.evaluate_decision(
            scenario_text=scenario,
            target_area_text=target_area,
            parameters=parameters,
            baseline_data_ref=baseline_data_ref,
            session_id=session_id,
        )

        if dec_result.target_area.confidence == 0.0 and dec_result.target_area.correction_hint:
            return _empty_result(
                scenario,
                target_area,
                scenario_type=scenario_type,
                scenario_name="区域解析失败",
                uncertainty=dec_result.target_area.correction_hint,
            )

        # Convert V2 MetricDeltaV2 to legacy MetricDelta dictionary for 100% backward compatibility
        legacy_metrics = {}
        for m_key, m_v2 in dec_result.metrics.items():
            legacy_metrics[m_key] = MetricDelta(
                baseline=round(m_v2.baseline, 2),
                simulated=round(m_v2.simulated, 2),
                delta_pct=round(m_v2.delta_pct, 2),
            )

        # Calculate area metrics from spatial impacts
        direct_area = next((z.area_km2 for z in dec_result.spatial_impacts if z.zone_type == "direct"), 0.0)
        indirect_area = next((z.area_km2 for z in dec_result.spatial_impacts if z.zone_type == "indirect"), 0.0)

        summary = {
            "scenario_type": scenario_type,
            "scenario_name": dec_result.scenario.name,
            "direct_area_km2": round(direct_area, 2),
            "indirect_area_km2": round(indirect_area, 2),
            "affected_metrics": list(legacy_metrics.keys()),
        }

        rules_applied_str = [f"{r.domain}: {r.name}" for r in dec_result.rules_applied]

        result = WhatIfSimulationResult(
            type="what_if_simulation",
            scenario=scenario,
            target_area=dec_result.target_area.resolved_name or target_area,
            simulation_ref_id=dec_result.simulation_ref_id,
            impact_summary=summary,
            metrics=legacy_metrics,
            uncertainty=dec_result.uncertainty_description,
            rules_applied=rules_applied_str,
            simulation_geojson=dec_result.simulation_geojson,
        )

        return result.model_dump()

    except Exception as e:
        logger.error(f"[WhatIfSimulateV2] Failed: {e}", exc_info=True)
        return _empty_result(scenario, target_area, uncertainty=f"错误: {str(e)}")


def what_if_simulate(
    scenario: str,
    target_area: str,
    parameters: dict = None,
    baseline_data_ref: str = "",
    output_format: str = "layer",
) -> dict:
    """Synchronous entry point for legacy callers and tool registry dispatch."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # If already in async loop (e.g. FastAPI / agent execution loop)
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(
                what_if_simulate_async(
                    scenario, target_area, parameters, baseline_data_ref, output_format
                )
            )
    except RuntimeError:
        pass

    return asyncio.run(
        what_if_simulate_async(
            scenario, target_area, parameters, baseline_data_ref, output_format
        )
    )


def register_what_if_simulate(registry: ToolRegistry):
    """Register what_if_simulate tool to ToolRegistry."""

    @tool(
        registry,
        name="what_if_simulate",
        description="What-if 场景模拟 V2：基于真实 GIS 空间数据、工程规则与 RAG 证据链，开展动态情景影响推演与指标变化计算。",
        tier=3,
        domains=["what_if"],
        args_model=WhatIfArgs,
    )
    async def _what_if_simulate_wrapper(
        scenario: str,
        target_area: str,
        parameters: dict = None,
        baseline_data_ref: str = "",
        output_format: str = "layer",
    ) -> dict:
        return await what_if_simulate_async(
            scenario, target_area, parameters, baseline_data_ref, output_format
        )
