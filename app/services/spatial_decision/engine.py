"""
DecisionEngine - Core Orchestration Engine for Spatial Decision Intelligence V2.
Coordinates scenario resolution, target area geocoding/boundary lookup, baseline resolution,
domain rule pack matching, RAG evidence grounding, spatial impact polygon generation,
metric evaluation with uncertainty ranges, and SessionStore persistence.
"""
import uuid
import logging
from typing import Dict, List, Any, Optional

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioSpec,
    TargetAreaSpec,
    EvidenceItem,
    MetricDeltaV2,
    DomainRule,
    SpatialImpactZone,
)
from app.services.spatial_decision.target_resolver import TargetAreaResolver
from app.services.spatial_decision.baseline_resolver import BaselineResolver
from app.services.spatial_decision.rule_pack import get_rule_pack_registry
from app.services.spatial_decision.impact_engine import SpatialImpactEngine
from app.services.spatial_decision.metric_evaluator import MetricEvaluator
from app.services.session_data_protocol import get_session_store

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Deep Spatial Decision Intelligence Engine V2."""

    def __init__(
        self,
        target_resolver: Optional[TargetAreaResolver] = None,
        baseline_resolver: Optional[BaselineResolver] = None,
        rule_registry=None,
        impact_engine: Optional[SpatialImpactEngine] = None,
        metric_evaluator: Optional[MetricEvaluator] = None,
    ):
        self.target_resolver = target_resolver or TargetAreaResolver()
        self.baseline_resolver = baseline_resolver or BaselineResolver()
        self.rule_registry = rule_registry or get_rule_pack_registry()
        self.impact_engine = impact_engine or SpatialImpactEngine()
        self.metric_evaluator = metric_evaluator or MetricEvaluator()

    async def evaluate_decision(
        self,
        scenario_text: str,
        target_area_text: str,
        parameters: Optional[Dict[str, Any]] = None,
        baseline_data_ref: str = "",
        session_id: str = "",
        owner_token: Optional[str] = None,
    ) -> SpatialDecisionResult:
        """
        Evaluate spatial scenario decision end-to-end.
        
        Args:
            scenario_text: User's scenario description.
            target_area_text: Target area name, geocode string, or session ref.
            parameters: Additional parameters (e.g. growth_pct, capacity, radii).
            baseline_data_ref: Reference ID to baseline spatial dataset in SessionStore.
            session_id: Active session UUID.
            owner_token: Security owner token.
            
        Returns:
            SpatialDecisionResult value object.
        """
        parameters = parameters or {}
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        
        # 1. Resolve Target Area (No hardcoded Beijing!)
        target_spec = await self.target_resolver.resolve(
            target_area_text, session_id=session_id, owner_token=owner_token
        )

        # 2. Match Scenario Type and Domain Rules
        scenario_type = self._detect_scenario_type(scenario_text)
        rules = self.rule_registry.get_rules_by_scenario(scenario_type)
        if not rules:
            rules = self.rule_registry.search_rules(scenario_text)

        applicable_rules = self.rule_registry.match_applicable_rules(parameters)
        existing_ids = {r.id for r in rules}
        for ar in applicable_rules:
            if ar.id not in existing_ids:
                rules.append(ar)
                existing_ids.add(ar.id)

        from app.services.spatial_decision.rule_pack import build_evidence_chain
        evidence_chain = await build_evidence_chain(
            query=scenario_text,
            scenario_type=scenario_type,
            context_params=parameters,
        )

        scenario_name = self._get_scenario_name(scenario_type, rules)
        scenario_spec = ScenarioSpec(
            scenario_id=f"scen_{uuid.uuid4().hex[:8]}",
            scenario_type=scenario_type,
            name=scenario_name,
            description=scenario_text,
            target_area=target_spec,
            parameters=parameters,
            baseline_data_ref=baseline_data_ref,
        )

        # 3. Resolve Baseline Metrics & Evidence
        metrics_needed = [r.domain for r in rules] if rules else []
        for r in rules:
            if r.parameters:
                metrics_needed.extend([k for k in r.parameters.keys() if isinstance(k, str) and not k.startswith("pct_")])

        baseline_metrics = await self.baseline_resolver.resolve_baseline(
            baseline_data_ref=baseline_data_ref,
            target_area=target_spec,
            session_id=session_id,
            metrics_needed=metrics_needed,
        )
        baseline_refs_used = [baseline_data_ref] if baseline_data_ref else []

        # 4. Compute Spatial Impacts (Buffers, UTM Polygon Areas, GeoJSON)
        spatial_impacts, simulation_geojson = self.impact_engine.calculate_impacts(
            scenario_type=scenario_type,
            target_area=target_spec,
            rules=rules,
            parameters=parameters,
        )

        # 5. Evaluate Metrics & Uncertainty Ranges
        evaluated_metrics, assumptions, confidence, uncertainty_desc = self.metric_evaluator.evaluate(
            scenario_type=scenario_type,
            baseline_metrics=baseline_metrics,
            rules=rules,
            parameters=parameters,
            target_area=target_spec,
            evidence_chain=evidence_chain,
        )

        # 6. Formulate Recommendations
        recommendations = self._formulate_recommendations(
            scenario_type=scenario_type,
            target_area=target_spec,
            metrics=evaluated_metrics,
            rules=rules,
            confidence=confidence,
        )

        # 7. Register Simulation GeoJSON to SessionStore (Fetch-on-Demand cursor)
        simulation_ref_id = f"ref:sim-{decision_id}"
        if session_id:
            try:
                store = get_session_store()
                stored_ref = await store.store(session_id, simulation_geojson, prefix="sim")
                if stored_ref:
                    simulation_ref_id = stored_ref
            except Exception as e:
                logger.warning(f"Failed to register simulation GeoJSON to SessionStore: {e}")

        # 8. Provenance Lineage
        provenance = {
            "engine": "DecisionEngineV2",
            "decision_id": decision_id,
            "target_resolution_source": target_spec.source,
            "baseline_ref_count": len(baseline_refs_used),
            "rules_count": len(rules),
            "evidence_count": len(evidence_chain),
            "simulation_ref_id": simulation_ref_id,
        }

        return SpatialDecisionResult(
            type="spatial_decision_result",
            decision_id=decision_id,
            scenario=scenario_spec,
            target_area=target_spec,
            baseline_evidence_refs=baseline_refs_used,
            assumptions=assumptions,
            evidence_chain=evidence_chain,
            rules_applied=rules,
            metrics=evaluated_metrics,
            spatial_impacts=spatial_impacts,
            simulation_geojson=simulation_geojson,
            simulation_ref_id=simulation_ref_id,
            confidence=confidence,
            uncertainty_description=uncertainty_desc,
            recommendations=recommendations,
            provenance=provenance,
        )

    def _detect_scenario_type(self, scenario: str) -> str:
        """Detect scenario type keyword."""
        kw_map = {
            "subway": ["地铁", "地铁站", "轨道交通", "轻轨"],
            "school": ["学校", "小学", "中学", "学区", "教育"],
            "hospital": ["医院", "医疗", "诊所", "卫生院", "康复中心"],
            "park": ["公园", "绿地", "绿化", "生态"],
            "population_growth": ["人口增长", "人口增加", "人口", "流入", "居住人口"],
            "traffic_restriction": ["限行", "限号", "交通管制", "拥堵费", "禁行"],
        }
        for stype, keywords in kw_map.items():
            for kw in keywords:
                if kw in scenario:
                    return stype
        return "custom"

    def _get_scenario_name(self, scenario_type: str, rules: List[DomainRule]) -> str:
        names = {
            "subway": "轨道交通/地铁站接入情景",
            "school": "教育配套设施建设计划",
            "hospital": "医疗卫生服务设施扩建",
            "park": "公园绿地生态覆盖优化",
            "population_growth": "区域人口增长与承载力评估",
            "traffic_restriction": "交通限行/管控影响分析",
            "custom": "自定义空间决策模拟",
        }
        if rules:
            return rules[0].name
        return names.get(scenario_type, "空间决策情景模拟")

    def _formulate_recommendations(
        self,
        scenario_type: str,
        target_area: TargetAreaSpec,
        metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
        confidence: float,
    ) -> List[str]:
        recs = []
        area_name = target_area.resolved_name or target_area.query
        
        if confidence < 0.6:
            recs.append(f"当前由于基线数据缺失或推导条件有限，总体置信度为 {round(confidence*100, 1)}%，建议补充 [{area_name}] 详细规划数据后重新评估。")

        if scenario_type == "subway":
            recs.append(f"建议以 {area_name} 地铁站为中心 500m 核心区优先布局高密度商业与租赁住宅（TOD 模式）。")
            recs.append("建议在 500m-1500m 辐射圈内优化慢行交通与共享单车接驳。")
        elif scenario_type == "school":
            recs.append(f"建议关注 {area_name} 学校 500m 服务圈内的安防设施与上学高峰交通接驳。")
            recs.append("规范周边商业业态，禁止 200m 范围内开设娱乐场所。")
        elif scenario_type == "hospital":
            recs.append(f"建议建立 {area_name} 医院 1.5km 范围内的急救绿色通道与无障碍通行系统。")
        elif scenario_type == "park":
            recs.append(f"建议结合 {area_name} 公园 300m 步道圈提升周边居住小区环境品质。")
        elif scenario_type == "traffic_restriction":
            recs.append(f"建议同步增加 {area_name} 管制区域的地面公交发车频率 15-25%。")
        elif scenario_type == "population_growth":
            recs.append(f"建议提前规划 {area_name} 增加人口所需的教育、医疗与公共交通供给容量。")
        else:
            recs.append(f"基于综合评价，建议在 {area_name} 实施精细化管控并动态监测指标变化。")

        return recs
