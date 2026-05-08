"""What-if scenario simulation engine and tool registration."""
import logging
import math
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools.what_if_rules import WHAT_IF_RULES, get_rule

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


# --- Scenario detection ---

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


# --- Impact calculation ---

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
        # Spatial scenarios with direct/indirect zones
        for metric, zones in rule.get("impact", {}).items():
            impact[metric] = {}
            for zone, interval in zones.items():
                impact[metric][zone] = _sample_midpoint(interval)

    return impact


# --- GeoJSON generation ---

def _generate_circle_polygon(center_lng, center_lat, radius_m, num_points=32):
    """Generate approximate circle polygon in GeoJSON coordinates."""
    coords = []
    lat_rad = math.radians(center_lat)
    lat_factor = 111000.0
    lng_factor = 111000.0 * math.cos(lat_rad)
    for i in range(num_points + 1):
        angle = 2 * math.pi * i / num_points
        dx = radius_m * math.cos(angle) / lng_factor
        dy = radius_m * math.sin(angle) / lat_factor
        coords.append([center_lng + dx, center_lat + dy])
    return coords


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


def _generate_simulation_geojson(scenario_type, target_center, impact):
    """Generate FeatureCollection with direct zone and indirect zone (ring with hole)."""
    rule = get_rule(scenario_type)
    direct_radius = rule.get("direct_radius_m")
    indirect_radius = rule.get("indirect_radius_m")

    features = []

    if direct_radius is not None and indirect_radius is not None:
        # Direct zone: circle polygon
        direct_ring = _generate_circle_polygon(
            target_center[0], target_center[1], direct_radius
        )
        direct_props = {
            "zone": "direct",
            "impact_level": _impact_level_from_deltas(impact),
            "radius_m": direct_radius,
        }
        # Add metric deltas for direct zone
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

        # Indirect zone: ring with hole
        outer_ring = _generate_circle_polygon(
            target_center[0], target_center[1], indirect_radius
        )
        inner_ring = _generate_circle_polygon(
            target_center[0], target_center[1], direct_radius
        )
        # Reverse inner ring for hole (clockwise)
        inner_ring_reversed = list(reversed(inner_ring))
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
        # Non-spatial scenario: single point feature
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


# --- Tool registration ---

def _build_metrics(impact: dict) -> dict[str, MetricDelta]:
    """Build MetricDelta dict from impact deltas with placeholder baselines."""
    metrics = {}
    for metric, value in impact.items():
        if isinstance(value, dict):
            delta = value.get("direct", 0.0)
        else:
            delta = value
        baseline = 100.0
        simulated = baseline * (1 + delta)
        delta_pct = round(((simulated - baseline) / baseline) * 100, 2)
        metrics[metric] = MetricDelta(
            baseline=round(baseline, 2),
            simulated=round(simulated, 2),
            delta_pct=delta_pct,
        )
    return metrics


def _build_impact_summary(metrics: dict[str, MetricDelta], rule_name: str) -> dict:
    """Build impact summary from metrics."""
    deltas = [m.delta_pct for m in metrics.values()]
    return {
        "scenario_name": rule_name,
        "affected_metrics": list(metrics.keys()),
        "total_metrics": len(metrics),
        "max_positive_impact": round(
            max((d for d in deltas if d > 0), default=0.0), 2
        ),
        "max_negative_impact": round(
            min((d for d in deltas if d < 0), default=0.0), 2
        ),
    }


def what_if_simulate(
    scenario: str,
    target_area: str,
    parameters: dict = None,
    baseline_data_ref: str = "",
    output_format: str = "layer",
) -> dict:
    """What-if scenario simulation core logic."""
    if parameters is None:
        parameters = {}

    try:
        scenario_type = _detect_scenario_type(scenario)
        if scenario_type is None:
            return WhatIfSimulationResult(
                type="what_if_simulation",
                scenario=scenario,
                target_area=target_area,
                simulation_ref_id=uuid.uuid4().hex[:12],
                impact_summary={"error": f"无法识别场景: {scenario}"},
                metrics={},
                uncertainty="无法计算影响：未识别的场景类型",
                rules_applied=[],
                simulation_geojson={"type": "FeatureCollection", "features": []},
            ).model_dump()

        rule = get_rule(scenario_type)
        if not rule:
            return WhatIfSimulationResult(
                type="what_if_simulation",
                scenario=scenario,
                target_area=target_area,
                simulation_ref_id=uuid.uuid4().hex[:12],
                impact_summary={"error": f"未找到规则: {scenario_type}"},
                metrics={},
                uncertainty="无法计算影响：规则不存在",
                rules_applied=[],
                simulation_geojson={"type": "FeatureCollection", "features": []},
            ).model_dump()

        impact = _calculate_impact(scenario_type, parameters)
        metrics = _build_metrics(impact)

        # Placeholder target center (Beijing)
        target_center = [116.4, 39.9]
        geojson = _generate_simulation_geojson(scenario_type, target_center, impact)

        summary = _build_impact_summary(metrics, rule["name"])

        result = WhatIfSimulationResult(
            type="what_if_simulation",
            scenario=scenario,
            target_area=target_area,
            simulation_ref_id=uuid.uuid4().hex[:12],
            impact_summary=summary,
            metrics=metrics,
            uncertainty="基于规则库的中点估计，实际影响可能因具体地段、市场条件而异",
            rules_applied=[f"{scenario_type}: {rule['name']}"],
            simulation_geojson=geojson,
        )

        return result.model_dump()
    except Exception as e:
        logger.error(f"[WhatIfSimulate] Failed: {e}")
        return WhatIfSimulationResult(
            type="what_if_simulation",
            scenario=scenario,
            target_area=target_area,
            simulation_ref_id=uuid.uuid4().hex[:12],
            impact_summary={"error": f"模拟执行错误: {str(e)}"},
            metrics={},
            uncertainty=f"错误: {str(e)}",
            rules_applied=[],
            simulation_geojson={"type": "FeatureCollection", "features": []},
        ).model_dump()


def register_what_if_simulate(registry: ToolRegistry):
    """Register what_if_simulate tool to ToolRegistry."""

    @tool(
        registry,
        name="what_if_simulate",
        description="What-if 场景模拟：基于规则库对城市规划、交通、人口等场景进行影响模拟，输出指标变化与模拟 GeoJSON。",
        args_model=WhatIfArgs,
    )
    def _what_if_simulate_wrapper(
        scenario: str,
        target_area: str,
        parameters: dict = None,
        baseline_data_ref: str = "",
        output_format: str = "layer",
    ) -> dict:
        return what_if_simulate(
            scenario, target_area, parameters, baseline_data_ref, output_format
        )
