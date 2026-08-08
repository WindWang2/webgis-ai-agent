"""
Network Graph Engine Component.
Unified orchestrator facade exposing clean entry points for graph building, snapping, routing,
OD matrix calculation, closest facility search, service areas, accessibility, location-allocation,
and VRP route optimization.
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
    PointSnappingResult,
    Route,
    ODPair,
    ServiceArea,
    AccessibilityResult,
    NetworkAnalysisResult,
)
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService
from app.services.network.od_matrix import NetworkODMatrixService
from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.service_area import NetworkServiceAreaService
from app.services.network.accessibility import NetworkAccessibilityService
from app.services.network.allocation import NetworkLocationAllocationService
from app.services.network.vrp import NetworkRouteOptimizationService


class NetworkGraphEngine:
    """
    Unified Orchestrator Seam for Network Analyst V2.
    Integrates all network services into a single clean API.
    """

    def __init__(
        self,
        builder: Optional[NetworkGraphBuilder] = None,
        snapper: Optional[PointSnappingService] = None,
    ):
        self.builder = builder or NetworkGraphBuilder()
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)
        self.od_service = NetworkODMatrixService(snapper=self.snapper)
        self.fac_service = NetworkClosestFacilityService(snapper=self.snapper)
        self.sa_service = NetworkServiceAreaService(snapper=self.snapper)
        self.acc_service = NetworkAccessibilityService(snapper=self.snapper)
        self.alloc_service = NetworkLocationAllocationService(snapper=self.snapper)
        self.vrp_service = NetworkRouteOptimizationService(snapper=self.snapper)

    def build_network(
        self,
        data: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        profile: Optional[TravelProfile] = None,
        snap_tolerance: float = 1e-5,
        split_intersections: bool = True,
        use_cache: bool = True,
    ) -> Tuple[nx.DiGraph, NetworkDataset]:
        """Builds or fetches cached network graph and dataset."""
        return self.builder.build_graph(
            data=data,
            profile=profile,
            snap_tolerance=snap_tolerance,
            split_intersections=split_intersections,
            use_cache=use_cache,
        )

    def snap_point(
        self,
        point: Tuple[float, float],
        network_dataset: NetworkDataset,
        max_tolerance_m: float = 500.0,
    ) -> PointSnappingResult:
        """Snaps a point (lng, lat) to nearest network dataset edge."""
        return self.snapper.snap_point(point, network_dataset, max_tolerance_m)

    def shortest_path(
        self,
        origin: Union[Tuple[float, float], str, PointSnappingResult],
        destination: Union[Tuple[float, float], str, PointSnappingResult],
        network_dataset: NetworkDataset,
        graph: Optional[nx.DiGraph] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
        algorithm: str = "dijkstra",
    ) -> Route:
        """Calculates shortest path route between origin and destination."""
        if graph is None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.router.network_shortest_path(
            graph=graph,
            network_dataset=network_dataset,
            origin=origin,
            destination=destination,
            profile=profile,
            impedance=impedance,
            barriers=barriers,
            algorithm=algorithm,
        )

    def od_matrix(
        self,
        origins: List[Union[Tuple[float, float], str, PointSnappingResult]],
        destinations: List[Union[Tuple[float, float], str, PointSnappingResult]],
        network_dataset: NetworkDataset,
        graph: Optional[nx.DiGraph] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> List[ODPair]:
        """Calculates batch N x M origin-destination cost matrix."""
        if graph is None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.od_service.network_od_matrix(
            origins=origins,
            destinations=destinations,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
            impedance=impedance,
            barriers=barriers,
        )

    def closest_facility(
        self,
        demand_points: List[Union[DemandPoint, Tuple[float, float], Dict[str, Any]]],
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
        network_dataset: NetworkDataset,
        graph: Optional[nx.DiGraph] = None,
        cutoff_cost: Optional[float] = None,
        target_facility_count: int = 1,
        travel_direction: str = "incident_to_facility",
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> NetworkAnalysisResult:
        """Finds closest facilities for demand points."""
        if graph is None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.fac_service.network_closest_facility(
            demand_points=demand_points,
            facilities=facilities,
            graph=graph,
            network_dataset=network_dataset,
            cutoff_cost=cutoff_cost,
            target_facility_count=target_facility_count,
            travel_direction=travel_direction,
            profile=profile,
            impedance=impedance,
            barriers=barriers,
        )

    def service_area(
        self,
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
        breaks: List[float],
        break_unit: str = "minutes",
        network_dataset: Optional[NetworkDataset] = None,
        graph: Optional[nx.DiGraph] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> List[ServiceArea]:
        """Calculates service areas and isochrone polygons."""
        if graph is None and network_dataset is not None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.sa_service.network_service_area(
            facilities=facilities,
            breaks=breaks,
            break_unit=break_unit,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
            impedance=impedance,
            barriers=barriers,
        )

    def accessibility(
        self,
        demand_points: List[DemandPoint],
        facilities: List[Facility],
        network_dataset: Optional[NetworkDataset] = None,
        graph: Optional[nx.DiGraph] = None,
        cutoff_minutes: float = 15.0,
        method: str = "15min_circle",
        profile: Optional[TravelProfile] = None,
    ) -> AccessibilityResult:
        """Calculates 15-minute life circle accessibility or 2SFCA."""
        if graph is None and network_dataset is not None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.acc_service.network_accessibility(
            demand_points=demand_points,
            facilities=facilities,
            graph=graph,
            network_dataset=network_dataset,
            cutoff_minutes=cutoff_minutes,
            method=method,
            profile=profile,
        )

    def location_allocation(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        problem_type: str = "p_median",
        cutoff_cost: Optional[float] = None,
        network_dataset: Optional[NetworkDataset] = None,
        graph: Optional[nx.DiGraph] = None,
        profile: Optional[TravelProfile] = None,
    ) -> NetworkAnalysisResult:
        """Performs P-Median or Max Coverage location-allocation optimization."""
        if graph is None and network_dataset is not None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.alloc_service.location_allocation(
            candidate_facilities=candidate_facilities,
            demand_points=demand_points,
            p_count=p_count,
            problem_type=problem_type,
            cutoff_cost=cutoff_cost,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
        )

    def optimize_route(
        self,
        stops: List[Union[Tuple[float, float], DemandPoint, Dict[str, Any]]],
        depot: Optional[Union[Tuple[float, float], Facility, Dict[str, Any]]] = None,
        end_at_depot: bool = True,
        network_dataset: Optional[NetworkDataset] = None,
        graph: Optional[nx.DiGraph] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
    ) -> Route:
        """Performs TSP / 2-opt VRP multi-stop route optimization."""
        if graph is None and network_dataset is not None:
            graph, _ = self.builder.build_graph(network_dataset, profile=profile)
        return self.vrp_service.optimize_route(
            stops=stops,
            depot=depot,
            end_at_depot=end_at_depot,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
            impedance=impedance,
        )

    # --- High-level Async Tool/Harness Seam Interfaces ---

    async def solve_shortest_path(
        self,
        network: Any,
        origin: Any,
        destination: Any,
        profile: Optional[TravelProfile] = None,
        barriers: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level shortest path solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        # Parse origin & destination
        orig_pt = origin if isinstance(origin, (list, tuple)) else origin.get("coordinates", [0, 0]) if isinstance(origin, dict) else [0, 0]
        dest_pt = destination if isinstance(destination, (list, tuple)) else destination.get("coordinates", [0, 0]) if isinstance(destination, dict) else [0, 0]

        # Process barriers
        barrier_objs = []
        if barriers:
            for idx, b in enumerate(barriers):
                geom = b if isinstance(b, dict) and "type" in b else {"type": "Point", "coordinates": b}
                barrier_objs.append(Barrier(barrier_id=f"b_{idx}", geometry=geom))

        route = self.shortest_path(
            origin=tuple(orig_pt),
            destination=tuple(dest_pt),
            network_dataset=net_ds,
            graph=graph,
            profile=profile,
            barriers=barrier_objs,
        )

        return NetworkAnalysisResult(
            analysis_type="shortest_path",
            status="success",
            routes=[route],
            result_geojson={
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"distance_m": route.total_distance_m, "time_s": route.total_time_s}, "geometry": route.geometry}]
            }
        )

    async def solve_od_matrix(
        self,
        network: Any,
        origins: List[Any],
        destinations: List[Any],
        profile: Optional[TravelProfile] = None,
        cutoff_s: Optional[float] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level OD matrix solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        orig_pts = [p if isinstance(p, (list, tuple)) else p.get("coordinates", [0, 0]) for p in origins]
        dest_pts = [p if isinstance(p, (list, tuple)) else p.get("coordinates", [0, 0]) for p in destinations]

        pairs = self.od_matrix(
            origins=[tuple(p) for p in orig_pts],
            destinations=[tuple(p) for p in dest_pts],
            network_dataset=net_ds,
            graph=graph,
            profile=profile,
        )

        return NetworkAnalysisResult(
            analysis_type="od_matrix",
            status="success",
            od_matrix=pairs,
        )

    async def solve_closest_facility(
        self,
        network: Any,
        incidents: List[Any],
        facilities: List[Any],
        profile: Optional[TravelProfile] = None,
        number_to_find: int = 1,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level closest facility solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        inc_pts = [p if isinstance(p, (list, tuple)) else p.get("coordinates", [0, 0]) for p in incidents]

        fac_objs = []
        for idx, f in enumerate(facilities):
            if isinstance(f, dict) and "coordinates" in f:
                fac_id = str(f.get("id", f"fac_{idx}"))
                geom = {"type": "Point", "coordinates": f["coordinates"]}
            elif isinstance(f, (list, tuple)):
                fac_id = f"fac_{idx}"
                geom = {"type": "Point", "coordinates": list(f)}
            else:
                fac_id = str(f)
                geom = {"type": "Point", "coordinates": [0, 0]}
            fac_objs.append(Facility(facility_id=fac_id, geometry=geom))

        fac_res = self.closest_facility(
            demand_points=[tuple(p) for p in inc_pts],
            facilities=fac_objs,
            network_dataset=net_ds,
            graph=graph,
            target_facility_count=number_to_find,
            profile=profile,
        )
        return fac_res if isinstance(fac_res, NetworkAnalysisResult) else NetworkAnalysisResult(
            analysis_type="closest_facility",
            status="success",
            routes=fac_res if isinstance(fac_res, list) else [],
        )

    async def solve_service_area(
        self,
        network: Any,
        facilities: List[Any],
        breaks_minutes: Optional[List[float]] = None,
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level service area solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        breaks_minutes = breaks_minutes or [5.0, 10.0, 15.0]
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        fac_objs = []
        for idx, f in enumerate(facilities):
            if isinstance(f, dict) and "coordinates" in f:
                fac_id = str(f.get("id", f"fac_{idx}"))
                geom = {"type": "Point", "coordinates": f["coordinates"]}
            elif isinstance(f, (list, tuple)):
                fac_id = f"fac_{idx}"
                geom = {"type": "Point", "coordinates": list(f)}
            else:
                fac_id = str(f)
                geom = {"type": "Point", "coordinates": [0, 0]}
            fac_objs.append(Facility(facility_id=fac_id, geometry=geom))

        sa_breaks = self.service_area(
            facilities=fac_objs,
            breaks=breaks_minutes,
            network_dataset=net_ds,
            graph=graph,
            profile=profile,
        )

        breaks_list = []
        if isinstance(sa_breaks, list):
            for sa in sa_breaks:
                if hasattr(sa, "breaks"):
                    breaks_list.extend(sa.breaks)
                elif hasattr(sa, "break_value"):
                    breaks_list.append(sa)

        return NetworkAnalysisResult(
            analysis_type="service_area",
            status="success",
            service_area_breaks=breaks_list,
        )

    async def solve_accessibility(
        self,
        network: Any,
        demand_layer: Any,
        facilities: List[Any],
        cutoff_minutes: float = 15.0,
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level accessibility solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        demands = []
        if isinstance(demand_layer, list):
            for idx, d in enumerate(demand_layer):
                if isinstance(d, dict):
                    d_id = str(d.get("id", f"d_{idx}"))
                    weight = float(d.get("weight", 1.0))
                    coords = d.get("coordinates", [0, 0])
                    geom = {"type": "Point", "coordinates": coords}
                else:
                    d_id = f"d_{idx}"
                    weight = 1.0
                    geom = {"type": "Point", "coordinates": list(d)}
                demands.append(DemandPoint(demand_id=d_id, weight=weight, geometry=geom))

        fac_objs = []
        for idx, f in enumerate(facilities):
            if isinstance(f, dict) and "coordinates" in f:
                fac_id = str(f.get("id", f"fac_{idx}"))
                geom = {"type": "Point", "coordinates": f["coordinates"]}
            elif isinstance(f, (list, tuple)):
                fac_id = f"fac_{idx}"
                geom = {"type": "Point", "coordinates": list(f)}
            else:
                fac_id = str(f)
                geom = {"type": "Point", "coordinates": [0, 0]}
            fac_objs.append(Facility(facility_id=fac_id, geometry=geom))

        acc_res = self.accessibility(
            demand_points=demands,
            facilities=fac_objs,
            network_dataset=net_ds,
            graph=graph,
            cutoff_minutes=cutoff_minutes,
            profile=profile,
        )

        return NetworkAnalysisResult(
            analysis_type="accessibility",
            status="success",
            accessibility=acc_res,
        )

    async def solve_location_allocation(
        self,
        network: Any,
        candidate_facilities: List[Any],
        demand_points: List[Any],
        n_to_choose: int = 2,
        objective: str = "minimize_cost",
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level location-allocation solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        cand_objs = []
        for idx, c in enumerate(candidate_facilities):
            if isinstance(c, dict) and "coordinates" in c:
                c_id = str(c.get("id", f"cand_{idx}"))
                geom = {"type": "Point", "coordinates": c["coordinates"]}
            elif isinstance(c, (list, tuple)):
                c_id = f"cand_{idx}"
                geom = {"type": "Point", "coordinates": list(c)}
            else:
                c_id = str(c)
                geom = {"type": "Point", "coordinates": [0, 0]}
            cand_objs.append(Facility(facility_id=c_id, geometry=geom))

        demands = []
        for idx, d in enumerate(demand_points):
            if isinstance(d, dict):
                d_id = str(d.get("id", f"d_{idx}"))
                weight = float(d.get("weight", 1.0))
                coords = d.get("coordinates", [0, 0])
                geom = {"type": "Point", "coordinates": coords}
            else:
                d_id = f"d_{idx}"
                weight = 1.0
                geom = {"type": "Point", "coordinates": list(d)}
            demands.append(DemandPoint(demand_id=d_id, weight=weight, geometry=geom))

        res = self.location_allocation(
            candidate_facilities=cand_objs,
            demand_points=demands,
            p_count=n_to_choose,
            problem_type="p_median" if objective == "minimize_cost" else "max_coverage",
            network_dataset=net_ds,
            graph=graph,
            profile=profile,
        )
        return res

    async def solve_optimize_route(
        self,
        network: Any,
        depot: Any,
        stops: List[Any],
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level route optimization solver working with raw GeoJSON/dict inputs."""
        profile = profile or TravelProfile()
        graph, net_ds = self.builder.build_graph(network, profile=profile)

        depot_pt = depot if isinstance(depot, (list, tuple)) else depot.get("coordinates", [0, 0]) if isinstance(depot, dict) else [0, 0]
        stop_pts = [s if isinstance(s, (list, tuple)) else s.get("coordinates", [0, 0]) if isinstance(s, dict) else [0, 0] for s in stops]

        route = self.optimize_route(
            stops=[tuple(p) for p in stop_pts],
            depot=tuple(depot_pt),
            network_dataset=net_ds,
            graph=graph,
            profile=profile,
        )

        return NetworkAnalysisResult(
            analysis_type="optimize_route",
            status="success",
            routes=[route],
        )
