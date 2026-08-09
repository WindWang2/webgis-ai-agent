"""
Point Snapping Service Component.
Snaps arbitrary (lng, lat) coordinates onto the nearest edge of a network graph/dataset.
Computes snapped coordinate, nearest edge/node ID, fraction along edge, perpendicular distance,
confidence score, and tolerance breach correction hints.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from shapely.geometry import Point, LineString, shape
from shapely.strtree import STRtree

from app.services.network.models import NetworkDataset, Node, PointSnappingResult, Edge
from app.services.network.graph_builder import haversine_distance


class PointSnappingService:
    """
    Service for snapping points (facilities, demand points, incidents) onto network graph dataset edges.

    PERF-02: caches the per-dataset STRtree + node-id lookup map so that
    repeated snaps (OD matrix of N×M, service-area over many facilities,
    location-allocation) do not rebuild the spatial index and linearly scan
    all nodes on every call. The cache is keyed by (dataset_id, edge_count,
    node_count) so a rebuilt/changed dataset invalidates it automatically.
    """

    def __init__(self) -> None:
        # key -> (STRtree, edge_refs list, node_by_id dict)
        self._index_cache: Dict[Tuple[str, int, int], Tuple[STRtree, List[Edge], Dict[object, Node]]] = {}

    def _get_index(
        self, network_dataset: NetworkDataset
    ) -> Optional[Tuple[STRtree, List[Edge], Dict[object, Node]]]:
        """Build (and cache) the STRtree over edges + a node-id lookup map."""
        if not network_dataset.edges:
            return None
        cache_key = (
            getattr(network_dataset, "dataset_id", "") or "",
            len(network_dataset.edges),
            len(network_dataset.nodes),
        )
        cached = self._index_cache.get(cache_key)
        if cached is not None:
            return cached

        lines: List[LineString] = []
        edge_refs: List[Edge] = []
        for edge in network_dataset.edges:
            if edge.geometry:
                g = shape(edge.geometry)
                if isinstance(g, LineString):
                    lines.append(g)
                    edge_refs.append(edge)
            else:
                u_node = next((n for n in network_dataset.nodes if n.id == edge.u), None)
                v_node = next((n for n in network_dataset.nodes if n.id == edge.v), None)
                if u_node and v_node:
                    lines.append(LineString([(u_node.x, u_node.y), (v_node.x, v_node.y)]))
                    edge_refs.append(edge)

        node_by_id = {n.id: n for n in network_dataset.nodes}
        tree = STRtree(lines)
        entry = (tree, edge_refs, node_by_id)
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

        tree, edge_refs, node_by_id = index

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
        # Re-derive the nearest LineString from the edge (cheap, single shape).
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

        projected_distance_ratio = nearest_line.project(pt_geom, normalized=True)
        snapped_geom = nearest_line.interpolate(projected_distance_ratio, normalized=True)
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
