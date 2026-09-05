"""
Network Graph Engine Component.
Unified orchestrator facade exposing clean entry points for graph building, snapping, routing,
OD matrix calculation, closest facility search, service areas, accessibility, location-allocation,
and VRP route optimization.
"""
from __future__ import annotations
import asyncio
import math
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx

from app.lib.gis.scientific_errors import DisconnectedNetwork

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


# VNext（ADR-0099）：端点捕捉披露（scientific-honesty pack）。
# snapping 对每个端点报告到路网的距离与置信度，但此前从不进入工具结果 ——
# 捕捉是「最近边吸附」而非「容差内才命中」，超容差吸附必须逐端点可审计。
# 证据按端点有界（超出截断并计数），超容差（snapping 默认 500 m）的端点
# 同时浮出为结果警告。
_SNAP_EVIDENCE_MAX_POINTS = 32
_SNAP_DEFAULT_TOLERANCE_M = 500.0


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
    def _layer_items(layer: Any) -> Optional[List[Any]]:
        """Normalize a layer argument to its item list (audit #814).

        Accepts a bare list of items, a GeoJSON FeatureCollection (returns its
        features), or a single GeoJSON Feature (wraps it). Returns None when
        the shape is not recognizable — callers decide whether that is an
        error. Previously a FeatureCollection demand_layer was silently
        replaced by ``[]`` and solve_accessibility fabricated 0% coverage."""
        if isinstance(layer, list):
            return layer
        if isinstance(layer, dict):
            t = layer.get("type")
            if t == "FeatureCollection":
                feats = layer.get("features")
                return list(feats) if isinstance(feats, list) else []
            if t == "Feature":
                return [layer]
        return None

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
        if isinstance(raw, dict) and isinstance(raw.get("geometry"), dict):
            # GeoJSON Feature (audit #814): coordinates from geometry, attributes from properties.
            geom = raw["geometry"]
            coords = geom.get("coordinates")
            if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
                raise ValueError(
                    f"Facility feature at index {idx} has no valid geometry.coordinates."
                )
            props = raw.get("properties") or {}
            return Facility(
                facility_id=str(raw.get("id") or props.get("id") or f"fac_{idx}"),
                geometry={"type": "Point", "coordinates": [float(coords[0]), float(coords[1])]},
                capacity=float(props.get("capacity", 1.0) or 1.0),
                name=str(props.get("name", "") or ""),
            )
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
            elif isinstance(raw.get("geometry"), dict):
                # GeoJSON Feature (audit #814): weight from common property spellings.
                g = raw["geometry"]
                coords = g.get("coordinates")
                if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
                    raise ValueError(
                        f"Demand feature at index {idx} has no valid geometry.coordinates."
                    )
                props = raw.get("properties") or {}
                w = props.get("weight", props.get("population", props.get("pop", 1.0)))
                weight = float(w if w is not None else 1.0)
                d_id = str(raw.get("id") or props.get("id") or d_id)
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

    def _snap_evidence(
        self,
        labeled_points: List[Tuple[str, Tuple[float, float]]],
        network_dataset: NetworkDataset,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Bounded per-endpoint snapping disclosure (VNext, scientific-honesty).

        Returns ``(evidence, warnings)``: ``evidence`` maps each labeled
        endpoint to its snap distance (m) and confidence, capped at
        ``_SNAP_EVIDENCE_MAX_POINTS`` entries (beyond that a truncation flag +
        total count — batch inputs are already capped at the tool layer).
        Endpoints whose snap distance exceeds the snapping service's default
        tolerance additionally produce a warning string, so over-tolerance
        snapping is visible in the tool result instead of silent.
        """
        evidence: Dict[str, Any] = {}
        warnings: List[str] = []
        total = len(labeled_points)
        for idx, (label, pt) in enumerate(labeled_points):
            if idx >= _SNAP_EVIDENCE_MAX_POINTS:
                evidence["_truncated"] = True
                evidence["_total_endpoints"] = total
                break
            try:
                snap = self.snapper.snap_point(
                    (float(pt[0]), float(pt[1])), network_dataset
                )
            except Exception:
                continue  # 证据失败不改变分析本身
            evidence[label] = {
                "distance_m": round(float(snap.distance_to_network_m), 2),
                "confidence": round(float(snap.confidence), 3),
            }
            if float(snap.distance_to_network_m) > _SNAP_DEFAULT_TOLERANCE_M:
                warnings.append(
                    f"snap_evidence: {label} 距路网 {float(snap.distance_to_network_m):.1f} m "
                    f"超过默认捕捉容差 {_SNAP_DEFAULT_TOLERANCE_M:.0f} m（confidence=0，"
                    f"已吸附最近边；结果精度受此影响）"
                )
        return evidence, warnings

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

            # VNext：端点捕捉证据（有界）+ 可达性显式披露 —— 不可达路线
            # （total_cost=inf）保留 origin/destination，绝不以欧氏替代。
            snap_evidence, snap_warnings = self._snap_evidence(
                [("origin", orig_pt), ("destination", dest_pt)], net_ds
            )
            summary: Dict[str, Any] = {
                "snap_evidence": snap_evidence,
                "reachable": math.isfinite(route.total_cost),
            }

            return NetworkAnalysisResult(
                analysis_type="shortest_path",
                status="success",
                summary=summary,
                warnings=snap_warnings,
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

            # VNext：不可达对显式计数（行永远齐全，绝不静默缺行）；仅当
            # 全部起终点对均不可达（图从所有起点不连通）时抛科学错误。
            unreachable_count = sum(1 for p in pairs if not p.reachable)
            snap_evidence, snap_warnings = self._snap_evidence(
                [(f"origin_{i}", pt) for i, pt in enumerate(orig_pts)]
                + [(f"destination_{i}", pt) for i, pt in enumerate(dest_pts)],
                net_ds,
            )
            summary: Dict[str, Any] = {
                "pair_count": len(pairs),
                "unreachable_pair_count": unreachable_count,
                "unreachable_ratio": round(unreachable_count / len(pairs), 4) if pairs else 0.0,
                "snap_evidence": snap_evidence,
            }
            if cutoff_s is None and pairs and unreachable_count == len(pairs):
                # 仅在**未设 cutoff** 时全对不可达才构成结构事实（无截止的
                # Dijkstra 已探索整个可达分量 —— 不可达 ⇔ 有向图不连通）。
                # 紧 cutoff 把全部对裁掉是合法的预算选择，照常返回
                # reachable=False 行，不误报科学错误。
                raise DisconnectedNetwork(
                    f"OD 矩阵的 {len(pairs)} 个起终点对全部不可达：图从所有起点出发均不连通"
                    f"（岛屿分量/空图/单行方向隔离）。请检查路网连通性与捕捉容差。"
                )

            return NetworkAnalysisResult(
                analysis_type="od_matrix",
                status="success",
                summary=summary,
                warnings=snap_warnings,
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
            fac_res = fac_res if isinstance(fac_res, NetworkAnalysisResult) else NetworkAnalysisResult(
                analysis_type="closest_facility",
                status="success",
                routes=fac_res if isinstance(fac_res, list) else [],
            )
            # VNext：端点捕捉证据（有界）合并进结果 summary —— 超容差吸附
            # 必须可审计；未匹配需求点由 closest_facility 服务列入 summary。
            snap_evidence, snap_warnings = self._snap_evidence(
                [(f"incident_{i}", pt) for i, pt in enumerate(inc_pts)]
                + [(f"facility_{i}", (f.geometry["coordinates"][0], f.geometry["coordinates"][1]))
                   for i, f in enumerate(fac_objs)],
                net_ds,
            )
            fac_res.summary = {**fac_res.summary, "snap_evidence": snap_evidence}
            fac_res.warnings = [*fac_res.warnings, *snap_warnings]
            return fac_res

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

            # VNext：未产出任何服务区的设施（捕捉节点不在图内/空图）显式
            # 披露 —— 不再静默缺位，调用方据此判断覆盖缺口。
            returned_ids = {
                str(sa.facility_id)
                for sa in (sa_breaks if isinstance(sa_breaks, list) else [])
                if hasattr(sa, "facility_id")
            }
            unreachable_facility_ids = [
                f.facility_id for f in fac_objs if str(f.facility_id) not in returned_ids
            ]
            snap_evidence, snap_warnings = self._snap_evidence(
                [(f"facility_{i}", (f.geometry["coordinates"][0], f.geometry["coordinates"][1]))
                 for i, f in enumerate(fac_objs)],
                net_ds,
            )
            summary: Dict[str, Any] = {
                "facility_count": len(fac_objs),
                "unreachable_facility_count": len(unreachable_facility_ids),
                "unreachable_facility_ids": unreachable_facility_ids,
                "snap_evidence": snap_evidence,
            }

            return NetworkAnalysisResult(
                analysis_type="service_area",
                status="success",
                summary=summary,
                warnings=snap_warnings,
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
            raw_demands = self._layer_items(demand_layer)
            if raw_demands is None:
                raise ValueError(
                    "demand_layer must be a list of points, a GeoJSON FeatureCollection, "
                    f"or a single Feature — got {type(demand_layer).__name__} "
                    "(audit #814: unrecognized shapes must not fabricate 0% coverage)."
                )
            for idx, d in enumerate(raw_demands):
                demands.append(self._to_demand(d, idx))
            if not demands:
                raise ValueError(
                    "demand_layer parsed to zero demand points — refusing to "
                    "report fabricated 0% coverage (audit #814)."
                )

            raw_facilities = self._layer_items(facilities)
            if raw_facilities is None:
                raise ValueError(
                    "facilities must be a list of points, a GeoJSON FeatureCollection, "
                    f"or a single Feature — got {type(facilities).__name__}."
                )
            fac_objs = [self._to_facility(f, i) for i, f in enumerate(raw_facilities)]

            acc_res = self.accessibility(
                demand_points=demands,
                facilities=fac_objs,
                network_dataset=net_ds,
                graph=graph,
                cutoff_minutes=cutoff_minutes,
                profile=prof,
            )

            # VNext（Task D）：2SFCA/覆盖法方法学诊断 —— 供给总量、需求总量、
            # 捕获半径。工具层据此挂 build_evidence 的 diagnostics。
            supply_total = 0.0
            for f in fac_objs:
                try:
                    supply_total += float(f.capacity)
                except (TypeError, ValueError):
                    supply_total += 1.0
            summary: Dict[str, Any] = {
                "catchment_radius_min": float(cutoff_minutes),
                "demand_total": acc_res.total_demand,
                "supply_total": round(supply_total, 4),
                "demand_point_count": len(demands),
                "facility_count": len(fac_objs),
            }

            return NetworkAnalysisResult(
                analysis_type="accessibility",
                status="success",
                summary=summary,
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

