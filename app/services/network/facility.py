"""
Network Closest Facility Service Component.
Finds nearest facilities for demand incidents based on network travel cost.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Barrier,
    Facility,
    DemandPoint,
    Route,
    NetworkAnalysisResult,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService
from app.services.network.od_matrix import NetworkODMatrixService


class NetworkClosestFacilityService:
    """
    Service for finding closest facilities to demand locations/incidents using network travel cost.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    def network_closest_facility(
        self,
        demand_points: List[Union[DemandPoint, Tuple[float, float], Dict[str, Any]]],
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        cutoff_cost: Optional[float] = None,
        target_facility_count: int = 1,
        travel_direction: str = "incident_to_facility",
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> NetworkAnalysisResult:
        """
        Finds the closest facilities for each demand point.

        Args:
            demand_points: List of DemandPoint objects or (lng, lat) tuples.
            facilities: List of Facility objects or (lng, lat) tuples.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            cutoff_cost: Optional maximum cost threshold.
            target_facility_count: Number of closest facilities to find per demand point.
            travel_direction: 'incident_to_facility' or 'facility_to_incident'.
            profile: TravelProfile.
            impedance: Impedance model.
            barriers: Optional list of barriers.

        Returns:
            NetworkAnalysisResult containing routes and summary statistics.
        """
        normalized_demands = self._normalize_demands(demand_points)
        normalized_facilities = self._normalize_facilities(facilities)

        if not normalized_demands or not normalized_facilities:
            return NetworkAnalysisResult(
                analysis_type="closest_facility",
                status="success",
                summary={"demand_count": len(normalized_demands), "facility_count": len(normalized_facilities)},
            )

        if travel_direction == "facility_to_incident":
            origins, destinations = normalized_facilities, normalized_demands
        else:
            origins, destinations = normalized_demands, normalized_facilities

        # PERF: one multi-source Dijkstra over all origins (instead of D×F
        # independent shortest-path calls), reusing the predecessor trees to
        # rebuild only the selected routes. The old per-pair
        # ``network_shortest_path`` also ran a full ``graph.copy()`` for every
        # coordinate pair — with D demands × F facilities that dominated the
        # analysis.
        od = self.od_service.network_od_paths(
            origins=[o.geometry["coordinates"] for o in origins],
            destinations=[d.geometry["coordinates"] for d in destinations],
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
            impedance=impedance,
            barriers=barriers,
        )
        origin_labels = od["origin_labels"]
        dest_labels = od["dest_labels"]
        profile_name = profile.name if profile else "driving"

        routes: List[Route] = []
        matched_pairs_count = 0

        for dem_idx, dem in enumerate(normalized_demands):
            for fac_idx, fac in enumerate(normalized_facilities):
                if travel_direction == "facility_to_incident":
                    o_label, d_label = origin_labels[fac_idx], dest_labels[dem_idx]
                    o_id, d_id = fac.facility_id, dem.demand_id
                else:
                    o_label, d_label = origin_labels[dem_idx], dest_labels[fac_idx]
                    o_id, d_id = dem.demand_id, fac.facility_id

                info = od["pairs"].get((o_label, d_label))
                # Issue #456: a demand point located exactly AT a facility has
                # distance_m == 0 — a perfectly valid (zero-cost) match. The
                # old `distance_m <= 0` guard dropped those pairs entirely;
                # filter only non-finite sentinel values (unreachable legs).
                if (
                    info is None or not info["reachable"]
                    or not math.isfinite(info["cost"])
                    or not math.isfinite(info["distance_m"])
                ):
                    continue
                if cutoff_cost is not None and info["cost"] > cutoff_cost:
                    continue

                route = self.router.build_route_from_path(
                    od["graph_view"], info["path"],
                    origin_label=o_label, destination_label=d_label,
                    profile_name=profile_name,
                    route_id=f"route_{o_label}_{d_label}",
                    weight_func=od["weight_func"],
                )
                route.origin_id = o_id
                route.destination_id = d_id
                routes.append((route, fac))

        # Group by demand: closest facility selection happens per demand point.
        per_demand: Dict[str, List[Tuple[Route, Facility]]] = {}
        for route, fac in routes:
            key = route.origin_id if travel_direction == "incident_to_facility" else route.destination_id
            per_demand.setdefault(key, []).append((route, fac))

        selected_routes: List[Route] = []
        for key in sorted(per_demand):
            candidates = sorted(per_demand[key], key=lambda x: x[0].total_cost)
            for r, _ in candidates[:target_facility_count]:
                selected_routes.append(r)
                matched_pairs_count += 1

        summary = {
            "demand_count": len(normalized_demands),
            "facility_count": len(normalized_facilities),
            "routes_found": len(selected_routes),
            "target_facility_count": target_facility_count,
            "cutoff_cost": cutoff_cost,
        }

        return NetworkAnalysisResult(
            analysis_type="closest_facility",
            status="success",
            summary=summary,
            routes=selected_routes,
        )

    def _normalize_demands(
        self,
        demand_points: List[Union[DemandPoint, Tuple[float, float], Dict[str, Any]]],
    ) -> List[DemandPoint]:
        result: List[DemandPoint] = []
        for i, item in enumerate(demand_points):
            if isinstance(item, DemandPoint):
                result.append(item)
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                result.append(
                    DemandPoint(
                        demand_id=f"d_{i}",
                        weight=1.0,
                        geometry={"type": "Point", "coordinates": [float(item[0]), float(item[1])]},
                    )
                )
            elif isinstance(item, dict):
                d_id = item.get("demand_id", item.get("id", f"d_{i}"))
                weight = item.get("weight", 1.0)
                geom = item.get("geometry", {"type": "Point", "coordinates": [0.0, 0.0]})
                result.append(DemandPoint(demand_id=str(d_id), weight=float(weight), geometry=geom))
        return result

    def _normalize_facilities(
        self,
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
    ) -> List[Facility]:
        result: List[Facility] = []
        for i, item in enumerate(facilities):
            if isinstance(item, Facility):
                result.append(item)
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                result.append(
                    Facility(
                        facility_id=f"f_{i}",
                        name=f"Facility {i+1}",
                        geometry={"type": "Point", "coordinates": [float(item[0]), float(item[1])]},
                    )
                )
            elif isinstance(item, dict):
                f_id = item.get("facility_id", item.get("id", f"f_{i}"))
                name = item.get("name", f"Facility {i+1}")
                geom = item.get("geometry", {"type": "Point", "coordinates": [0.0, 0.0]})
                cap = item.get("capacity", 1.0)
                result.append(Facility(facility_id=str(f_id), name=name, geometry=geom, capacity=float(cap)))
        return result
