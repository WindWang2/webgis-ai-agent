"""
Network Closest Facility Service Component.
Finds nearest facilities for demand incidents based on network travel cost.
"""
from __future__ import annotations
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

        routes: List[Route] = []
        matched_pairs_count = 0

        for dem in normalized_demands:
            dem_coords = (dem.geometry["coordinates"][0], dem.geometry["coordinates"][1])

            # Calculate route to all facilities
            candidate_routes: List[Tuple[Route, Facility]] = []

            for fac in normalized_facilities:
                fac_coords = (fac.geometry["coordinates"][0], fac.geometry["coordinates"][1])

                if travel_direction == "facility_to_incident":
                    origin_loc, dest_loc = fac_coords, dem_coords
                    o_id, d_id = fac.facility_id, dem.demand_id
                else:
                    origin_loc, dest_loc = dem_coords, fac_coords
                    o_id, d_id = dem.demand_id, fac.facility_id

                route = self.router.network_shortest_path(
                    graph=graph,
                    network_dataset=network_dataset,
                    origin=origin_loc,
                    destination=dest_loc,
                    profile=profile,
                    impedance=impedance,
                    barriers=barriers,
                )

                if route.total_cost < float("inf") and route.total_distance_m > 0:
                    route.origin_id = o_id
                    route.destination_id = d_id
                    if cutoff_cost is None or route.total_cost <= cutoff_cost:
                        candidate_routes.append((route, fac))

            # Sort by total_cost ascending
            candidate_routes.sort(key=lambda x: x[0].total_cost)
            selected = candidate_routes[:target_facility_count]

            for r, _ in selected:
                routes.append(r)
                matched_pairs_count += 1

        summary = {
            "demand_count": len(normalized_demands),
            "facility_count": len(normalized_facilities),
            "routes_found": len(routes),
            "target_facility_count": target_facility_count,
            "cutoff_cost": cutoff_cost,
        }

        return NetworkAnalysisResult(
            analysis_type="closest_facility",
            status="success",
            summary=summary,
            routes=routes,
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
