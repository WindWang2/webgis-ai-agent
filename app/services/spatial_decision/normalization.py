"""
Unit Safety and Criterion Normalization Framework for Spatial Decision Intelligence V3.
Converts heterogeneous physical quantities (minutes, RMB, people, AQI, km2) into dimensionless
normalized utilities in [0.0, 1.0] with robust handling of zero range, negative values, and missing data.
"""
import math
import logging
from typing import Dict, List, Optional, Tuple

from app.services.spatial_decision.models_v3 import (
    Criterion,
    CriterionDirection,
    NormalizationStrategy,
    MissingPolicy,
    WeightSource,
)

logger = logging.getLogger(__name__)


class NormalizationError(ValueError):
    """Raised when normalization cannot proceed due to invalid inputs or unknown direction."""
    pass


def normalize_criterion_values(
    raw_values: Dict[str, Optional[float]],
    criterion: Criterion,
) -> Dict[str, float]:
    """
    Normalizes a dictionary of {alternative_id: raw_value} for a single criterion into [0.0, 1.0].

    Args:
        raw_values: Mapping of alternative ID to numeric raw metric value (or None if missing).
        criterion: Criterion definition with direction, normalization strategy, and bounds.

    Returns:
        Mapping of alternative ID to normalized utility score in [0.0, 1.0].
    """
    # 1. Direction validation
    direction = criterion.direction
    if direction == CriterionDirection.UNKNOWN:
        raise NormalizationError(
            f"Criterion '{criterion.id}' has UNKNOWN direction. Explicit optimization direction "
            f"(MAXIMIZE, MINIMIZE, TARGET, or RANGE) is required before normalization."
        )

    # 2. Filter available finite values
    valid_items = {
        alt_id: val
        for alt_id, val in raw_values.items()
        if val is not None and not math.isnan(val) and not math.isinf(val)
    }

    # If all values are missing
    if not valid_items:
        if criterion.missing_policy == MissingPolicy.FAIL_CLOSED and criterion.is_core:
            raise NormalizationError(
                f"Core criterion '{criterion.name}' ({criterion.id}) has no valid measurements across any alternative."
            )
        return {alt_id: 0.0 for alt_id in raw_values}

    vals = list(valid_items.values())
    min_val = min(vals)
    max_val = max(vals)
    val_range = max_val - min_val

    normalized: Dict[str, float] = {}

    # 3. Strategy evaluation
    for alt_id, raw_val in raw_values.items():
        if raw_val is None or math.isnan(raw_val) or math.isinf(raw_val):
            # Missing policy handling
            if criterion.missing_policy == MissingPolicy.FAIL_CLOSED:
                if criterion.is_core:
                    raise NormalizationError(
                        f"Alternative '{alt_id}' lacks core baseline metric for '{criterion.name}'."
                    )
                normalized[alt_id] = 0.0
            elif criterion.missing_policy == MissingPolicy.PENALIZE:
                normalized[alt_id] = 0.0
            else:
                normalized[alt_id] = 0.0
            continue

        score = 0.0
        # Edge case: All values identical (zero range)
        if abs(val_range) < 1e-12:
            # If all alternatives have the same value, they are tied
            if direction == CriterionDirection.TARGET and criterion.target_value is not None:
                diff = abs(raw_val - criterion.target_value)
                score = max(0.0, 1.0 - diff / max(abs(criterion.target_value), 1.0))
            else:
                score = 1.0
            normalized[alt_id] = round(max(0.0, min(1.0, score)), 6)
            continue

        if direction == CriterionDirection.MAXIMIZE:
            # Min-Max Benefit: higher is better
            score = (raw_val - min_val) / val_range
        elif direction == CriterionDirection.MINIMIZE:
            # Min-Max Cost: lower is better
            score = (max_val - raw_val) / val_range
        elif direction == CriterionDirection.TARGET:
            target = criterion.target_value if criterion.target_value is not None else (min_val + max_val) / 2.0
            max_dist = max(abs(max_val - target), abs(min_val - target), 1e-9)
            dist = abs(raw_val - target)
            score = 1.0 - (dist / max_dist)
        elif direction == CriterionDirection.RANGE:
            bounds = criterion.range_bounds or (min_val, max_val)
            r_min, r_max = min(bounds), max(bounds)
            if r_min <= raw_val <= r_max:
                score = 1.0
            elif raw_val < r_min:
                score = max(0.0, 1.0 - (r_min - raw_val) / max(abs(r_min), 1.0))
            else:
                score = max(0.0, 1.0 - (raw_val - r_max) / max(abs(r_max), 1.0))
        else:
            score = (raw_val - min_val) / val_range

        normalized[alt_id] = round(max(0.0, min(1.0, score)), 6)

    return normalized


def normalize_weights(criteria: List[Criterion]) -> Tuple[Dict[str, float], str]:
    """
    Normalizes criterion weights to strictly sum to 1.0.
    Rejects negative weights, NaNs, and infinite weights.
    Falls back to transparent equal weighting if no weights are provided.

    Returns:
        Tuple[Dict[criterion_id, normalized_weight], weight_rationale_note]
    """
    if not criteria:
        raise ValueError("Criteria list cannot be empty.")

    weights: Dict[str, float] = {}
    has_user_weights = False

    for crit in criteria:
        w = crit.weight
        if math.isnan(w) or math.isinf(w):
            raise ValueError(f"Weight for criterion '{crit.id}' cannot be NaN or Infinite.")
        if w < 0.0:
            raise ValueError(f"Weight for criterion '{crit.id}' cannot be negative ({w}).")
        if crit.weight_source == WeightSource.USER_DECLARED:
            has_user_weights = True
        weights[crit.id] = float(w)

    total_w = sum(weights.values())

    if total_w <= 1e-12:
        # All zero or unweighted -> Equal default
        equal_w = round(1.0 / len(criteria), 6)
        normalized = {crit.id: equal_w for crit in criteria}
        # Correct rounding remainder to ensure exact 1.0 sum
        rem = 1.0 - sum(normalized.values())
        normalized[criteria[0].id] = round(normalized[criteria[0].id] + rem, 6)
        note = "Decision ranking assumes equal criterion importance (no prior preferences declared)."
        return normalized, note

    # Normalize proportional to total weight
    normalized = {cid: round(w / total_w, 6) for cid, w in weights.items()}
    rem = round(1.0 - sum(normalized.values()), 6)
    if abs(rem) > 0:
        first_k = next(iter(normalized))
        normalized[first_k] = round(normalized[first_k] + rem, 6)

    if has_user_weights:
        note = "Criterion weights derived from user-declared preferences."
    else:
        note = "Criterion weights normalized from policy and rule-pack defaults."

    return normalized, note
