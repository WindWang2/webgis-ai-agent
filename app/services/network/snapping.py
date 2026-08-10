"""
Point Snapping Service Component.
Snaps arbitrary (lng, lat) coordinates onto the nearest edge of a network graph/dataset.
Computes snapped coordinate, nearest edge/node ID, fraction along edge, perpendicular distance,
confidence score, and tolerance breach correction hints.
"""
from __future__ import annotations
import threading
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Point, LineString, shape
from shapely.strtree import STRtree

from app.services.network.models import NetworkDataset, Node, PointSnappingResult, Edge
from app.services.network.graph_builder import haversine_distance


def _utm_crs_for_bbox(bbox: Optional[List[float]]) -> Optional[str]:
    """Determine a local UTM CRS for a WGS84 bounding box [w,s,e,n].

    Returns None when the bbox is missing/degenerate (caller falls back to
    degree-space queries). Mirrors the zone detection in
    geo_processor/core.py to_utm_gdf.
    """
    if not bbox or len(bbox) < 4:
        return None
    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    if not all(isinstance(v, (int, float)) for v in (west, south, east, north)):
        return None
    lon = (west + east) / 2.0
    lat = (south + north) / 2.0
    # UTM is only defined between -80° and 84° latitude.
    if lat < -80.0 or lat > 84.0:
        return None
    zone_number = int((lon + 180.0) / 6.0) + 1
    zone_number = max(1, min(60, zone_number))
    hemisphere = 32600 if lat >= 0.0 else 32700
    return f"EPSG:{hemisphere + zone_number}"


def _project_coords(
    coords: List[Tuple[float, float]], proj: Any
) -> List[Tuple[float, float]]:
    """Project a WGS84 coordinate sequence with a pyproj Transformer's transform."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    px, py = proj.transform(xs, ys)
    return list(zip(px, py))


class PointSnappingService:
    """
    Service for snapping points (facilities, demand points, incidents) onto network graph dataset edges.

    PERF-02: caches the per-dataset STRtree + node-id lookup map so that
    repeated snaps (OD matrix of N×M, service-area over many facilities,
    location-allocation) do not rebuild the spatial index and linearly scan
    all nodes on every call.

    Reviewer B/A BLOCKING fix: the cache is keyed by the Python object identity
    of the NetworkDataset (id()), NOT by (dataset_id, edge_count, node_count).
    The previous key collided across distinct datasets with equal cardinality
    (dataset_id is itself only a hash of edge_count in graph_builder), which
    silently returned the wrong network's STRtree. Object identity is a sound
    memoization key (matches the pattern in geo_processor/core.py to_utm_gdf)
    and is invalidated automatically when a new NetworkDataset is built.

    Thread-safety: the shared snapper instance is called from multiple worker
    threads (network tools run under ToolExecutionPolicy.THREAD). The cache is
    guarded by a lock, mirroring NetworkGraphBuilder._cache_lock.

    GIS-02 (deep-audit round 2): the STRtree used to run in raw WGS84 degrees,
    so "nearest" was nearest-in-degrees, not nearest-in-meters (a degree of
    longitude is ~85 km at 40°N vs ~111 km of latitude). Edges are now
    projected to a local UTM zone derived from the dataset bbox, the tree is
    built in meters, and query points are projected before the nearest query.
    The returned coordinates remain WGS84.
    """

    def __init__(self) -> None:
        # id(dataset) -> (STRtree, edge_refs list, node_by_id dict, proj_to_utm, proj_to_wgs, utm_crs)
        self._index_cache: Dict[
            int, Tuple[STRtree, List[Edge], Dict[object, Node], Any, Any, Optional[str]]
        ] = {}
        self._cache_lock = threading.Lock()

    def _get_index(
        self, network_dataset: NetworkDataset
    ) -> Optional[Tuple[STRtree, List[Edge], Dict[object, Node], Any, Any, Optional[str]]]:
        """Build (and cache) the STRtree over edges + a node-id lookup map.

        Returns (tree, edge_refs, node_by_id, to_utm, to_wgs, utm_crs); tree is
        built in UTM meters when a zone can be determined, else in degrees.
        """
        if not network_dataset.edges:
            return None
        # Object-identity key: a rebuilt/changed dataset is a new object → new
        # key → no stale collision. id() is stable for the object's lifetime.
        cache_key = id(network_dataset)
        with self._cache_lock:
            cached = self._index_cache.get(cache_key)
        if cached is not None:
            return cached

        # GIS-02: project to local UTM so the STRtree measures real meters.
        utm_crs = _utm_crs_for_bbox(getattr(network_dataset, "bounding_box", None))
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
                utm_crs = None

        lines: List[LineString] = []
        edge_refs: List[Edge] = []
        for edge in network_dataset.edges:
            if edge.geometry:
                g = shape(edge.geometry)
                if isinstance(g, LineString):
                    coords = list(g.coords)
                    if to_utm is not None:
                        coords = _project_coords(coords, to_utm)
                    lines.append(LineString(coords))
                    edge_refs.append(edge)
            else:
                u_node = next((n for n in network_dataset.nodes if n.id == edge.u), None)
                v_node = next((n for n in network_dataset.nodes if n.id == edge.v), None)
                if u_node and v_node:
                    coords = [(u_node.x, u_node.y), (v_node.x, v_node.y)]
                    if to_utm is not None:
                        coords = _project_coords(coords, to_utm)
                    lines.append(LineString(coords))
                    edge_refs.append(edge)

        node_by_id = {n.id: n for n in network_dataset.nodes}
        tree = STRtree(lines)
        entry = (tree, edge_refs, node_by_id, to_utm, to_wgs, utm_crs)
        with self._cache_lock:
            # Bound the cache to avoid unbounded growth across many datasets.
            if len(self._index_cache) >= 32:
                self._index_cache.pop(next(iter(self._index_cache)))
            self._index_cache[cache_key] = entry
        return entry

    def snap_point(
        self,
        point: Tuple[float, float],
        network_dataset: NetworkDataset,
        max_tolerance_m: float = 500.0,
    ) -> PointSnappingResult:
        """
        Snaps a single (lng, lat) point to the nearest edge in the network dataset.

        Args:
            point: Tuple of (lng, lat).
            network_dataset: NetworkDataset object.
            max_tolerance_m: Maximum acceptable distance in meters.

        Returns:
            PointSnappingResult object.
        """
        index = self._get_index(network_dataset)
        if index is None:
            # No edges — fall back to nearest-node snapping (unchanged behavior).
            if network_dataset.nodes:
                nearest_n = min(
                    network_dataset.nodes,
                    key=lambda n: haversine_distance(point, (n.x, n.y)),
                )
                dist_m = haversine_distance(point, (nearest_n.x, nearest_n.y))
                conf = max(0.0, 1.0 - dist_m / max_tolerance_m) if dist_m <= max_tolerance_m else 0.0
                hint = (
                    f"Distance {dist_m:.1f}m exceeding tolerance threshold {max_tolerance_m:.1f}m."
                    if dist_m > max_tolerance_m
                    else None
                )
                return PointSnappingResult(
                    original_point=point,
                    snapped_point=(nearest_n.x, nearest_n.y),
                    nearest_node_id=nearest_n.id,
                    nearest_edge_id=None,
                    fraction_along_edge=0.0,
                    distance_to_network_m=dist_m,
                    confidence=conf,
                    correction_hint=hint,
                )

            return PointSnappingResult(
                original_point=point,
                snapped_point=point,
                nearest_node_id="n_none",
                nearest_edge_id=None,
                fraction_along_edge=0.0,
                distance_to_network_m=0.0,
                confidence=1.0,
                correction_hint=None,
            )

        tree, edge_refs, node_by_id, to_utm, to_wgs, utm_crs = index

        # GIS-02: query the tree in the same space it was built in (UTM meters
        # when available, degrees otherwise).
        if to_utm is not None:
            px, py = to_utm.transform(point[0], point[1])
            pt_geom = Point(px, py)
        else:
            pt_geom = Point(point[0], point[1])
        nearest_idx = tree.nearest(pt_geom)
        # Guard against None (shapely returns None if the tree is empty).
        if nearest_idx is None:
            return PointSnappingResult(
                original_point=point,
                snapped_point=point,
                nearest_node_id="n_none",
                nearest_edge_id=None,
                fraction_along_edge=0.0,
                distance_to_network_m=0.0,
                confidence=1.0,
                correction_hint=None,
            )

        nearest_edge = edge_refs[nearest_idx]
        # Re-derive the nearest LineString from the edge. We need the geometry
        # in the SAME space as the tree: UTM when projected, else WGS84.
        if nearest_edge.geometry:
            nearest_line = shape(nearest_edge.geometry)
            if not isinstance(nearest_line, LineString):
                nearest_line = nearest_line.geoms[0] if hasattr(nearest_line, "geoms") else LineString()
        else:
            u_node = node_by_id.get(nearest_edge.u)
            v_node = node_by_id.get(nearest_edge.v)
            if u_node and v_node:
                nearest_line = LineString([(u_node.x, u_node.y), (v_node.x, v_node.y)])
            else:
                nearest_line = LineString()
        if to_utm is not None:
            nearest_line = LineString(_project_coords(list(nearest_line.coords), to_utm))

        projected_distance_ratio = nearest_line.project(pt_geom, normalized=True)
        snapped_geom = nearest_line.interpolate(projected_distance_ratio, normalized=True)

        if to_wgs is not None:
            sx, sy = to_wgs.transform(snapped_geom.x, snapped_geom.y)
            snapped_coord = (sx, sy)
        else:
            snapped_coord = (snapped_geom.x, snapped_geom.y)

        dist_m = haversine_distance(point, snapped_coord)

        # PERF-02: O(1) dict lookup instead of O(N) linear scan per snap.
        u_node = node_by_id.get(nearest_edge.u)
        v_node = node_by_id.get(nearest_edge.v)

        nearest_node_id = nearest_edge.u
        if u_node and v_node:
            dist_u = haversine_distance(snapped_coord, (u_node.x, u_node.y))
            dist_v = haversine_distance(snapped_coord, (v_node.x, v_node.y))
            nearest_node_id = u_node.id if dist_u <= dist_v else v_node.id

        confidence = max(0.0, 1.0 - (dist_m / max_tolerance_m)) if dist_m <= max_tolerance_m else 0.0
        correction_hint = (
            f"Distance {dist_m:.1f}m exceeding tolerance threshold {max_tolerance_m:.1f}m. Consider increasing max_tolerance_m."
            if dist_m > max_tolerance_m
            else None
        )

        return PointSnappingResult(
            original_point=point,
            snapped_point=snapped_coord,
            nearest_node_id=nearest_node_id,
            nearest_edge_id=nearest_edge.id,
            fraction_along_edge=float(projected_distance_ratio),
            distance_to_network_m=dist_m,
            confidence=confidence,
            correction_hint=correction_hint,
        )

    def snap_points(
        self,
        points: List[Tuple[float, float]],
        network_dataset: NetworkDataset,
        max_tolerance_m: float = 500.0,
    ) -> List[PointSnappingResult]:
        """Snaps a batch of (lng, lat) points onto the network dataset."""
        return [self.snap_point(pt, network_dataset, max_tolerance_m) for pt in points]
