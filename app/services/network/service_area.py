"""
Network Service Area Service Component.
Calculates network service area isochrones for multiple breaks (e.g. 5, 10, 15, 30 min / meters)
returning reachable network edges and boundary polygons.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString, shape, mapping
from shapely.ops import unary_union

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
            break_unit: 'minutes' or 'meters'.
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

        graph_view = self.router._apply_barriers(graph, barriers)

        cost_field = "travel_time_s" if break_unit == "minutes" else "length_m"
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

        for fac in normalized_facilities:
            fac_coords = (fac.geometry["coordinates"][0], fac.geometry["coordinates"][1])
            start_node_id, _ = self.router._resolve_node(fac_coords, network_dataset)

            if start_node_id not in graph_view:
                continue

            max_break = sorted_breaks[-1]
            max_cutoff = max_break * 60.0 if break_unit == "minutes" and cost_field == "travel_time_s" else max_break

            node_costs = nx.single_source_dijkstra_path_length(graph_view, start_node_id, cutoff=max_cutoff, weight=weight_func)

            sa_breaks: List[ServiceAreaBreak] = []

            for brk_val in sorted_breaks:
                cutoff = brk_val * 60.0 if break_unit == "minutes" and cost_field == "travel_time_s" else brk_val
                reachable_nodes = [n for n, cost in node_costs.items() if cost <= cutoff]

                # Extract node coordinates
                node_coords: List[Tuple[float, float]] = []
                for n in reachable_nodes:
                    data = graph_view.nodes[n]
                    node_coords.append((data["x"], data["y"]))

                # Collect reachable edges
                reachable_line_geoms: List[LineString] = []
                edge_count = 0

                for u in reachable_nodes:
                    for v in graph_view.successors(u):
                        if v in reachable_nodes:
                            edge_count += 1
                            edge_data = graph_view[u][v]
                            g_dict = edge_data.get("geometry")
                            if g_dict:
                                reachable_line_geoms.append(shape(g_dict))
                            else:
                                u_d = graph_view.nodes[u]
                                v_d = graph_view.nodes[v]
                                reachable_line_geoms.append(LineString([(u_d["x"], u_d["y"]), (v_d["x"], v_d["y"])]))

                # Build boundary polygon (GIS-08/09)
                poly_geojson = self._build_isochrone_polygon(
                    node_coords, reachable_line_geoms, fac_coords,
                    to_utm=to_utm, to_wgs=to_wgs,
                )

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
                    reachable_edge_count=edge_count,
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
        # cluster. Keep the old degree-based convex hull ONLY as a last resort
        # so the shape is never empty.
        points = [Point(c[0], c[1]) for c in node_coords] if node_coords else [Point(fac_coords)]
        if len(points) >= 3:
            mp = MultiPoint(points)
            hull = mp.convex_hull.buffer(0.005)
            return mapping(hull)
        elif len(points) > 0:
            mp = MultiPoint(points)
            buffered = mp.buffer(0.005)
            return mapping(buffered)
        else:
            p = Point(fac_coords).buffer(0.005)
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
