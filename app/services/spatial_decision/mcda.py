"""
Multi-Criteria Decision Analysis (MCDA) Engine for Spatial Decision Intelligence V3.
Implements mathematically sound Weighted Sum Model (WSM) and TOPSIS.
Guarantees unit safety via normalized decision matrices and excludes infeasible alternatives.
"""
import math
import logging
from typing import Dict, List, Optional

from app.services.spatial_decision.models_v3 import (
    Criterion,
    CriterionDirection,
)

logger = logging.getLogger(__name__)


class MultiCriteriaDecisionEngine:
    """Reusable Multi-Criteria Decision Engine supporting WSM and TOPSIS."""

    def evaluate_wsm(
        self,
        normalized_matrix: Dict[str, Dict[str, float]],
        weights: Dict[str, float],
        feasible_mask: Dict[str, bool],
        soft_penalties: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Computes Weighted Sum Model composite scores: S_i = sum(w_j * r_ij) - penalties.

        Args:
            normalized_matrix: {criterion_id: {alt_id: normalized_value}} in [0.0, 1.0].
            weights: {criterion_id: weight} summing to 1.0.
            feasible_mask: {alt_id: is_feasible}.
            soft_penalties: {alt_id: total_soft_penalty}.

        Returns:
            Mapping of alt_id to final WSM score.
        """
        soft_penalties = soft_penalties or {}
        scores: Dict[str, float] = {}

        # Get list of all alternative IDs
        all_alts = set()
        for c_dict in normalized_matrix.values():
            all_alts.update(c_dict.keys())

        for alt_id in all_alts:
            if not feasible_mask.get(alt_id, True):
                # Infeasible alternative receives 0.0 composite score
                scores[alt_id] = 0.0
                continue

            raw_score = 0.0
            for cid, w in weights.items():
                r_ij = normalized_matrix.get(cid, {}).get(alt_id, 0.0)
                raw_score += w * r_ij

            # Deduct soft penalty if any (cannot drop below 0.0)
            penalty = soft_penalties.get(alt_id, 0.0)
            final_score = max(0.0, raw_score - penalty)
            scores[alt_id] = round(final_score, 6)

        return scores

    def evaluate_topsis(
        self,
        raw_matrix: Dict[str, Dict[str, Optional[float]]],
        criteria: List[Criterion],
        weights: Dict[str, float],
        feasible_mask: Dict[str, bool],
        soft_penalties: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Computes TOPSIS relative closeness to ideal solution:
        C_i = S_i^- / (S_i^+ + S_i^-).

        Args:
            raw_matrix: {criterion_id: {alt_id: raw_val}}.
            criteria: Criterion definitions with direction.
            weights: Normalized weights summing to 1.0.
            feasible_mask: {alt_id: is_feasible}.
            soft_penalties: {alt_id: total_soft_penalty}.

        Returns:
            Mapping of alt_id to TOPSIS closeness score in [0.0, 1.0].
        """
        soft_penalties = soft_penalties or {}
        scores: Dict[str, float] = {}

        all_alts = set()
        for c_dict in raw_matrix.values():
            all_alts.update(c_dict.keys())

        # Vector normalization per criterion
        v_matrix: Dict[str, Dict[str, float]] = {}  # {cid: {alt_id: weighted_normalized}}
        v_plus: Dict[str, float] = {}   # Positive ideal solution
        v_minus: Dict[str, float] = {}  # Negative ideal solution

        for crit in criteria:
            cid = crit.id
            w = weights.get(cid, 0.0)
            alt_vals = raw_matrix.get(cid, {})

            # Calculate vector denominator: sqrt(sum(x^2)) across valid values
            valid_vals = [v for v in alt_vals.values() if v is not None and not math.isnan(v)]
            denom = math.sqrt(sum(v * v for v in valid_vals)) if valid_vals else 0.0

            if crit.direction == CriterionDirection.TARGET:
                target_val = (
                    crit.target_value
                    if crit.target_value is not None
                    else ((min(valid_vals) + max(valid_vals)) / 2.0 if valid_vals else 0.0)
                )
                dev_vals = {
                    alt_id: abs(raw_v - target_val) if (raw_v is not None and not math.isnan(raw_v)) else None
                    for alt_id, raw_v in alt_vals.items()
                }
                valid_devs = [v for v in dev_vals.values() if v is not None]
                denom = math.sqrt(sum(v * v for v in valid_devs)) if valid_devs else 0.0

                v_matrix[cid] = {}
                col_weighted = []

                for alt_id in all_alts:
                    dev_v = dev_vals.get(alt_id)
                    if dev_v is None or denom == 0.0:
                        norm_v = 0.0
                    else:
                        norm_v = dev_v / denom
                    weighted_v = norm_v * w
                    v_matrix[cid][alt_id] = weighted_v
                    if feasible_mask.get(alt_id, True):
                        col_weighted.append(weighted_v)

                if not col_weighted:
                    col_weighted = [0.0]

                # Ideal deviation is 0.0 (exact target match)
                # Negative ideal deviation is max deviation observed
                v_plus[cid] = 0.0
                v_minus[cid] = max(col_weighted)

            elif crit.direction == CriterionDirection.RANGE:
                bounds = crit.range_bounds or (
                    (min(valid_vals), max(valid_vals)) if valid_vals else (0.0, 0.0)
                )
                r_min, r_max = min(bounds), max(bounds)

                def _range_dev(val: Optional[float]) -> Optional[float]:
                    if val is None or math.isnan(val):
                        return None
                    if val < r_min:
                        return r_min - val
                    if val > r_max:
                        return val - r_max
                    return 0.0

                dev_vals = {alt_id: _range_dev(alt_vals.get(alt_id)) for alt_id in all_alts}
                valid_devs = [v for v in dev_vals.values() if v is not None]
                denom = math.sqrt(sum(v * v for v in valid_devs)) if valid_devs else 0.0

                v_matrix[cid] = {}
                col_weighted = []

                for alt_id in all_alts:
                    dev_v = dev_vals.get(alt_id)
                    if dev_v is None or denom == 0.0:
                        norm_v = 0.0
                    else:
                        norm_v = dev_v / denom
                    weighted_v = norm_v * w
                    v_matrix[cid][alt_id] = weighted_v
                    if feasible_mask.get(alt_id, True):
                        col_weighted.append(weighted_v)

                if not col_weighted:
                    col_weighted = [0.0]

                # Ideal deviation is 0.0 (inside acceptable range)
                # Negative ideal deviation is max deviation outside range
                v_plus[cid] = 0.0
                v_minus[cid] = max(col_weighted)

            elif crit.direction == CriterionDirection.MINIMIZE:
                v_matrix[cid] = {}
                col_weighted = []

                for alt_id in all_alts:
                    raw_v = alt_vals.get(alt_id)
                    if raw_v is None or denom == 0.0:
                        norm_v = 0.0
                    else:
                        norm_v = raw_v / denom
                    weighted_v = norm_v * w
                    v_matrix[cid][alt_id] = weighted_v
                    if feasible_mask.get(alt_id, True):
                        col_weighted.append(weighted_v)

                if not col_weighted:
                    col_weighted = [0.0]

                # Cost criterion: ideal is min, negative ideal is max
                v_plus[cid] = min(col_weighted)
                v_minus[cid] = max(col_weighted)

            else:  # MAXIMIZE or default
                v_matrix[cid] = {}
                col_weighted = []

                for alt_id in all_alts:
                    raw_v = alt_vals.get(alt_id)
                    if raw_v is None or denom == 0.0:
                        norm_v = 0.0
                    else:
                        norm_v = raw_v / denom
                    weighted_v = norm_v * w
                    v_matrix[cid][alt_id] = weighted_v
                    if feasible_mask.get(alt_id, True):
                        col_weighted.append(weighted_v)

                if not col_weighted:
                    col_weighted = [0.0]

                # Benefit: ideal is max, negative ideal is min
                v_plus[cid] = max(col_weighted)
                v_minus[cid] = min(col_weighted)

        # Calculate Euclidean distances to A+ and A-
        for alt_id in all_alts:
            if not feasible_mask.get(alt_id, True):
                scores[alt_id] = 0.0
                continue

            d_plus_sq = 0.0
            d_minus_sq = 0.0

            for crit in criteria:
                cid = crit.id
                v_ij = v_matrix[cid].get(alt_id, 0.0)
                d_plus_sq += (v_ij - v_plus[cid]) ** 2
                d_minus_sq += (v_ij - v_minus[cid]) ** 2

            d_plus = math.sqrt(d_plus_sq)
            d_minus = math.sqrt(d_minus_sq)

            if (d_plus + d_minus) < 1e-12:
                closeness = 1.0
            else:
                closeness = d_minus / (d_plus + d_minus)

            penalty = soft_penalties.get(alt_id, 0.0)
            scores[alt_id] = round(max(0.0, closeness - penalty), 6)

        return scores
