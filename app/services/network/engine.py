"""
Network Graph Engine Component.
Unified orchestrator facade exposing clean entry points for graph building, snapping, routing,
OD matrix calculation, closest facility search, service areas, accessibility, location-allocation,
and VRP route optimization.
"""
from __future__ import annotations
import asyncio
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
        cutoff_s: Optional[float] = None,
    ) -> List[ODPair]:
        """Calculates batch N x M origin-destination cost matrix.

        ``cutoff_s`` bounds the per-origin Dijkstra in the active impedance's
        cost units (#449); pairs beyond it are returned unreachable.
        """
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
            cutoff_s=cutoff_s,
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

    # --- Private Input Parsing Helpers (S3/S4 dedup, W1 fail-fast) ---

    @staticmethod
    def _parse_point(raw: Any, label: str = "point") -> Tuple[float, float]:
        """Parses raw input into (lng, lat) tuple. Raises ValueError instead of falling back to [0,0]."""
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return (float(raw[0]), float(raw[1]))
        if isinstance(raw, dict):
            if "coordinates" in raw:
                coords = raw["coordinates"]
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    return (float(coords[0]), float(coords[1]))
            # GeoJSON Feature with geometry
            geom = raw.get("geometry", {})
            if isinstance(geom, dict) and "coordinates" in geom:
                coords = geom["coordinates"]
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    return (float(coords[0]), float(coords[1]))
        raise ValueError(
            f"Cannot parse {label} to (lng, lat) coordinate. "
            f"Expected [lng, lat] array, {{coordinates: [lng, lat]}} dict, "
            f"or GeoJSON Feature. Got: {type(raw).__name__}"
        )

    @staticmethod
    def _to_facility(raw: Any, idx: int) -> Facility:
        """Converts raw input to a Facility domain object."""
        if isinstance(raw, Facility):
            return raw
        if isinstance(raw, dict) and "coordinates" in raw:
            fac_id = str(raw.get("id", f"fac_{idx}"))
            geom = {"type": "Point", "coordinates": raw["coordinates"]}
            return Facility(facility_id=fac_id, geometry=geom,
                            capacity=float(raw.get("capacity", 1.0)),
                            name=str(raw.get("name", "")))
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return Facility(
                facility_id=f"fac_{idx}",
                geometry={"type": "Point", "coordinates": [float(raw[0]), float(raw[1])]},
            )
        raise ValueError(
            f"Cannot parse facility at index {idx}. "
            f"Expected [lng, lat], {{coordinates: [lng, lat]}} dict, or Facility object. "
            f"Got: {type(raw).__name__}"
        )

    @staticmethod
    def _to_demand(raw: Any, idx: int) -> DemandPoint:
        """Converts raw input to a DemandPoint domain object."""
        if isinstance(raw, DemandPoint):
            return raw
        if isinstance(raw, dict):
            d_id = str(raw.get("id", f"d_{idx}"))
            weight = float(raw.get("weight", 1.0))
            coords = raw.get("coordinates")
            if coords and isinstance(coords, (list, tuple)) and len(coords) >= 2:
                geom = {"type": "Point", "coordinates": [float(coords[0]), float(coords[1])]}
            else:
                raise ValueError(
                    f"Demand point at index {idx} missing valid 'coordinates' field."
                )
            return DemandPoint(demand_id=d_id, weight=weight, geometry=geom)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return DemandPoint(
                demand_id=f"d_{idx}",
                weight=1.0,
                geometry={"type": "Point", "coordinates": [float(raw[0]), float(raw[1])]},
            )
        raise ValueError(
            f"Cannot parse demand point at index {idx}. "
            f"Expected [lng, lat], {{coordinates: [lng, lat], weight: N}} dict, or DemandPoint. "
            f"Got: {type(raw).__name__}"
        )

    def _ensure_graph(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        profile: Optional[TravelProfile] = None,
    ) -> Tuple[nx.DiGraph, NetworkDataset]:
        """Builds or fetches cached graph, ensuring a non-None graph is returned."""
        return self.builder.build_graph(network, profile=profile)

    # --- High-level Async Tool/Harness Seam Interfaces ---

    async def solve_shortest_path(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        origin: Any,
        destination: Any,
        profile: Optional[TravelProfile] = None,
        barriers: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level shortest path solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)
            orig_pt = self._parse_point(origin, "origin")
            dest_pt = self._parse_point(destination, "destination")

            barrier_objs = []
            if barriers:
                for idx, b in enumerate(barriers):
                    geom = b if isinstance(b, dict) and "type" in b else {"type": "Point", "coordinates": b}
                    barrier_objs.append(Barrier(barrier_id=f"b_{idx}", geometry=geom))

            route = self.shortest_path(
                origin=orig_pt,
                destination=dest_pt,
                network_dataset=net_ds,
                graph=graph,
                profile=prof,
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

        return await asyncio.to_thread(_sync_solve)

    async def solve_od_matrix(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        origins: List[Any],
        destinations: List[Any],
        profile: Optional[TravelProfile] = None,
        cutoff_s: Optional[float] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level OD matrix solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)
            orig_pts = [self._parse_point(p, f"origin[{i}]") for i, p in enumerate(origins)]
            dest_pts = [self._parse_point(p, f"destination[{i}]") for i, p in enumerate(destinations)]

            pairs = self.od_matrix(
                origins=orig_pts,
                destinations=dest_pts,
                network_dataset=net_ds,
                graph=graph,
                profile=prof,
                cutoff_s=cutoff_s,
            )

            return NetworkAnalysisResult(
                analysis_type="od_matrix",
                status="success",
                od_matrix=pairs,
            )

        return await asyncio.to_thread(_sync_solve)

    async def solve_closest_facility(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        incidents: List[Any],
        facilities: List[Any],
        profile: Optional[TravelProfile] = None,
        number_to_find: int = 1,
        cutoff_cost: Optional[float] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level closest facility solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)
            inc_pts = [self._parse_point(p, f"incident[{i}]") for i, p in enumerate(incidents)]
            fac_objs = [self._to_facility(f, i) for i, f in enumerate(facilities)]

            fac_res = self.closest_facility(
                demand_points=inc_pts,
                facilities=fac_objs,
                network_dataset=net_ds,
                graph=graph,
                cutoff_cost=cutoff_cost,
                target_facility_count=number_to_find,
                profile=prof,
            )
            return fac_res if isinstance(fac_res, NetworkAnalysisResult) else NetworkAnalysisResult(
                analysis_type="closest_facility",
                status="success",
                routes=fac_res if isinstance(fac_res, list) else [],
            )

        return await asyncio.to_thread(_sync_solve)

    async def solve_service_area(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        facilities: List[Any],
        breaks_minutes: Optional[List[float]] = None,
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level service area solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            b_minutes = breaks_minutes or [5.0, 10.0, 15.0]
            graph, net_ds = self._ensure_graph(network, prof)
            fac_objs = [self._to_facility(f, i) for i, f in enumerate(facilities)]

            sa_breaks = self.service_area(
                facilities=fac_objs,
                breaks=b_minutes,
                network_dataset=net_ds,
                graph=graph,
                profile=prof,
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

        return await asyncio.to_thread(_sync_solve)

    async def solve_accessibility(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        demand_layer: Any,
        facilities: List[Any],
        cutoff_minutes: float = 15.0,
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level accessibility solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)

            demands = []
            raw_demands = demand_layer if isinstance(demand_layer, list) else []
            for idx, d in enumerate(raw_demands):
                demands.append(self._to_demand(d, idx))

            fac_objs = [self._to_facility(f, i) for i, f in enumerate(facilities)]

            acc_res = self.accessibility(
                demand_points=demands,
                facilities=fac_objs,
                network_dataset=net_ds,
                graph=graph,
                cutoff_minutes=cutoff_minutes,
                profile=prof,
            )

            return NetworkAnalysisResult(
                analysis_type="accessibility",
                status="success",
                accessibility=acc_res,
            )

        return await asyncio.to_thread(_sync_solve)

    async def solve_location_allocation(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        candidate_facilities: List[Any],
        demand_points: List[Any],
        n_to_choose: int = 2,
        objective: str = "minimize_cost",
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level location-allocation solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)

            cand_objs = [self._to_facility(c, i) for i, c in enumerate(candidate_facilities)]
            demands = [self._to_demand(d, i) for i, d in enumerate(demand_points)]

            return self.location_allocation(
                candidate_facilities=cand_objs,
                demand_points=demands,
                p_count=n_to_choose,
                problem_type="p_median" if objective == "minimize_cost" else "max_coverage",
                network_dataset=net_ds,
                graph=graph,
                profile=prof,
            )

        return await asyncio.to_thread(_sync_solve)

    async def solve_optimize_route(
        self,
        network: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        depot: Any,
        stops: List[Any],
        profile: Optional[TravelProfile] = None,
        session_id: str = "",
    ) -> NetworkAnalysisResult:
        """High level route optimization solver working with raw GeoJSON/dict inputs."""
        def _sync_solve():
            prof = profile or TravelProfile()
            graph, net_ds = self._ensure_graph(network, prof)

            depot_pt = self._parse_point(depot, "depot")
            stop_pts = [self._parse_point(s, f"stop[{i}]") for i, s in enumerate(stops)]

            route = self.optimize_route(
                stops=stop_pts,
                depot=depot_pt,
                network_dataset=net_ds,
                graph=graph,
                profile=prof,
            )

            return NetworkAnalysisResult(
                analysis_type="optimize_route",
                status="success",
                routes=[route],
            )

        return await asyncio.to_thread(_sync_solve)

