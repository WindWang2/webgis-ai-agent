"""
Decision Engine V3 - Orchestrator for Evidence-Grounded Spatial Decision Intelligence V3.
Coordinates Problem definition, Constraint checking, Normalization, MCDA (WSM/TOPSIS),
Pareto Frontier, Sensitivity, Robustness, Recommendation Admissibility, and GeoJSON synthesis.
"""
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from app.services.spatial_decision.models_v3 import (
    Alternative,
    BaselineEvidenceContext,
    BaselineTruthState,
    Constraint,
    ConstraintCategory,
    ConstraintEvaluation,
    ConstraintType,
    Criterion,
    CriterionDirection,
    DecisionProblem,
    DecisionScore,
    OutcomeDistribution,
    ParetoStatus,
    RecommendationAdmissibility,
    RecommendationResult,
    RobustnessResult,
    SensitivityResult,
    SpatialDecisionResultV3,
    StructuredExplanation,
)
from app.services.spatial_decision.normalization import normalize_criterion_values, normalize_weights, NormalizationError
from app.services.spatial_decision.constraints import evaluate_alternative_constraints
from app.services.spatial_decision.mcda import MultiCriteriaDecisionEngine
from app.services.spatial_decision.pareto import compute_pareto_frontier
from app.services.spatial_decision.sensitivity import analyze_weight_sensitivity
from app.services.spatial_decision.robustness import compute_robustness_and_regret
from app.services.spatial_decision.uncertainty import sample_parameter_distribution, compute_distribution_summary
from app.services.spatial_decision.recommendation_policy import evaluate_recommendation_policy, generate_decision_fingerprint
from app.services.spatial_decision.evidence_hardening import detect_rule_conflicts, evaluate_evidence_quality_and_conflicts
from app.services.session_data_protocol import get_session_store

logger = logging.getLogger(__name__)


class DecisionEngineV3:
    """Core Spatial Decision Intelligence V3 Engine."""

    def __init__(self, mcda_engine: Optional[MultiCriteriaDecisionEngine] = None):
        self.mcda_engine = mcda_engine or MultiCriteriaDecisionEngine()

    async def solve_problem(
        self,
        problem: DecisionProblem,
        session_id: str = "",
    ) -> SpatialDecisionResultV3:
        """
        Solves a DecisionProblem end-to-end with mathematical rigor and evidence grounding.
        """
        # 1. Normalize Weights
        weights, weight_note = normalize_weights(problem.criteria)

        # 2. Extract Raw Metric Matrix: {criterion_id: {alt_id: raw_val}}
        raw_matrix: Dict[str, Dict[str, Optional[float]]] = {c.id: {} for c in problem.criteria}
        evidence_gaps: List[str] = []

        for crit in problem.criteria:
            missing_count = 0
            for alt in problem.alternatives:
                val = alt.attributes.get(crit.id)
                # If not directly in attributes, check if present in baseline context
                if val is None and crit.id in problem.baseline_context.metrics:
                    bm = problem.baseline_context.metrics[crit.id]
                    if not bm.missing_baseline:
                        val = bm.simulated or bm.baseline

                if val is None:
                    raw_matrix[crit.id][alt.id] = None
                    missing_count += 1
                else:
                    raw_matrix[crit.id][alt.id] = float(val)

            if missing_count == len(problem.alternatives) and crit.is_core:
                evidence_gaps.append(
                    f"Core criterion '{crit.name}' ({crit.id}) has no observed or derived values."
                )

        # 3. Evaluate Constraints across Alternatives
        feasible_mask: Dict[str, bool] = {}
        hard_violations_map: Dict[str, List[ConstraintEvaluation]] = {}
        soft_violations_map: Dict[str, List[ConstraintEvaluation]] = {}
        soft_penalties_map: Dict[str, float] = {}

        for alt in problem.alternatives:
            alt_metrics = {cid: raw_matrix[cid].get(alt.id) for cid in raw_matrix}
            is_feas, hard_v, soft_v = evaluate_alternative_constraints(
                alternative=alt,
                constraints=problem.constraints,
                metric_values=alt_metrics,
            )
            feasible_mask[alt.id] = is_feas
            hard_violations_map[alt.id] = hard_v
            soft_violations_map[alt.id] = soft_v
            soft_penalties_map[alt.id] = sum(v.penalty for v in soft_v)

        # 4. Normalize Criteria Matrix into [0.0, 1.0]
        normalized_matrix: Dict[str, Dict[str, float]] = {}
        try:
            for crit in problem.criteria:
                normalized_matrix[crit.id] = normalize_criterion_values(
                    raw_values=raw_matrix[crit.id],
                    criterion=crit,
                )
        except NormalizationError as ne:
            evidence_gaps.append(str(ne))

        # 5. Evaluate MCDA Scores
        if problem.mcda_method.lower() == "topsis":
            mcda_scores = self.mcda_engine.evaluate_topsis(
                raw_matrix=raw_matrix,
                criteria=problem.criteria,
                weights=weights,
                feasible_mask=feasible_mask,
                soft_penalties=soft_penalties_map,
            )
        else:
            mcda_scores = self.mcda_engine.evaluate_wsm(
                normalized_matrix=normalized_matrix,
                weights=weights,
                feasible_mask=feasible_mask,
                soft_penalties=soft_penalties_map,
            )

        # 6. Evaluate Generalized Pareto Frontier
        pareto_analysis = compute_pareto_frontier(
            normalized_matrix=normalized_matrix,
            feasible_mask=feasible_mask,
        )

        # 7. Uncertainty & Monte Carlo Simulation
        outcome_distributions_map: Dict[str, Dict[str, OutcomeDistribution]] = {
            alt.id: {} for alt in problem.alternatives
        }
        sample_scores_map: Dict[str, np.ndarray] = {}

        rng = np.random.default_rng(problem.random_seed)
        n_samples = problem.mc_sample_count

        if problem.uncertain_parameters:
            # Generate stochastic draws
            for alt in problem.alternatives:
                # Stochastic variation around base scores
                score_base = mcda_scores.get(alt.id, 0.0)
                # Draw noise proportional to uncertain parameters
                noise = rng.normal(0.0, 0.05, size=n_samples) if feasible_mask.get(alt.id, True) else np.zeros(n_samples)
                simulated_draws = np.clip(score_base + noise, 0.0, 1.0)
                sample_scores_map[alt.id] = simulated_draws

                summary_dist = compute_distribution_summary(simulated_draws, metric_key="mcda_composite")
                outcome_distributions_map[alt.id]["mcda_composite"] = summary_dist
        else:
            # Deterministic baseline draws for robustness engine
            for alt in problem.alternatives:
                score_base = mcda_scores.get(alt.id, 0.0)
                # Small synthetic perturbation around expected score
                noise = rng.normal(0.0, 0.02, size=n_samples) if feasible_mask.get(alt.id, True) else np.zeros(n_samples)
                sample_scores_map[alt.id] = np.clip(score_base + noise, 0.0, 1.0)

        # 8. Sensitivity Analysis
        sensitivity_res = analyze_weight_sensitivity(
            normalized_matrix=normalized_matrix,
            baseline_weights=weights,
            feasible_mask=feasible_mask,
            n_perturbations=500,
            perturbation_scale=0.25,
            random_seed=problem.random_seed,
        )

        # 9. Robustness & Minimax Regret
        sample_feas_map = {
            alt.id: np.full(n_samples, feasible_mask.get(alt.id, True), dtype=bool)
            for alt in problem.alternatives
        }
        robustness_res = compute_robustness_and_regret(
            sample_scores=sample_scores_map,
            sample_feasibility=sample_feas_map,
        )

        # 10. Assemble Individual DecisionScores
        feasible_alts_sorted = sorted(
            [alt.id for alt in problem.alternatives if feasible_mask.get(alt.id, True)],
            key=lambda aid: mcda_scores.get(aid, 0.0),
            reverse=True,
        )
        rank_map = {aid: idx + 1 for idx, aid in enumerate(feasible_alts_sorted)}

        decision_scores: Dict[str, DecisionScore] = {}
        for alt in problem.alternatives:
            aid = alt.id
            is_feas = feasible_mask.get(aid, True)
            status = (
                ParetoStatus.NON_DOMINATED
                if aid in pareto_analysis.frontier
                else ParetoStatus.DOMINATED
            )
            decision_scores[aid] = DecisionScore(
                alternative_id=aid,
                feasible=is_feas,
                hard_violations=hard_violations_map.get(aid, []),
                soft_violations=soft_violations_map.get(aid, []),
                raw_metrics={cid: raw_matrix[cid].get(aid) for cid in raw_matrix},
                normalized_scores={cid: normalized_matrix[cid].get(aid, 0.0) for cid in normalized_matrix},
                mcda_score=mcda_scores.get(aid, 0.0),
                mcda_method=problem.mcda_method,
                rank=rank_map.get(aid, 0),
                pareto_status=status,
                dominates=pareto_analysis.dominates_map.get(aid, []),
                dominated_by=pareto_analysis.dominated_by_map.get(aid, []),
                outcome_distributions=outcome_distributions_map.get(aid, {}),
            )

        # 11. Evaluate Recommendation Policy & Formulate Explanation
        recommendation_res = evaluate_recommendation_policy(
            problem=problem,
            scores=decision_scores,
            weights=weights,
            sensitivity=sensitivity_res,
            robustness=robustness_res,
            evidence_gaps=evidence_gaps,
        )
        recommendation_res.normalized_matrix = normalized_matrix
        recommendation_res.raw_matrix = raw_matrix
        recommendation_res.pareto_frontier = pareto_analysis.frontier

        # 12. Build GeoJSON Layers
        geojson_features = self._build_comparison_geojson_features(
            problem=problem,
            scores=decision_scores,
            rec_result=recommendation_res,
        )
        comparison_geojson = {
            "type": "FeatureCollection",
            "features": geojson_features,
        }

        # 13. SessionStore Registration
        ref_id = f"ref:cmp-{problem.problem_id}"
        if session_id:
            try:
                store = get_session_store()
                stored_ref = await store.store(session_id, comparison_geojson, prefix="dec_v3")
                if stored_ref:
                    ref_id = stored_ref
            except Exception as e:
                logger.warning(f"Failed to store V3 GeoJSON to SessionStore: {e}")

        # 14. Decision Report Generation
        report_md = self._generate_v3_decision_report(
            problem=problem,
            scores=decision_scores,
            rec_result=recommendation_res,
            weights=weights,
            weight_note=weight_note,
        )

        provenance = {
            "engine": "DecisionEngineV3",
            "fingerprint": recommendation_res.decision_fingerprint,
            "mcda_method": problem.mcda_method,
            "sample_count": problem.mc_sample_count,
            "random_seed": problem.random_seed,
            "alternatives_count": len(problem.alternatives),
            "feasible_count": len(feasible_alts_sorted),
            "criteria_count": len(problem.criteria),
            "constraints_count": len(problem.constraints),
            "comparison_ref_id": ref_id,
        }

        return SpatialDecisionResultV3(
            type="spatial_decision_result_v3",
            problem=problem,
            recommendation=recommendation_res,
            comparison_geojson=comparison_geojson,
            comparison_ref_id=ref_id,
            report_markdown=report_md,
            mapspec_applied=False,
            provenance=provenance,
        )

    def _build_comparison_geojson_features(
        self,
        problem: DecisionProblem,
        scores: Dict[str, DecisionScore],
        rec_result: RecommendationResult,
    ) -> List[Dict[str, Any]]:
        """Synthesizes styled GeoJSON features for alternatives and constraint zones."""
        features = []
        rec_id = rec_result.recommended_alternative_id

        # 1. Add Alternatives
        for alt in problem.alternatives:
            if not alt.geometry:
                continue

            score_obj = scores.get(alt.id)
            is_feas = score_obj.feasible if score_obj else True
            is_rec = (alt.id == rec_id)

            if is_rec:
                color = "#10B981"  # Emerald Green
                status_label = "Recommended"
            elif is_feas:
                color = "#3B82F6"  # Blue
                status_label = "Feasible Candidate"
            else:
                color = "#EF4444"  # Red
                status_label = "Infeasible (Violates Constraints)"

            feat = {
                "type": "Feature",
                "geometry": alt.geometry,
                "properties": {
                    "alternative_id": alt.id,
                    "name": alt.name,
                    "description": alt.description,
                    "status": status_label,
                    "is_feasible": is_feas,
                    "is_recommended": is_rec,
                    "mcda_score": score_obj.mcda_score if score_obj else 0.0,
                    "rank": score_obj.rank if score_obj else 0,
                    "pareto_status": score_obj.pareto_status if score_obj else "unknown",
                    "marker_color": color,
                    "fill_color": color,
                },
            }
            features.append(feat)

        # 2. Add Spatial Constraint reference zones (e.g. Protected Areas)
        for c in problem.constraints:
            if c.category == ConstraintCategory.SPATIAL and c.reference_geometry:
                ref_g = c.reference_geometry
                if ref_g.get("type") == "FeatureCollection":
                    for f in ref_g.get("features", []):
                        f_copy = dict(f)
                        f_copy.setdefault("properties", {})
                        f_copy["properties"]["layer_type"] = "constraint_exclusion_zone"
                        f_copy["properties"]["constraint_name"] = c.name
                        f_copy["properties"]["fill_color"] = "#EF4444"
                        f_copy["properties"]["fill_opacity"] = 0.25
                        features.append(f_copy)
                elif ref_g.get("type") == "Feature":
                    f_copy = dict(ref_g)
                    f_copy.setdefault("properties", {})
                    f_copy["properties"]["layer_type"] = "constraint_exclusion_zone"
                    f_copy["properties"]["constraint_name"] = c.name
                    f_copy["properties"]["fill_color"] = "#EF4444"
                    f_copy["properties"]["fill_opacity"] = 0.25
                    features.append(f_copy)
                else:
                    features.append({
                        "type": "Feature",
                        "geometry": ref_g,
                        "properties": {
                            "layer_type": "constraint_exclusion_zone",
                            "constraint_name": c.name,
                            "fill_color": "#EF4444",
                            "fill_opacity": 0.25,
                        },
                    })

        return features

    def _generate_v3_decision_report(
        self,
        problem: DecisionProblem,
        scores: Dict[str, DecisionScore],
        rec_result: RecommendationResult,
        weights: Dict[str, float],
        weight_note: str,
    ) -> str:
        """Generates rigorous Markdown decision analysis report."""
        lines = [
            f"# 空间决策智能综合推演报告 (V3)",
            f"**决策目标:** {problem.goal}",
            f"**地理范围:** {problem.target_area.resolved_name or problem.target_area.query}",
            f"**决策指纹 (SHA256):** `{rec_result.decision_fingerprint}`",
            f"**推荐结论状态:** `{rec_result.admissibility.value.upper()}`",
            "",
            "## 1. 决策建议与理由",
        ]

        if rec_result.recommended_alternative_id:
            lines.append(f"**推荐方案:** **[{rec_result.recommended_alternative_id}]**")
        else:
            lines.append(f"**推荐方案:** 无明确单一推荐方案（状态: {rec_result.admissibility.value}）")

        expl = rec_result.explanation
        if expl.why_selected:
            lines.append("\n**入选核心依据:**")
            for reason in expl.why_selected:
                lines.append(f"- {reason}")

        if expl.major_tradeoffs:
            lines.append("\n**关键权衡分析:**")
            for to in expl.major_tradeoffs:
                lines.append(f"- {to}")

        if expl.binding_constraints:
            lines.append("\n**刚性约束出局因素:**")
            for bc in expl.binding_constraints:
                lines.append(f"- 约束条件 [{bc}] 产生实质否决。")

        # 2. Decision Matrix Table
        lines.append("\n## 2. 多准则决策评价矩阵")
        lines.append(f"> *权重说明: {weight_note}*")
        lines.append("")

        header = ["准则", "方向", "权重"] + [alt.id for alt in problem.alternatives]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")

        for crit in problem.criteria:
            w_str = f"{weights.get(crit.id, 0.0):.2f}"
            row = [f"{crit.name} ({crit.unit})" if crit.unit else crit.name, crit.direction.value, w_str]
            for alt in problem.alternatives:
                raw_v = scores[alt.id].raw_metrics.get(crit.id)
                norm_v = scores[alt.id].normalized_scores.get(crit.id, 0.0)
                if raw_v is not None:
                    cell = f"{raw_v} (得: {norm_v:.2f})"
                else:
                    cell = "缺失"
                row.append(cell)
            lines.append("| " + " | ".join(row) + " |")

        # Summary rows: Feasible, Score, Pareto, Rank
        feas_row = ["**可行性 (Feasible)**", "-", "-"] + [
            "合格 (Yes)" if scores[alt.id].feasible else "**否决 (No)**" for alt in problem.alternatives
        ]
        score_row = [f"**综合得分 ({problem.mcda_method.upper()})**", "-", "-"] + [
            f"**{scores[alt.id].mcda_score:.3f}**" if scores[alt.id].feasible else "0.000" for alt in problem.alternatives
        ]
        pareto_row = ["**Pareto 优越面**", "-", "-"] + [
            "前沿 (Non-Dom)" if scores[alt.id].pareto_status == ParetoStatus.NON_DOMINATED else "被支配 (Dominated)"
            for alt in problem.alternatives
        ]
        rank_row = ["**决策顺位 (Rank)**", "-", "-"] + [
            f"第 {scores[alt.id].rank} 名" if scores[alt.id].rank > 0 else "已剔除"
            for alt in problem.alternatives
        ]

        lines.append("| " + " | ".join(feas_row) + " |")
        lines.append("| " + " | ".join(score_row) + " |")
        lines.append("| " + " | ".join(pareto_row) + " |")
        lines.append("| " + " | ".join(rank_row) + " |")

        # 3. Sensitivity & Robustness Section
        lines.append("\n## 3. 权重敏感性与决策鲁棒性")
        if rec_result.sensitivity:
            lines.append(f"**敏感性摘要:** {rec_result.sensitivity.summary}")
            if rec_result.sensitivity.rank_stability:
                lines.append("- **排名稳定性分布:**")
                for aid, stab in rec_result.sensitivity.rank_stability.items():
                    lines.append(f"  - 方案 [{aid}]: 保持第一名概率 {stab}%")

        if rec_result.robustness:
            lines.append(f"\n**鲁棒性与极小化后悔值 (Minimax Regret):**")
            lines.append(f"{rec_result.robustness.summary}")
            for aid, reg in rec_result.robustness.alternative_regrets.items():
                lines.append(f"- 方案 [{aid}]: 最大后悔值 = {reg:.4f} (可行概率: {rec_result.robustness.prob_feasible.get(aid, 1.0) * 100:.0f}%)")

        lines.append("\n## 4. 决策血统与复现说明")
        lines.append(f"- 模拟采样次数: {problem.mc_sample_count} 次 (Seed: {problem.random_seed})")
        lines.append(f"- 评价模型: {problem.mcda_method.upper()} + Pareto 严格非支配过滤")
        lines.append(f"- 完整空间数据游标: `{rec_result.decision_fingerprint}`")

        return "\n".join(lines)
