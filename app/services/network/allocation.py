"""
Network Location-Allocation Service Component.
Implements P-Median and Max Coverage facility location allocation models.
"""
from __future__ import annotations
import itertools
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


class NetworkLocationAllocationService:
    """
    Service for selecting optimal facility locations using P-Median or Max Coverage models.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

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

        best_subset: Tuple[int, ...] = tuple(range(p_count))

        if problem_type.lower() == "max_coverage":
            cutoff = cutoff_cost if cutoff_cost is not None else 900.0  # default 15 min
            best_coverage = -1.0

            # Evaluate combinations
            all_combos = list(itertools.combinations(range(m_fac), p_count))
            for combo in all_combos:
                coverage = 0.0
                for i in range(n_dem):
                    min_c = min(cost_matrix[i][j] for j in combo)
                    if min_c <= cutoff:
                        coverage += demand_points[i].weight
                if coverage > best_coverage:
                    best_coverage = coverage
                    best_subset = combo
        else:
            # P-Median: minimize sum_i w_i * min_{j in S} C_{i,j}
            best_impedance = float("inf")
            all_combos = list(itertools.combinations(range(m_fac), p_count))
            for combo in all_combos:
                total_w_cost = 0.0
                for i in range(n_dem):
                    min_c = min(cost_matrix[i][j] for j in combo)
                    if min_c == float("inf"):
                        total_w_cost += 1e9 * demand_points[i].weight
                    else:
                        total_w_cost += min_c * demand_points[i].weight
                if total_w_cost < best_impedance:
                    best_impedance = total_w_cost
                    best_subset = combo

        # Generate allocation results
        allocated_facilities: List[Dict[str, Any]] = []
        for fac_idx in best_subset:
            fac = candidate_facilities[fac_idx]
            assigned_demands: List[str] = []
            total_assigned_weight = 0.0

            for i in range(n_dem):
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

        summary = {
            "problem_type": problem_type,
            "p_count": p_count,
            "selected_facilities_count": len(best_subset),
            "total_demand_count": n_dem,
            "candidate_facility_count": m_fac,
        }

        return NetworkAnalysisResult(
            analysis_type="location_allocation",
            status="success",
            summary=summary,
            allocated_facilities=allocated_facilities,
        )
