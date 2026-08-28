"""
Network Service Area Service Component.
Calculates network service area isochrones for multiple breaks (e.g. 5, 10, 15, 30 min / meters)
returning reachable network edges and boundary polygons.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString, shape, mapping
from shapely.ops import unary_union, substring

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Barrier,
    Facility,
    ServiceAreaBreak,
    ServiceArea,
)
from app.services.network.snapping import PointSnappingService, _utm_crs_for_bbox
from app.services.network.routing import NetworkRoutingService

# GIS-08 (deep-audit round 3): the isochrone smoothing buffer was 0.005
# DEGREES — at 40°N the longitude component is ~425 m but ~555 m at the
# equator, so the smoothing radius varied by tens of percent across a dataset
# (and by latitude within it). Buffer radii are now meters in a local UTM zone.
_ISOCHRONE_BUFFER_M = 150.0  # road-width smoothing in meters


_ALLOWED_BREAK_UNITS = {"minutes", "meters", "seconds"}
_BREAK_UNIT_ALIASES = {"minutes": "minutes", "meters": "meters", "seconds": "seconds", "km": "meters"}


def _normalize_break_unit(raw: str) -> str:
    unit = str(raw or "").strip().lower()
    if unit in _BREAK_UNIT_ALIASES:
        return _BREAK_UNIT_ALIASES[unit]
    raise ValueError(f"Unsupported break_unit '{raw}'. Allowed: minutes, meters (seconds accepted as alias).")


def _break_to_cutoff(
    brk_val: float,
    break_unit: str,
    impedance: Optional[Impedance],
) -> float:
    """Convert a service-area break into Dijkstra cutoff units.

    #618-20: ``break_unit=="minutes"`` is wall-clock minutes. Graph time
    weights are seconds (``travel_time_s`` and other seconds-based custom
    impedances), so minutes always convert unless ``impedance.unit`` is
    already ``minutes``. Distance breaks pass through unchanged.
    break_unit is validated; 'seconds' is accepted and converted to minutes
    for Dijkstra seconds weight (seconds/60 -> minutes handled by caller).
    """
    norm = _normalize_break_unit(break_unit)
    raw = str(break_unit or "").strip().lower()
    if raw == "km":
        # km is an alias for meters — the VALUE must scale with the unit
        # rename, otherwise a 5 km isochrone computes as 5 m (#706).
        return float(brk_val) * 1000.0
    if norm == "seconds":
        # seconds break with seconds-weighted graph: pass through as seconds;
        # with minutes-weighted graph, convert.
        unit = impedance.unit if impedance is not None else "seconds"
        if unit == "minutes":
            return float(brk_val) / 60.0
        return float(brk_val)
    if norm != "minutes":
        return float(brk_val)
    unit = impedance.unit if impedance is not None else "seconds"
    if unit == "minutes":
        return float(brk_val)
    return float(brk_val) * 60.0


class NetworkServiceAreaService:
    """
    Service for calculating network service areas (isochrones / service drive-time zones).
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)

    def network_service_area(
        self,
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
        breaks: List[float],
        break_unit: str = "minutes",
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> List[ServiceArea]:
        """
        Calculates service areas and isochrone polygons for facilities across specified breaks.

        Args:
            facilities: List of Facility objects or (lng, lat) tuples.
            breaks: Cutoff break values e.g. [5.0, 10.0, 15.0].
            break_unit: 'minutes' / 'meters' / 'seconds'（km 为 meters 别名；秒按速度换算为分钟）
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            profile: TravelProfile.
            impedance: Impedance model.
            barriers: Optional barriers list.

        Returns:
            List of ServiceArea objects.
        """
        if not facilities or not breaks:
            return []

        normalized_facilities = self._normalize_facilities(facilities)
        sorted_breaks = sorted(breaks)

        # #453: resolve facility coordinates on a working-copy graph with the
        # same virtual-node mid-edge splitting routing uses — the isochrone
        # must be rooted at the facility's snapped position, not at the
        # nearest edge endpoint (up to a full edge-length away).
        # PERF (#540): the previous code copied the whole graph per call even
        # when NO facility snapped mid-edge (node-exact snaps insert no virtual
        # node). Snap once against the DATASET (the snapper's STRtree is cached
        # across calls — PERF-02), then copy the working graph only when at
        # least one facility actually needs a virtual-node split.
        coords_snaps: List[Tuple[Tuple[float, float], Any]] = []
        needs_split = False
        for fac in normalized_facilities:
            coords = (fac.geometry["coordinates"][0], fac.geometry["coordinates"][1])
            if graph is not None:
                snap_res = self.snapper.snap_point(coords, network_dataset)
                if (snap_res.nearest_edge_id is not None
                        and 1e-6 < snap_res.fraction_along_edge < 1.0 - 1e-6):
                    needs_split = True
            else:
                snap_res = None
            coords_snaps.append((coords, snap_res))

        graph_work = graph.copy() if needs_split else graph
        resolved_starts: List[Tuple[str, Tuple[float, float]]] = []
        for coords, snap_res in coords_snaps:
            if needs_split:
                node_id, _ = self.router._resolve_with_snap(
                    snap_res, network_dataset, graph=graph_work
                )[:2]
            elif graph is not None:
                # node-exact snap (no split needed): resolve WITHOUT a working
                # graph so the caller's graph is never touched.
                node_id, _ = self.router._resolve_with_snap(
                    snap_res, network_dataset, graph=None
                )[:2]
            else:
                node_id, _ = self.router._resolve_node(coords, network_dataset)
            resolved_starts.append((node_id, coords))

        # PERF (#540): ``graph_work`` is already a private copy when a split was
        # needed, so barriers apply in place — no second graph copy.
        graph_view = self.router._apply_barriers(graph_work, barriers, copy_graph=not needs_split)

        # #706: cost_field must follow the NORMALIZED unit — the raw-string
        # compare picked length_m for break_unit="seconds", silently comparing
        # a seconds cutoff against meter weights (540 s → 540 m isochrone).
        normalized_break_unit = _normalize_break_unit(break_unit)
        cost_field = (
            "travel_time_s"
            if normalized_break_unit in ("minutes", "seconds")
            else "length_m"
        )
        if impedance and impedance.name:
            cost_field = impedance.name

        def weight_func(u: Any, v: Any, edge_data: Dict[str, Any]) -> float:
            base_w = edge_data.get(cost_field, edge_data.get("length_m", 1.0))
            if base_w is None or base_w <= 0:
                base_w = 0.001
            barrier_factor = edge_data.get("_barrier_factor", 1.0)
            return max(0.0001, float(base_w * barrier_factor))

        # GIS-08: derive the local UTM zone once per dataset so every break's
        # polygon uses the same meter-space projection.
        bbox = getattr(network_dataset, "bounding_box", None) if network_dataset else None
        utm_crs = _utm_crs_for_bbox(bbox)
        to_utm: Any = None
        to_wgs: Any = None
        if utm_crs:
            try:
                from pyproj import Transformer

                to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
                to_wgs = Transformer.from_crs(utm_crs, "EPSG:4326", always_xy=True)
            except Exception:
                to_utm = None
                to_wgs = None

        service_areas: List[ServiceArea] = []

        for fac, (start_node_id, fac_coords) in zip(normalized_facilities, resolved_starts):
            if start_node_id not in graph_view:
                continue

            max_break = sorted_breaks[-1]
            max_cutoff = _break_to_cutoff(max_break, break_unit, impedance)

            node_costs = nx.single_source_dijkstra_path_length(graph_view, start_node_id, cutoff=max_cutoff, weight=weight_func)

            # #1063: 单趟边分类。每个可达节点从其首个 break 进入；每条边
            # 只分类一次（两端都可达的 break 起全量、其前的 break 按剩余
            # 预算截断），追加到所属的每个 break。旧实现按 break 重扫全部
            # 可达边并重建几何并集 —— O(B×E)，嵌套 break 重复小 break 的
            # 全部工作。输出集合与旧实现逐 break 等价（golden 校验）。
            cutoffs = [
                _break_to_cutoff(b, break_unit, impedance) for b in sorted_breaks
            ]
            n_breaks = len(sorted_breaks)
            first_idx: Dict[Any, int] = {}
            for node, cost in node_costs.items():
                for bi, cutoff in enumerate(cutoffs):
                    if cost <= cutoff:
                        first_idx[node] = bi
                        break

            node_coords_per_break: List[List[Tuple[float, float]]] = [
                [] for _ in range(n_breaks)
            ]
            for node, bi in first_idx.items():
                data = graph_view.nodes[node]
                xy = (data["x"], data["y"])
                for k in range(bi, n_breaks):
                    node_coords_per_break[k].append(xy)

            line_geoms_per_break: List[List[LineString]] = [
                [] for _ in range(n_breaks)
            ]
            edge_counts = [0] * n_breaks
            for u, cost_u in node_costs.items():
                u_idx = first_idx[u]
                for v in graph_view.successors(u):
                    edge_data = graph_view[u][v]
                    line = self._edge_linestring(graph_view, u, v, edge_data)
                    v_idx = first_idx.get(v)
                    for bi in range(u_idx, n_breaks):
                        if v_idx is not None and v_idx <= bi:
                            # v 在本 break 可达 → 全边属于 bi 及之后所有 break
                            for bj in range(bi, n_breaks):
                                edge_counts[bj] += 1
                                line_geoms_per_break[bj].append(line)
                            break
                        # 部分截断（#618-20）：远端点超出该 break 的预算
                        w = weight_func(u, v, edge_data)
                        remaining = cutoffs[bi] - cost_u
                        if remaining <= 0 or w <= 0:
                            continue
                        frac = min(1.0, remaining / w)
                        if frac <= 0:
                            continue
                        try:
                            seg = (
                                line
                                if frac >= 1.0
                                else substring(line, 0.0, frac, normalized=True)
                            )
                        except Exception:
                            seg = line
                        if (
                            not isinstance(seg, LineString)
                            or getattr(seg, "is_empty", False)
                        ):
                            continue
                        edge_counts[bi] += 1
                        line_geoms_per_break[bi].append(seg)

            sa_breaks: List[ServiceAreaBreak] = []
            for bi, brk_val in enumerate(sorted_breaks):
                # Build boundary polygon (GIS-08/09)
                poly_geojson = self._build_isochrone_polygon(
                    node_coords_per_break[bi], line_geoms_per_break[bi], fac_coords,
                    to_utm=to_utm, to_wgs=to_wgs,
                )

                reachable_line_geoms = line_geoms_per_break[bi]
                reachable_net_dict = (
                    mapping(MultiLineString(reachable_line_geoms))
                    if reachable_line_geoms
                    else None
                )

                sa_break = ServiceAreaBreak(
                    break_value=brk_val,
                    break_unit=break_unit,
                    geometry=poly_geojson,
                    reachable_network_geometry=reachable_net_dict,
                    reachable_edge_count=edge_counts[bi],
                )
                sa_breaks.append(sa_break)

            overall_poly = sa_breaks[-1].geometry if sa_breaks else None
            sa = ServiceArea(
                facility_id=fac.facility_id,
                mode=profile.name if profile else "driving",
                breaks=sa_breaks,
                overall_geometry=overall_poly,
            )
            service_areas.append(sa)

        return service_areas

    @staticmethod
    def _edge_linestring(graph_view: nx.DiGraph, u: Any, v: Any, edge_data: Dict[str, Any]) -> LineString:
        g_dict = edge_data.get("geometry")
        if g_dict:
            geom = shape(g_dict)
            if isinstance(geom, LineString):
                return geom
        u_d = graph_view.nodes[u]
        v_d = graph_view.nodes[v]
        return LineString([(u_d["x"], u_d["y"]), (v_d["x"], v_d["y"])])

    def _build_isochrone_polygon(
        self,
        node_coords: List[Tuple[float, float]],
        line_geoms: List[LineString],
        fac_coords: Tuple[float, float],
        to_utm: Any = None,
        to_wgs: Any = None,
    ) -> Dict[str, Any]:
        """Constructs smoothed isochrone boundary polygon.

        GIS-08/09 (deep-audit round 3):
        - The old code buffered by 0.005 DEGREES (non-uniform: ~425 m of
          longitude at 40°N vs ~555 m at the equator) and used the CONVEX HULL
          of reachable nodes, which bridges unreachable gaps (e.g. a road loop
          with an unreachable center) and overstates coverage.
        - Now: when UTM projection is available, the reachable edges are
          buffered by a fixed METER radius and unioned — coverage follows the
          actual road network (concave, no gap-bridging). Without projection
          (e.g. polar regions) it degrades to a point-buffer fallback.
        """
        if to_utm is not None:
            try:
                # Project reachable edges + facility into UTM, buffer in meters,
                # union, then project back to WGS84.
                utm_lines: List[LineString] = []
                for lg in line_geoms:
                    xs = [c[0] for c in lg.coords]
                    ys = [c[1] for c in lg.coords]
                    px, py = to_utm.transform(xs, ys)
                    utm_lines.append(LineString(list(zip(px, py))))

                if utm_lines:
                    net_union = unary_union(utm_lines).buffer(_ISOCHRONE_BUFFER_M)
                    if net_union.is_empty:
                        raise ValueError("empty union")
                    if to_wgs is not None:
                        # Project the polygon back (shapely.transform handles
                        # exterior + interiors).
                        from shapely.ops import transform as shp_transform

                        def _to_wgs(x: float, y: float) -> Tuple[float, float]:
                            wx, wy = to_wgs.transform(x, y)
                            return wx, wy

                        net_union = shp_transform(_to_wgs, net_union)
                    return mapping(net_union)
            except Exception:
                # Fall through to the point-buffer fallback below.
                pass

        # Fallback (no projection or empty reachable edges): buffer the node
        # cluster using latitude-adjusted degree distance (150m equivalent).
        import math
        lat_deg = float(fac_coords[1]) if len(fac_coords) > 1 else 0.0
        cos_lat = max(0.01, math.cos(math.radians(lat_deg)))
        buf_deg = (_ISOCHRONE_BUFFER_M / 111320.0) / cos_lat

        points = [Point(c[0], c[1]) for c in node_coords] if node_coords else [Point(fac_coords)]
        if len(points) >= 3:
            mp = MultiPoint(points)
            hull = mp.convex_hull.buffer(buf_deg)
            return mapping(hull)
        elif len(points) > 0:
            mp = MultiPoint(points)
            buffered = mp.buffer(buf_deg)
            return mapping(buffered)
        else:
            p = Point(fac_coords).buffer(buf_deg)
            return mapping(p)

    def _normalize_facilities(
        self,
        facilities: List[Union[Facility, Tuple[float, float], Dict[str, Any]]],
    ) -> List[Facility]:
        res: List[Facility] = []
        for i, item in enumerate(facilities):
            if isinstance(item, Facility):
                res.append(item)
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                res.append(
                    Facility(
                        facility_id=f"f_{i}",
                        geometry={"type": "Point", "coordinates": [float(item[0]), float(item[1])]},
                    )
                )
            elif isinstance(item, dict):
                f_id = item.get("facility_id", item.get("id", f"f_{i}"))
                geom = item.get("geometry", {"type": "Point", "coordinates": [0.0, 0.0]})
                res.append(Facility(facility_id=str(f_id), geometry=geom))
        return res
