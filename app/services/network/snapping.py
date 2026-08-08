"""
Point Snapping Service Component.
Snaps arbitrary (lng, lat) coordinates onto the nearest edge of a network graph/dataset.
Computes snapped coordinate, nearest edge/node ID, fraction along edge, perpendicular distance,
confidence score, and tolerance breach correction hints.
"""
from __future__ import annotations
import math
from typing import List, Optional, Tuple, Union

from shapely.geometry import Point, LineString, shape
from shapely.strtree import STRtree

from app.services.network.models import NetworkDataset, PointSnappingResult, Node, Edge
from app.services.network.graph_builder import haversine_distance


class PointSnappingService:
    """
    Service for snapping points (facilities, demand points, incidents) onto network graph dataset edges.
    """

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
        if not network_dataset.edges:
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
                    g = LineString([(u_node.x, u_node.y), (v_node.x, v_node.y)])
                    lines.append(g)
                    edge_refs.append(edge)

        pt_geom = Point(point[0], point[1])
        tree = STRtree(lines)
        nearest_idx = tree.nearest(pt_geom)
        nearest_line = lines[nearest_idx]
        nearest_edge = edge_refs[nearest_idx]

        projected_distance_ratio = nearest_line.project(pt_geom, normalized=True)
        snapped_geom = nearest_line.interpolate(projected_distance_ratio, normalized=True)
        snapped_coord = (snapped_geom.x, snapped_geom.y)

        dist_m = haversine_distance(point, snapped_coord)

        u_node = next((n for n in network_dataset.nodes if n.id == nearest_edge.u), None)
        v_node = next((n for n in network_dataset.nodes if n.id == nearest_edge.v), None)

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
