"""
Scenario Comparison Engine for Spatial Decision Intelligence V2.
Evaluates and compares multiple spatial scenarios (Baseline vs Scenario A vs Scenario B vs Scenario C).
Computes metric comparison matrix, affected area comparison, trade-offs, Pareto non-dominated frontiers, and evidence explanations.
"""
import uuid
import logging
from typing import Dict, List, Optional, Tuple

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)
from app.services.session_data_protocol import get_session_store

logger = logging.getLogger(__name__)


class ScenarioComparisonEngine:
    """Multi-Scenario Comparison Engine."""

    def _infer_optimization_goals(self, metric_matrix: Dict[str, Dict[str, float]]) -> Dict[str, str]:
        """Infer default optimization direction ('maximize' vs 'minimize') for metric keys."""
        default_goals = {
            "housing_price": "maximize",
            "rent": "maximize",
            "commercial_vitality": "maximize",
            "education_access": "maximize",
            "medical_access": "maximize",
            "living_quality": "maximize",
            "air_quality": "maximize",
            "public_transit_usage": "maximize",
            "commute_time": "minimize",
            "traffic_load": "minimize",
            "road_saturation": "minimize",
        }
        goals = {}
        for m_key in metric_matrix:
            goals[m_key] = default_goals.get(m_key, "maximize")
        return goals

    async def compare_scenarios(
        self,
        results: List[SpatialDecisionResult],
        session_id: str = "",
        optimization_goals: Optional[Dict[str, str]] = None,
    ) -> ScenarioComparisonResult:
        """
        Compare multiple SpatialDecisionResult objects and return a unified ScenarioComparisonResult.
        
        Args:
            results: List of SpatialDecisionResult instances to compare.
            session_id: Session ID for storing comparison GeoJSON.
            optimization_goals: Dict mapping metric_key to 'maximize' or 'minimize'.
        """
        if not results:
            raise ValueError("At least one SpatialDecisionResult is required for scenario comparison.")

        comparison_id = f"cmp_{uuid.uuid4().hex[:12]}"
        
        # 1. Build Metric Matrix
        # Format: metric_key -> {scenario_id: simulated_value}
        metric_matrix: Dict[str, Dict[str, float]] = {}
        affected_area_comparison: Dict[str, float] = {}

        for res in results:
            scen_id = res.scenario.scenario_id
            
            # Sum total affected area (km2)
            total_area = sum(zone.area_km2 for zone in res.spatial_impacts)
            affected_area_comparison[scen_id] = round(total_area, 2)

            for m_key, m_delta in res.metrics.items():
                if m_key not in metric_matrix:
                    metric_matrix[m_key] = {}
                metric_matrix[m_key][scen_id] = m_delta.simulated

        # Default optimization goals if unspecified
        goals = optimization_goals or self._infer_optimization_goals(metric_matrix)

        # 2. Pareto Optimal Analysis
        pareto_scenarios = self._find_pareto_optimal(results, goals)
        trade_offs = self._analyze_trade_offs(results, metric_matrix, goals)

        # 3. Trade-off Analysis & Recommendation
        recommended_id, rationale = self._select_recommended_scenario(
            results, pareto_scenarios, trade_offs, goals
        )

        # 4. Generate Merged Comparison GeoJSON
        merged_features = []
        scenario_colors = [
            "#3B82F6",  # Blue
            "#10B981",  # Green
            "#F59E0B",  # Amber
            "#EF4444",  # Red
            "#8B5CF6",  # Purple
        ]
        
        for idx, res in enumerate(results):
            scen_id = res.scenario.scenario_id
            color = scenario_colors[idx % len(scenario_colors)]
            
            for feat in res.simulation_geojson.get("features", []):
                feat_copy = dict(feat)
                props = dict(feat_copy.get("properties", {}))
                props["scenario_id"] = scen_id
                props["scenario_name"] = res.scenario.name
                props["scenario_color"] = color
                props["is_recommended"] = (scen_id == recommended_id)
                feat_copy["properties"] = props
                merged_features.append(feat_copy)

        comparison_geojson = {
            "type": "FeatureCollection",
            "features": merged_features,
        }

        # 5. Store Comparison Layer in SessionStore
        comparison_ref_id = f"ref:cmp-{comparison_id}"
        if session_id:
            try:
                store = get_session_store()
                stored_ref = await store.store(session_id, comparison_geojson, prefix="cmp")
                if stored_ref:
                    comparison_ref_id = stored_ref
            except Exception as e:
                logger.warning(f"Failed to store comparison GeoJSON to SessionStore: {e}")

        return ScenarioComparisonResult(
            type="scenario_comparison_result",
            comparison_id=comparison_id,
            scenarios=results,
            metric_matrix=metric_matrix,
            affected_area_comparison=affected_area_comparison,
            trade_offs=trade_offs,
            pareto_optimal_scenarios=pareto_scenarios,
            recommended_scenario_id=recommended_id,
            recommendation_rationale=rationale,
            comparison_geojson=comparison_geojson,
            comparison_ref_id=comparison_ref_id,
        )

    def _find_pareto_optimal(
        self, results: List[SpatialDecisionResult], goals: Dict[str, str]
    ) -> List[str]:
        """Find Pareto non-dominated scenario IDs."""
        if len(results) <= 1:
            return [r.scenario.scenario_id for r in results]

        scenarios = [r.scenario.scenario_id for r in results]
        dominated = set()

        for a in results:
            a_id = a.scenario.scenario_id
            for b in results:
                b_id = b.scenario.scenario_id
                if a_id == b_id:
                    continue
                
                # Check if b dominates a
                b_strictly_better = False
                b_worse_in_any = False
                
                for m_key, goal in goals.items():
                    if m_key in a.metrics and m_key in b.metrics:
                        val_a = a.metrics[m_key].simulated
                        val_b = b.metrics[m_key].simulated
                        # GIS-03: metrics without a real baseline are unsimulated
                        # (simulated=None). They carry no comparative signal and
                        # must not dominate / be dominated by None comparisons.
                        if val_a is None or val_b is None:
                            continue

                        if goal == "maximize":
                            if val_b > val_a:
                                b_strictly_better = True
                            elif val_b < val_a:
                                b_worse_in_any = True
                        elif goal == "minimize":
                            if val_b < val_a:
                                b_strictly_better = True
                            elif val_b > val_a:
                                b_worse_in_any = True

                if b_strictly_better and not b_worse_in_any:
                    dominated.add(a_id)

        pareto = [s_id for s_id in scenarios if s_id not in dominated]
        return pareto if pareto else scenarios

    def _analyze_trade_offs(
        self,
        results: List[SpatialDecisionResult],
        metric_matrix: Dict[str, Dict[str, float]],
        goals: Dict[str, str],
    ) -> List[str]:
        """Analyze trade-offs between scenarios."""
        trade_offs = []
        if len(results) < 2:
            return ["单方案评估，无跨方案权衡。"]

        for m_key, scenario_values in metric_matrix.items():
            if len(scenario_values) > 1:
                # GIS-03: drop unsimulated (None) values before comparing.
                finite = {sid: v for sid, v in scenario_values.items() if v is not None}
                if len(finite) < 2:
                    continue
                vals = list(finite.values())
                max_scen = max(finite, key=finite.get)
                min_scen = min(finite, key=finite.get)
                diff_pct = round(((max(vals) - min(vals)) / max(abs(min(vals)), 1.0)) * 100, 1)
                
                if diff_pct > 5.0:
                    goal = goals.get(m_key, "maximize")
                    best_scen = max_scen if goal == "maximize" else min_scen
                    worst_scen = min_scen if goal == "maximize" else max_scen
                    trade_offs.append(
                        f"指标 [{m_key}]: 方案 [{best_scen}] 表现最佳 ({scenario_values[best_scen]}), "
                        f"相比方案 [{worst_scen}] ({scenario_values[worst_scen]}) 优势明显 (差异 {diff_pct}%)。"
                    )

        return trade_offs

    def _select_recommended_scenario(
        self,
        results: List[SpatialDecisionResult],
        pareto_scenarios: List[str],
        trade_offs: List[str],
        goals: Dict[str, str],
    ) -> Tuple[str, str]:
        """Select best scenario and build recommendation rationale."""
        if not results:
            return "", "无有效方案"

        # Calculate composite score for each scenario
        scores: Dict[str, float] = {}
        for res in results:
            scen_id = res.scenario.scenario_id
            score = 0.0
            for m_key, m_val in res.metrics.items():
                goal = goals.get(m_key, "maximize")
                delta_pct = m_val.delta_pct
                # GIS-03: unsimulated metrics (delta_pct is None) contribute 0
                # to the composite score — they carry no signal.
                if delta_pct is None:
                    continue
                if goal == "maximize":
                    score += delta_pct
                else:
                    score -= delta_pct
            
            # Boost score if in pareto optimal set
            if scen_id in pareto_scenarios:
                score += 10.0
            
            # Boost score by confidence
            score *= (0.5 + 0.5 * res.confidence)
            scores[scen_id] = round(score, 2)

        best_id = max(scores, key=scores.get)
        best_res = next(r for r in results if r.scenario.scenario_id == best_id)
        
        rationale_lines = [
            f"推荐采用方案 [{best_res.scenario.name}] (ID: {best_id})。",
            f"综合得分: {scores[best_id]}，基于置信度 {round(best_res.confidence * 100, 1)}% 与 Pareto 优越性评估。",
            "核心优势:",
        ]
        
        for m_key, m_val in best_res.metrics.items():
            # GIS-03: skip unsimulated metrics (delta_pct is None) — they have
            # no real change to report and would crash abs(None).
            if m_val.delta_pct is None:
                continue
            if abs(m_val.delta_pct) > 2.0:
                direction = "提升" if m_val.delta_pct > 0 else "降低"
                rationale_lines.append(f"  - {m_val.metric_name}: {direction} {abs(m_val.delta_pct)}% (基线: {m_val.baseline} -> 模拟: {m_val.simulated})")

        if best_res.recommendations:
            rationale_lines.append("具体落地建议:")
            for rec in best_res.recommendations[:3]:
                rationale_lines.append(f"  * {rec}")

        return best_id, "\n".join(rationale_lines)
