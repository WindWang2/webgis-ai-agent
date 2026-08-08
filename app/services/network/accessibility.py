"""
Network Accessibility Service Component.
Implements spatial accessibility metrics including 15-minute life circle coverage analysis,
served/unserved population calculation, and 2SFCA (Two-Step Floating Catchment Area).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Facility,
    DemandPoint,
    AccessibilityResult,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService
from app.services.network.od_matrix import NetworkODMatrixService


class NetworkAccessibilityService:
    """
    Service for calculating spatial accessibility, 15-minute life circle metrics, and 2SFCA scores.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    def network_accessibility(
        self,
        demand_points: List[DemandPoint],
        facilities: List[Facility],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        cutoff_minutes: float = 15.0,
        method: str = "15min_circle",
        profile: Optional[TravelProfile] = None,
    ) -> AccessibilityResult:
        """
        Calculates network accessibility metrics for demand points and facilities.

        Args:
            demand_points: List of DemandPoint objects.
            facilities: List of Facility objects.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            cutoff_minutes: Time threshold in minutes (e.g. 15.0).
            method: '15min_circle' or '2sfca'.
            profile: TravelProfile.

        Returns:
            AccessibilityResult object.
        """
        if not demand_points or not facilities:
            return AccessibilityResult(
                analysis_id="acc_empty",
                mode=profile.name if profile else "driving",
                cutoff_minutes=cutoff_minutes,
                total_demand=0.0,
                served_demand=0.0,
                unserved_demand=0.0,
                coverage_percentage=0.0,
                average_travel_time_min=0.0,
            )

        # Build OD matrix
        orig_coords = [(d.geometry["coordinates"][0], d.geometry["coordinates"][1]) for d in demand_points]
        dest_coords = [(f.geometry["coordinates"][0], f.geometry["coordinates"][1]) for f in facilities]

        od_pairs = self.od_service.network_od_matrix(
            origins=orig_coords,
            destinations=dest_coords,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
        )

        # Map travel times by (demand_idx, facility_idx)
        time_matrix: Dict[Tuple[int, int], float] = {}
        for idx_d, d in enumerate(demand_points):
            for idx_f, f in enumerate(facilities):
                pair_idx = idx_d * len(facilities) + idx_f
                if pair_idx < len(od_pairs) and od_pairs[pair_idx].reachable:
                    # Convert travel_time_s to minutes
                    time_min = od_pairs[pair_idx].travel_time_s / 60.0
                    time_matrix[(idx_d, idx_f)] = time_min
                else:
                    time_matrix[(idx_d, idx_f)] = float("inf")

        total_demand = sum(d.weight for d in demand_points)
        served_demand = 0.0
        unserved_demand = 0.0
        weighted_travel_time_sum = 0.0

        per_zone_metrics: List[Dict[str, Any]] = []

        if method.lower() == "2sfca":
            # Step 1: Facility ratios R_j = Capacity_j / Sum(Demand_k in cutoff)
            facility_ratios: Dict[int, float] = {}
            for idx_f, fac in enumerate(facilities):
                catchment_demand = 0.0
                for idx_d, dem in enumerate(demand_points):
                    if time_matrix.get((idx_d, idx_f), float("inf")) <= cutoff_minutes:
                        catchment_demand += dem.weight
                cap = getattr(fac, "capacity", 1.0)
                facility_ratios[idx_f] = cap / catchment_demand if catchment_demand > 0 else 0.0

            # Step 2: Demand accessibility A_i = Sum(R_j for j in cutoff)
            for idx_d, dem in enumerate(demand_points):
                score = 0.0
                min_t = float("inf")
                for idx_f in range(len(facilities)):
                    t = time_matrix.get((idx_d, idx_f), float("inf"))
                    if t <= cutoff_minutes:
                        score += facility_ratios[idx_f]
                        min_t = min(min_t, t)

                if score > 0:
                    served_demand += dem.weight
                    weighted_travel_time_sum += min_t * dem.weight
                else:
                    unserved_demand += dem.weight

                per_zone_metrics.append({
                    "demand_id": dem.demand_id,
                    "weight": dem.weight,
                    "accessibility_score": round(score, 4),
                    "min_travel_time_min": round(min_t, 2) if min_t < float("inf") else None,
                    "is_served": score > 0,
                })

        else:
            # Standard 15-minute life circle accessibility
            for idx_d, dem in enumerate(demand_points):
                min_t = min(
                    (time_matrix.get((idx_d, idx_f), float("inf")) for idx_f in range(len(facilities))),
                    default=float("inf"),
                )

                is_served = min_t <= cutoff_minutes
                if is_served:
                    served_demand += dem.weight
                    weighted_travel_time_sum += min_t * dem.weight
                else:
                    unserved_demand += dem.weight

                per_zone_metrics.append({
                    "demand_id": dem.demand_id,
                    "weight": dem.weight,
                    "min_travel_time_min": round(min_t, 2) if min_t < float("inf") else None,
                    "is_served": is_served,
                })

        coverage_percentage = (served_demand / total_demand * 100.0) if total_demand > 0 else 100.0
        avg_travel_time = (weighted_travel_time_sum / served_demand) if served_demand > 0 else 0.0

        return AccessibilityResult(
            analysis_id=f"acc_{method}_{int(cutoff_minutes)}m",
            mode=profile.name if profile else "driving",
            cutoff_minutes=cutoff_minutes,
            total_demand=round(total_demand, 2),
            served_demand=round(served_demand, 2),
            unserved_demand=round(unserved_demand, 2),
            coverage_percentage=round(coverage_percentage, 2),
            average_travel_time_min=round(avg_travel_time, 2),
            per_zone_metrics=per_zone_metrics,
        )
