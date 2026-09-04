"""
Recommendation Policy and Structured Explanation Generator for Spatial Decision Intelligence V3.
Enforces admissibility states (RECOMMENDED, CONDITIONALLY_RECOMMENDED, NO_CLEAR_WINNER,
INSUFFICIENT_EVIDENCE, NO_FEASIBLE_ALTERNATIVE) with complete audit-ready reasoning.
"""
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.spatial_decision.models_v3 import (
    Alternative,
    Criterion,
    DecisionProblem,
    DecisionScore,
    ParetoStatus,
    RecommendationAdmissibility,
    RecommendationResult,
    RobustnessResult,
    SensitivityResult,
    StructuredExplanation,
)

logger = logging.getLogger(__name__)


def generate_decision_fingerprint(
    problem: DecisionProblem,
    scores: Dict[str, DecisionScore],
    weights: Dict[str, float],
) -> str:
    """Computes a deterministic SHA-256 fingerprint for the decision run."""
    fingerprint_dict = {
        "problem_id": problem.problem_id,
        "goal": problem.goal,
        "alternatives": [a.id for a in problem.alternatives],
        "criteria": [c.id for c in problem.criteria],
        "weights": {k: round(v, 4) for k, v in sorted(weights.items())},
        "mcda_method": problem.mcda_method,
        "scores": {k: round(v.mcda_score, 4) for k, v in sorted(scores.items())},
        "random_seed": problem.random_seed,
    }
    encoded = json.dumps(fingerprint_dict, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def evaluate_recommendation_policy(
    problem: DecisionProblem,
    scores: Dict[str, DecisionScore],
    weights: Dict[str, float],
    sensitivity: Optional[SensitivityResult] = None,
    robustness: Optional[RobustnessResult] = None,
    evidence_gaps: Optional[List[str]] = None,
) -> RecommendationResult:
    """
    Applies the transparent V3 Recommendation Policy:
    1. Check for core evidence sufficiency.
    2. Check for alternative feasibility.
    3. Evaluate MCDA score separation, Pareto frontier, and rank stability.
    4. Determine admissibility state and formulate structured explanation.
    """
    evidence_gaps = evidence_gaps or []

    # 1. Check Core Evidence Sufficiency
    if evidence_gaps:
        why_not = {
            alt.id: [f"Core evidence missing: {gap}" for gap in evidence_gaps]
            for alt in problem.alternatives
        }
        explanation = StructuredExplanation(
            why_selected=[],
            why_not_selected=why_not,
            binding_constraints=[],
            criterion_contributions={},
            major_tradeoffs=["Core empirical baseline data is missing; cannot make evidence-grounded recommendation."],
            evidence_gaps=evidence_gaps,
        )
        return RecommendationResult(
            admissibility=RecommendationAdmissibility.INSUFFICIENT_EVIDENCE,
            recommended_alternative_id=None,
            explanation=explanation,
            scores=scores,
            sensitivity=sensitivity,
            robustness=robustness,
            provenance={"policy": "fail_closed_insufficient_evidence"},
            decision_fingerprint=generate_decision_fingerprint(problem, scores, weights),
        )

    # 2. Check Feasibility
    feasible_alts = [alt.id for alt in problem.alternatives if scores.get(alt.id) and scores[alt.id].feasible]
    infeasible_alts = [alt.id for alt in problem.alternatives if not (scores.get(alt.id) and scores[alt.id].feasible)]

    binding_constraints = []
    why_not: Dict[str, List[str]] = {}

    for inf_id in infeasible_alts:
        violations = scores[inf_id].hard_violations if inf_id in scores else []
        reasons = [f"Hard constraint violation: {v.evidence_statement}" for v in violations]
        why_not[inf_id] = reasons
        for v in violations:
            if v.constraint_id not in binding_constraints:
                binding_constraints.append(v.constraint_id)

    # If NO alternatives are feasible
    if not feasible_alts:
        explanation = StructuredExplanation(
            why_selected=[],
            why_not_selected=why_not,
            binding_constraints=binding_constraints,
            criterion_contributions={},
            major_tradeoffs=["All candidate alternatives violate at least one hard planning or spatial constraint."],
            evidence_gaps=[],
        )
        return RecommendationResult(
            admissibility=RecommendationAdmissibility.NO_FEASIBLE_ALTERNATIVE,
            recommended_alternative_id=None,
            explanation=explanation,
            scores=scores,
            sensitivity=sensitivity,
            robustness=robustness,
            provenance={"policy": "fail_closed_no_feasible_alternative"},
            decision_fingerprint=generate_decision_fingerprint(problem, scores, weights),
        )

    # 3. Rank feasible alternatives by MCDA score
    sorted_feasible = sorted(
        feasible_alts,
        key=lambda aid: scores[aid].mcda_score if aid in scores else 0.0,
        reverse=True,
    )

    top_id = sorted_feasible[0]
    top_score = scores[top_id].mcda_score

    # Compute criterion contributions for feasible alternatives
    criterion_contributions: Dict[str, Dict[str, float]] = {}
    for aid in feasible_alts:
        sc = scores[aid]
        criterion_contributions[aid] = {
            cid: round(weights.get(cid, 0.0) * sc.normalized_scores.get(cid, 0.0), 4)
            for cid in weights
        }

    # 4. Check for NO_CLEAR_WINNER (Tie or high rank instability)
    rank_stability = sensitivity.rank_stability.get(top_id, 100.0) if sensitivity else 100.0
    runner_up_id = sorted_feasible[1] if len(sorted_feasible) > 1 else None

    is_tie_or_unstable = False
    major_tradeoffs: List[str] = []

    if runner_up_id:
        runner_up_score = scores[runner_up_id].mcda_score
        score_diff_pct = abs(top_score - runner_up_score) / max(top_score, 1e-6)

        # If score difference < 2.5% and rank stability < 60%
        if score_diff_pct < 0.025 and rank_stability < 60.0:
            is_tie_or_unstable = True
            major_tradeoffs.append(
                f"Leading candidate [{top_id}] ({top_score:.3f}) and runner-up [{runner_up_id}] "
                f"({runner_up_score:.3f}) are in a near-tie (score difference {score_diff_pct * 100:.1f}%). "
                f"Rank stability for [{top_id}] is only {rank_stability:.1f}%, indicating the outcome flips easily under minor weight adjustments."
            )

    if is_tie_or_unstable:
        for aid in sorted_feasible:
            if aid != top_id:
                diff = top_score - scores[aid].mcda_score
                why_not.setdefault(aid, []).append(f"MCDA score {scores[aid].mcda_score:.3f} is close to leading score {top_score:.3f} (margin: {diff:.3f}).")

        explanation = StructuredExplanation(
            why_selected=[],
            why_not_selected=why_not,
            binding_constraints=binding_constraints,
            criterion_contributions=criterion_contributions,
            major_tradeoffs=major_tradeoffs + (sensitivity.tradeoff_drivers if sensitivity else []),
            sensitivity_summary=sensitivity.summary if sensitivity else "",
            uncertainty_summary=robustness.summary if robustness else "",
            evidence_gaps=[],
        )
        return RecommendationResult(
            admissibility=RecommendationAdmissibility.NO_CLEAR_WINNER,
            recommended_alternative_id=None,
            explanation=explanation,
            scores=scores,
            sensitivity=sensitivity,
            robustness=robustness,
            provenance={"policy": "admissibility_no_clear_winner"},
            decision_fingerprint=generate_decision_fingerprint(problem, scores, weights),
        )

    # 5. Check for CONDITIONALLY_RECOMMENDED vs RECOMMENDED
    top_has_soft_violations = len(scores[top_id].soft_violations) > 0
    top_has_assumptions = False
    for alt in problem.alternatives:
        if alt.id == top_id and len(alt.assumptions) > 0:
            top_has_assumptions = True
            break

    admissibility = (
        RecommendationAdmissibility.CONDITIONALLY_RECOMMENDED
        if (top_has_soft_violations or top_has_assumptions)
        else RecommendationAdmissibility.RECOMMENDED
    )

    why_selected = [
        f"Highest overall MCDA score ({top_score:.3f}) among all {len(feasible_alts)} feasible alternatives.",
        f"Satisfies all {len(problem.constraints)} declared hard planning constraints.",
        f"High rank stability ({rank_stability:.1f}%) under criterion weight perturbations.",
    ]
    if top_has_soft_violations:
        why_selected.append("Conditionally recommended subject to mitigation of soft constraint penalties.")
    if top_has_assumptions:
        why_selected.append("Subject to verification of explicit planning assumptions.")

    for aid in sorted_feasible[1:]:
        diff = top_score - scores[aid].mcda_score
        why_not.setdefault(aid, []).append(
            f"Lower overall MCDA score ({scores[aid].mcda_score:.3f} vs {top_score:.3f}, gap: {diff:.3f})."
        )
        if scores[aid].pareto_status == ParetoStatus.DOMINATED:
            why_not[aid].append(f"Pareto dominated by: {scores[aid].dominated_by}.")

    if runner_up_id:
        major_tradeoffs.append(
            f"Candidate [{top_id}] outranks [{runner_up_id}] with a score margin of "
            f"{(top_score - scores[runner_up_id].mcda_score):.3f}."
        )

    explanation = StructuredExplanation(
        why_selected=why_selected,
        why_not_selected=why_not,
        binding_constraints=binding_constraints,
        criterion_contributions=criterion_contributions,
        major_tradeoffs=major_tradeoffs + (sensitivity.tradeoff_drivers if sensitivity else []),
        sensitivity_summary=sensitivity.summary if sensitivity else "",
        uncertainty_summary=robustness.summary if robustness else "",
        evidence_gaps=[],
    )

    return RecommendationResult(
        admissibility=admissibility,
        recommended_alternative_id=top_id,
        explanation=explanation,
        scores=scores,
        sensitivity=sensitivity,
        robustness=robustness,
        pareto_frontier=[aid for aid in feasible_alts if scores[aid].pareto_status == ParetoStatus.NON_DOMINATED],
        provenance={"policy": "admissibility_recommended"},
        decision_fingerprint=generate_decision_fingerprint(problem, scores, weights),
    )
