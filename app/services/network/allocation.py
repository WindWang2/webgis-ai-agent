"""
Network Location-Allocation Service Component.
Implements P-Median and Max Coverage facility location allocation models.
"""
from __future__ import annotations
import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Facility,
    DemandPoint,
    NetworkAnalysisResult,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.od_matrix import NetworkODMatrixService

logger = logging.getLogger(__name__)

# GIS-11: exact enumeration of C(m, p) combinations is only tractable for small
# inputs. C(50,5) ≈ 2.1M and C(100,10) ≈ 1.7e13 — the old code materialized the
# full list and hung indefinitely on realistic inputs. Above this threshold the
# solver switches to the classic polynomial heuristics (Teitz-Bart vertex
# substitution for p-median, greedy add for max-coverage). The threshold is
# generous enough to keep exact answers for small problems while bounding the
# worst case: 20000 combinations × O(n·p) evaluation is fast.
_MAX_EXACT_COMBINATIONS = 20000


def _exact_combination_count(m_fac: int, p_count: int) -> int:
    """Number of C(m, p) combinations, capped to avoid overflow."""
    if p_count > m_fac or p_count < 0:
        return 0
    # Use math.comb semantics without importing math: iterative product.
    p = min(p_count, m_fac - p_count)
    count = 1
    for i in range(1, p + 1):
        count = count * (m_fac - p + i) // i
        if count > _MAX_EXACT_COMBINATIONS * 2:
            return count  # early exit, we only need the threshold decision
    return count


class NetworkLocationAllocationService:
    """
    Service for selecting optimal facility locations using P-Median or Max Coverage models.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    # --- GIS-11: polynomial heuristics for large instances ---

    def _solve_p_median_heuristic(
        self, cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
    ) -> Tuple[int, ...]:
        """Teitz-Bart vertex substitution for p-median.

        Starts from the first p facilities, then repeatedly tries replacing one
        selected facility with one unselected candidate when it lowers the
        weighted sum of min-costs. O(passes × p × (m-p) × n); converges in a
        small number of passes on real road networks.
        """
        m_fac = len(cost_matrix[0]) if cost_matrix else 0
        if p_count <= 0 or m_fac == 0:
            return ()

        def evaluate(subset: Tuple[int, ...]) -> float:
            total = 0.0
            for i, w in enumerate(demand_weights):
                min_c = min(cost_matrix[i][j] for j in subset)
                total += (1e9 if min_c == float("inf") else min_c) * w
            return total

        best_subset = tuple(range(p_count))
        best_cost = evaluate(best_subset)

        improved = True
        passes = 0
        while improved and passes < 10:
            improved = False
            passes += 1
            for out_idx in range(p_count):
                for cand in range(m_fac):
                    if cand in best_subset:
                        continue
                    trial = list(best_subset)
                    trial[out_idx] = cand
                    trial_cost = evaluate(tuple(trial))
                    if trial_cost < best_cost - 1e-12:
                        best_subset = tuple(trial)
                        best_cost = trial_cost
                        improved = True
        return best_subset

    def _solve_max_coverage_heuristic(
        self,
        cost_matrix: List[List[float]],
        demand_weights: List[float],
        p_count: int,
        cutoff: float,
    ) -> Tuple[int, ...]:
        """Greedy-add for max coverage: at each step pick the facility that
        covers the most currently-uncovered demand weight."""
        m_fac = len(cost_matrix[0]) if cost_matrix else 0
        if p_count <= 0 or m_fac == 0:
            return ()

        n_dem = len(demand_weights)
        covered = [False] * n_dem
        selected: List[int] = []

        for _ in range(p_count):
            best_j = -1
            best_gain = -1.0
            for j in range(m_fac):
                if j in selected:
                    continue
                gain = 0.0
                for i in range(n_dem):
                    if not covered[i] and cost_matrix[i][j] <= cutoff:
                        gain += demand_weights[i]
                if gain > best_gain:
                    best_gain = gain
                    best_j = j
            if best_j < 0:
                break
            selected.append(best_j)
            for i in range(n_dem):
                if not covered[i] and cost_matrix[i][best_j] <= cutoff:
                    covered[i] = True

        return tuple(selected)

    def location_allocation(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        problem_type: str = "p_median",
        cutoff_cost: Optional[float] = None,
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
    ) -> NetworkAnalysisResult:
        """
        Solves Location-Allocation problem (P-Median or Max Coverage).

        Args:
            candidate_facilities: List of candidate Facility objects.
            demand_points: List of DemandPoint objects.
            p_count: Number of facilities to select.
            problem_type: 'p_median' or 'max_coverage'.
            cutoff_cost: Optional cost cutoff threshold.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            profile: TravelProfile.

        Returns:
            NetworkAnalysisResult containing allocated facilities and assignments.
        """
        if not candidate_facilities or not demand_points or p_count <= 0:
            return NetworkAnalysisResult(
                analysis_type="location_allocation",
                status="success",
                summary={"problem_type": problem_type, "p_count": p_count, "selected_count": 0},
            )

        p_count = min(p_count, len(candidate_facilities))

        orig_coords = [(d.geometry["coordinates"][0], d.geometry["coordinates"][1]) for d in demand_points]
        dest_coords = [(f.geometry["coordinates"][0], f.geometry["coordinates"][1]) for f in candidate_facilities]

        od_pairs = self.od_service.network_od_matrix(
            origins=orig_coords,
            destinations=dest_coords,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
        )

        n_dem = len(demand_points)
        m_fac = len(candidate_facilities)

        # Build Cost Matrix cost_matrix[i][j]
        cost_matrix: List[List[float]] = [[float("inf")] * m_fac for _ in range(n_dem)]
        for i in range(n_dem):
            for j in range(m_fac):
                idx = i * m_fac + j
                if idx < len(od_pairs) and od_pairs[idx].reachable:
                    cost_matrix[i][j] = od_pairs[idx].travel_time_s

        # GIS-11: exact enumeration for tractable instances; polynomial
        # heuristics (Teitz-Bart / greedy-add) beyond that so real inputs
        # (e.g. choose 5 of 80 candidates) terminate instead of hanging.
        n_combos = _exact_combination_count(m_fac, p_count)
        use_exact = n_combos <= _MAX_EXACT_COMBINATIONS
        solver_used = "exact" if use_exact else "heuristic"

        demand_weights = [d.weight for d in demand_points]

        if problem_type.lower() == "max_coverage":
            cutoff = cutoff_cost if cutoff_cost is not None else 900.0  # default 15 min
            if use_exact:
                best_coverage = -1.0
                best_subset = tuple(range(p_count))
                for combo in itertools.combinations(range(m_fac), p_count):
                    coverage = 0.0
                    for i in range(n_dem):
                        min_c = min(cost_matrix[i][j] for j in combo)
                        if min_c <= cutoff:
                            coverage += demand_weights[i]
                    if coverage > best_coverage:
                        best_coverage = coverage
                        best_subset = combo
            else:
                logger.info(
                    "location_allocation max_coverage: C(%d,%d)=%d combinations exceed exact "
                    "limit (%d); using greedy-add heuristic",
                    m_fac, p_count, n_combos, _MAX_EXACT_COMBINATIONS,
                )
                best_subset = self._solve_max_coverage_heuristic(
                    cost_matrix, demand_weights, p_count, cutoff
                )
        else:
            # P-Median: minimize sum_i w_i * min_{j in S} C_{i,j}
            if use_exact:
                best_impedance = float("inf")
                best_subset = tuple(range(p_count))
                for combo in itertools.combinations(range(m_fac), p_count):
                    total_w_cost = 0.0
                    for i in range(n_dem):
                        min_c = min(cost_matrix[i][j] for j in combo)
                        if min_c == float("inf"):
                            total_w_cost += 1e9 * demand_weights[i]
                        else:
                            total_w_cost += min_c * demand_weights[i]
                    if total_w_cost < best_impedance:
                        best_impedance = total_w_cost
                        best_subset = combo
            else:
                logger.info(
                    "location_allocation p_median: C(%d,%d)=%d combinations exceed exact "
                    "limit (%d); using Teitz-Bart heuristic",
                    m_fac, p_count, n_combos, _MAX_EXACT_COMBINATIONS,
                )
                best_subset = self._solve_p_median_heuristic(
                    cost_matrix, demand_weights, p_count
                )

        # Generate allocation results; unreachable demand points are
        # collected as unassigned rather than silently assigned to the first
        # facility (inf cost would otherwise make min() pick index 0).
        unassigned_ids: List[str] = []
        allocated_facilities: List[Dict[str, Any]] = []
        for fac_idx in best_subset:
            fac = candidate_facilities[fac_idx]
            assigned_demands: List[str] = []
            total_assigned_weight = 0.0

            for i in range(n_dem):
                best_cost = min(cost_matrix[i][j] for j in best_subset)
                if best_cost == float("inf"):
                    if fac_idx == best_subset[0]:
                        # Only count unassigned once (first facility bucket)
                        pass
                    continue
                best_fac_idx = min(best_subset, key=lambda j: cost_matrix[i][j])
                if best_fac_idx == fac_idx:
                    assigned_demands.append(demand_points[i].demand_id)
                    total_assigned_weight += demand_points[i].weight

            allocated_facilities.append({
                "facility_id": fac.facility_id,
                "name": fac.name,
                "geometry": fac.geometry,
                "assigned_demand_count": len(assigned_demands),
                "assigned_total_weight": total_assigned_weight,
                "assigned_demand_ids": assigned_demands,
            })

        for i in range(n_dem):
            best_cost = min(cost_matrix[i][j] for j in best_subset) if best_subset else float("inf")
            if best_cost == float("inf"):
                unassigned_ids.append(demand_points[i].demand_id)

        summary = {
            "problem_type": problem_type,
            "p_count": p_count,
            "selected_facilities_count": len(best_subset),
            "total_demand_count": n_dem,
            "candidate_facility_count": m_fac,
            # GIS-11: "exact" (enumerated C(m,p)) or "heuristic" (Teitz-Bart /
            # greedy-add). Heuristic results are near-optimal, not guaranteed
            # optimal — explicit so consumers can weigh the trade-off.
            "solver": solver_used,
            "unassigned_count": len(unassigned_ids),
            "unassigned_ids": unassigned_ids,
        }

        return NetworkAnalysisResult(
            analysis_type="location_allocation",
            status="success",
            summary=summary,
            allocated_facilities=allocated_facilities,
        )
