"""
Sensitivity Analysis Engine for Spatial Decision Intelligence V3.
Performs criterion weight perturbation, calculates rank stability, detects critical weight thresholds,
and identifies the specific criteria trade-offs that drive decision flips.
"""
import logging
from typing import Any, Dict, List
import numpy as np

from app.services.spatial_decision.models_v3 import SensitivityResult

logger = logging.getLogger(__name__)


def analyze_weight_sensitivity(
    normalized_matrix: Dict[str, Dict[str, float]],
    baseline_weights: Dict[str, float],
    feasible_mask: Dict[str, bool],
    n_perturbations: int = 500,
    perturbation_scale: float = 0.25,
    random_seed: int = 42,
) -> SensitivityResult:
    """
    Evaluates sensitivity of decision rankings to criterion weights.

    Args:
        normalized_matrix: {criterion_id: {alt_id: normalized_val}}.
        baseline_weights: {criterion_id: baseline_weight} summing to 1.0.
        feasible_mask: {alt_id: is_feasible}.
        n_perturbations: Number of perturbed weight vectors evaluated.
        perturbation_scale: Relative perturbation scale (e.g. 0.25 = +/-25%).
        random_seed: PRNG seed for deterministic reproducibility.

    Returns:
        SensitivityResult object with rank stability percentages and trade-off drivers.
    """
    cids = sorted(list(baseline_weights.keys()))
    if not cids:
        return SensitivityResult(summary="No criteria provided for sensitivity analysis.")

    feasible_alts = sorted([a for a, f in feasible_mask.items() if f])
    if len(feasible_alts) <= 1:
        single_id = feasible_alts[0] if feasible_alts else "none"
        return SensitivityResult(
            rank_stability={single_id: 100.0} if feasible_alts else {},
            top_rank_probabilities={single_id: 1.0} if feasible_alts else {},
            summary="Single or zero feasible alternatives; rankings are trivially insensitive to weights.",
        )

    rng = np.random.default_rng(random_seed)
    base_w_arr = np.array([baseline_weights[cid] for cid in cids], dtype=float)

    # Matrix shape: (n_alts, n_criteria)
    matrix_arr = np.array(
        [[normalized_matrix.get(cid, {}).get(alt_id, 0.0) for cid in cids] for alt_id in feasible_alts],
        dtype=float,
    )

    top_count = {alt_id: 0 for alt_id in feasible_alts}

    for _ in range(n_perturbations):
        # Generate random multiplicative factors in [1 - scale, 1 + scale]
        factors = rng.uniform(1.0 - perturbation_scale, 1.0 + perturbation_scale, size=len(cids))
        w_pert = np.maximum(0.001, base_w_arr * factors)
        w_pert /= np.sum(w_pert)  # Re-normalize to 1.0

        scores = matrix_arr @ w_pert
        best_idx = int(np.argmax(scores))
        winner_id = feasible_alts[best_idx]
        top_count[winner_id] += 1

    rank_stability = {
        alt_id: round((count / n_perturbations) * 100.0, 1)
        for alt_id, count in top_count.items()
    }
    top_rank_probs = {
        alt_id: round(count / n_perturbations, 4)
        for alt_id, count in top_count.items()
    }

    # 1D threshold search: sweep each criterion from 0.05 to 0.95 to find tipping points
    critical_thresholds: List[Dict[str, Any]] = []
    tradeoff_drivers: List[str] = []

    # Find baseline winner
    base_scores = matrix_arr @ base_w_arr
    base_winner_id = feasible_alts[int(np.argmax(base_scores))]

    for i, target_cid in enumerate(cids):
        # Sweep target weight from 0.05 to 0.95
        sweep_weights = np.linspace(0.05, 0.95, 19)
        other_indices = [j for j in range(len(cids)) if j != i]
        other_base_sum = np.sum(base_w_arr[other_indices])

        switch_found = False
        tipping_w = None
        new_winner = None

        for tw in sweep_weights:
            w_sweep = np.zeros(len(cids), dtype=float)
            w_sweep[i] = tw
            remaining = 1.0 - tw
            if other_base_sum > 1e-9:
                w_sweep[other_indices] = (base_w_arr[other_indices] / other_base_sum) * remaining
            else:
                w_sweep[other_indices] = remaining / len(other_indices)

            sweep_scores = matrix_arr @ w_sweep
            cur_winner = feasible_alts[int(np.argmax(sweep_scores))]
            if cur_winner != base_winner_id and not switch_found:
                switch_found = True
                tipping_w = round(float(tw), 3)
                new_winner = cur_winner
                break

        if switch_found and tipping_w is not None:
            critical_thresholds.append({
                "criterion_id": target_cid,
                "tipping_weight": tipping_w,
                "baseline_weight": round(float(base_w_arr[i]), 3),
                "alternative_promoted": new_winner,
            })
            tradeoff_drivers.append(
                f"When criterion '{target_cid}' weight reaches {tipping_w * 100:.0f}%, "
                f"ranking switches from [{base_winner_id}] to [{new_winner}]."
            )

    highest_stability = max(rank_stability.values()) if rank_stability else 0.0
    summary_lines = [
        f"Rank stability for leading candidate [{base_winner_id}]: {rank_stability.get(base_winner_id, 0.0)}% "
        f"under +/-{int(perturbation_scale * 100)}% weight perturbations."
    ]
    if highest_stability < 65.0:
        summary_lines.append(
            "Ranking exhibits moderate-to-high instability: outcome depends strongly on preference weighting."
        )
    else:
        summary_lines.append("Leading recommendation is robust to plausible weight perturbations.")

    return SensitivityResult(
        rank_stability=rank_stability,
        top_rank_probabilities=top_rank_probs,
        critical_weight_thresholds=critical_thresholds,
        tradeoff_drivers=tradeoff_drivers,
        summary=" ".join(summary_lines),
    )
