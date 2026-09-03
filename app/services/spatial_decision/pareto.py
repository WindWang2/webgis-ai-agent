"""
Generalized Multi-Objective Pareto Frontier Engine for Spatial Decision Intelligence V3.
Evaluates strict non-dominance across multiple criteria without arbitrary hidden bonuses.
Integrates feasibility awareness: infeasible alternatives are excluded from the Pareto set.
"""
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.services.spatial_decision.models_v3 import Criterion, CriterionDirection, ParetoStatus

logger = logging.getLogger(__name__)


class ParetoAnalysis(BaseModel):
    """Full Pareto dominance analysis output."""
    frontier: List[str] = Field(default_factory=list, description="IDs of non-dominated feasible alternatives")
    dominated: List[str] = Field(default_factory=list, description="IDs of dominated alternatives")
    infeasible: List[str] = Field(default_factory=list, description="IDs of infeasible alternatives")
    dominates_map: Dict[str, List[str]] = Field(
        default_factory=dict, description="Alternative ID -> list of IDs it dominates"
    )
    dominated_by_map: Dict[str, List[str]] = Field(
        default_factory=dict, description="Alternative ID -> list of IDs that dominate it"
    )


def compute_pareto_frontier(
    normalized_matrix: Dict[str, Dict[str, float]],
    feasible_mask: Optional[Dict[str, bool]] = None,
    tolerance: float = 1e-6,
) -> ParetoAnalysis:
    """
    Computes the Pareto non-dominated frontier over normalized criterion values (where higher is better).

    Args:
        normalized_matrix: Mapping of criterion_id -> {alternative_id: normalized_val in [0.0, 1.0]}.
        feasible_mask: Optional mapping of alternative_id -> is_feasible.
        tolerance: Numerical tolerance for floating-point comparisons.

    Returns:
        ParetoAnalysis object with frontier, dominated set, and dominance mappings.
    """
    feasible_mask = feasible_mask or {}

    # Collect all alternative IDs
    all_alts = sorted(list({alt_id for c_dict in normalized_matrix.values() for alt_id in c_dict.keys()}))
    crit_ids = sorted(list(normalized_matrix.keys()))

    if not all_alts or not crit_ids:
        return ParetoAnalysis()

    # Separate feasible vs infeasible
    feasible_alts = [a for a in all_alts if feasible_mask.get(a, True)]
    infeasible_alts = [a for a in all_alts if not feasible_mask.get(a, True)]

    dominates_map: Dict[str, List[str]] = {a: [] for a in all_alts}
    dominated_by_map: Dict[str, List[str]] = {a: [] for a in all_alts}
    dominated_set: Set[str] = set()

    # Pairwise comparison strictly over feasible alternatives
    for i, a_id in enumerate(feasible_alts):
        for b_id in feasible_alts[i + 1:]:
            # Check if a dominates b or b dominates a
            a_better_in_any = False
            a_worse_in_any = False
            b_better_in_any = False
            b_worse_in_any = False

            for cid in crit_ids:
                val_a = normalized_matrix.get(cid, {}).get(a_id, 0.0)
                val_b = normalized_matrix.get(cid, {}).get(b_id, 0.0)

                diff = val_a - val_b
                if diff > tolerance:
                    a_better_in_any = True
                    b_worse_in_any = True
                elif diff < -tolerance:
                    b_better_in_any = True
                    a_worse_in_any = True

            # a dominates b
            if a_better_in_any and not a_worse_in_any:
                dominates_map[a_id].append(b_id)
                dominated_by_map[b_id].append(a_id)
                dominated_set.add(b_id)

            # b dominates a
            elif b_better_in_any and not b_worse_in_any:
                dominates_map[b_id].append(a_id)
                dominated_by_map[a_id].append(b_id)
                dominated_set.add(a_id)

    frontier = [a for a in feasible_alts if a not in dominated_set]
    dominated = [a for a in feasible_alts if a in dominated_set]

    return ParetoAnalysis(
        frontier=frontier,
        dominated=dominated,
        infeasible=infeasible_alts,
        dominates_map=dominates_map,
        dominated_by_map=dominated_by_map,
    )
