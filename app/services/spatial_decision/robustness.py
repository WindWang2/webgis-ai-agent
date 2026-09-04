"""
Robustness and Minimax Regret Engine for Spatial Decision Intelligence V3.
Quantifies decision resilience under stochastic uncertainty, parameter variation,
and worst-case performance bounds.
"""
import logging
from typing import Dict, Optional
import numpy as np

from app.services.spatial_decision.models_v3 import RobustnessResult

logger = logging.getLogger(__name__)


def compute_robustness_and_regret(
    sample_scores: Dict[str, np.ndarray],
    sample_feasibility: Optional[Dict[str, np.ndarray]] = None,
) -> RobustnessResult:
    """
    Computes Minimax Regret, probability of top ranking, feasibility probability,
    and worst-case normalized performance across Monte Carlo samples.

    Args:
        sample_scores: Mapping of alt_id to array of composite scores across N samples.
        sample_feasibility: Optional mapping of alt_id to boolean array of feasibility across N samples.

    Returns:
        RobustnessResult object.
    """
    alts = sorted(list(sample_scores.keys()))
    if not alts:
        return RobustnessResult(summary="No alternatives evaluated for robustness.")

    n_samples = len(next(iter(sample_scores.values())))
    if n_samples == 0:
        return RobustnessResult(summary="Zero simulation samples available.")

    # Stack scores: shape (n_alts, n_samples)
    score_matrix = np.array([sample_scores[alt_id] for alt_id in alts], dtype=float)

    if sample_feasibility:
        feas_matrix = np.array([sample_feasibility[alt_id] for alt_id in alts], dtype=bool)
    else:
        feas_matrix = np.ones((len(alts), n_samples), dtype=bool)

    # Infeasible entries penalized to -inf for state-best calculation
    effective_scores = np.where(feas_matrix, score_matrix, -np.inf)

    # 1. State-best score for each sample: shape (n_samples,)
    u_star = np.max(effective_scores, axis=0)

    # Regret matrix: shape (n_alts, n_samples)
    # If all infeasible in a sample, u_star is -inf, handle gracefully
    u_star_clean = np.where(np.isneginf(u_star), 0.0, u_star)
    effective_scores_clean = np.where(np.isneginf(effective_scores), 0.0, effective_scores)
    regret_matrix = np.maximum(0.0, u_star_clean[np.newaxis, :] - effective_scores_clean)

    # Maximum regret per alternative
    max_regrets = np.max(regret_matrix, axis=1)
    alternative_regrets = {
        alts[i]: round(float(max_regrets[i]), 4) for i in range(len(alts))
    }

    # 2. Probability of top rank
    top_indices = np.argmax(effective_scores, axis=0)
    top_counts = {alts[i]: 0 for i in range(len(alts))}
    for s, idx in enumerate(top_indices):
        # Check if winner was actually feasible in this sample
        if feas_matrix[idx, s] and np.isfinite(effective_scores[idx, s]):
            top_counts[alts[idx]] += 1

    prob_top_ranked = {
        alt_id: round(top_counts[alt_id] / n_samples, 4) for alt_id in alts
    }

    # 3. Probability of satisfying hard constraints
    prob_feasible = {
        alts[i]: round(float(np.mean(feas_matrix[i])), 4) for i in range(len(alts))
    }

    # 4. Worst-case performance across samples
    worst_case = {
        alts[i]: round(float(np.min(effective_scores_clean[i])), 4) for i in range(len(alts))
    }

    # Identify robust winner: minimizes maximum regret among alternatives with high feasibility
    feasible_regrets = {
        alt: r for alt, r in alternative_regrets.items() if prob_feasible.get(alt, 0.0) >= 0.8
    }
    robust_winner_id = (
        min(feasible_regrets, key=feasible_regrets.get) if feasible_regrets else None
    )

    summary = (
        f"Robustness evaluation over {n_samples} stochastic draws: "
        f"Alternative [{robust_winner_id or 'none'}] minimizes maximum regret "
        f"({alternative_regrets.get(robust_winner_id, 0.0)}) with "
        f"{round(prob_top_ranked.get(robust_winner_id, 0.0) * 100, 1)}% probability of achieving rank #1."
    )

    return RobustnessResult(
        alternative_regrets=alternative_regrets,
        prob_top_ranked=prob_top_ranked,
        prob_feasible=prob_feasible,
        worst_case_normalized=worst_case,
        robust_winner_id=robust_winner_id,
        summary=summary,
    )
