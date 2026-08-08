"""
Network Routing Service Component.
Implements network_shortest_path using Dijkstra / A* over NetworkX DiGraph.
Supports custom impedance metrics, turn penalties, point and polygon barrier avoidance,
route GeoJSON line generation, and turn-by-turn directions.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
from shapely.geometry import LineString, shape

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Barrier,
    Route,
    PointSnappingResult,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.graph_builder import haversine_distance


class NetworkRoutingService:
    """
    Service for calculating shortest path routes over spatial network graphs.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()

    def network_shortest_path(
        self,
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        origin: Union[Tuple[float, float], str, PointSnappingResult],
        destination: Union[Tuple[float, float], str, PointSnappingResult],
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
        algorithm: str = "dijkstra",
    ) -> Route:
        """
        Calculates network shortest path between origin and destination.

        Args:
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            origin: (lng, lat) tuple, node ID, or PointSnappingResult.
            destination: (lng, lat) tuple, node ID, or PointSnappingResult.
            profile: TravelProfile defining speed and defaults.
            impedance: Impedance model defining cost metric (length_m, travel_time_s, custom).
            barriers: List of Barrier objects to avoid or penalize.
            algorithm: 'dijkstra' or 'astar'.

        Returns:
            Route object.
        """
        # Resolve start and end node IDs
        start_node_id, origin_id_str = self._resolve_node(origin, network_dataset)
        end_node_id, dest_id_str = self._resolve_node(destination, network_dataset)

        # Apply barriers to graph copy
        graph_view = self._apply_barriers(graph, barriers)

        # Determine weight field
        cost_field = "travel_time_s"
        if impedance and impedance.name:
            cost_field = impedance.name
        elif profile and profile.impedance_field:
            cost_field = profile.impedance_field

        turn_penalty = impedance.turn_penalty_s if impedance else 0.0
        if profile and profile.turn_penalty_s:
            turn_penalty = max(turn_penalty, profile.turn_penalty_s)

        def weight_func(u: Any, v: Any, edge_data: Dict[str, Any]) -> float:
            base_w = edge_data.get(cost_field, edge_data.get("length_m", 1.0))
            if base_w is None or base_w <= 0:
                base_w = 0.001
            barrier_factor = edge_data.get("_barrier_factor", 1.0)
            w = base_w * barrier_factor
            if turn_penalty > 0 and cost_field == "travel_time_s":
                w += turn_penalty
            return max(0.0001, float(w))

        # Run Pathfinding
        try:
            if algorithm.lower() == "astar":
                def heuristic(u: Any, v: Any) -> float:
                    u_data = graph_view.nodes[u]
                    v_data = graph_view.nodes[v]
                    dist_m = haversine_distance((u_data["x"], u_data["y"]), (v_data["x"], v_data["y"]))
                    if cost_field == "travel_time_s":
                        speed = profile.speed_kmh if profile else 40.0
                        return dist_m / ((speed * 1000.0) / 3600.0)
                    return dist_m

                path_nodes = nx.astar_path(graph_view, start_node_id, end_node_id, heuristic=heuristic, weight=weight_func)
            else:
                path_nodes = nx.dijkstra_path(graph_view, start_node_id, end_node_id, weight=weight_func)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            # Fallback empty route when no path exists
            return Route(
                route_id="r_none",
                origin_id=origin_id_str,
                destination_id=dest_id_str,
                profile_name=profile.name if profile else "driving",
                total_distance_m=0.0,
                total_time_s=0.0,
                total_cost=float("inf"),
                geometry={"type": "LineString", "coordinates": []},
                path_node_ids=[],
                path_edge_ids=[],
                directions=[],
            )

        # Build route details
        path_edges: List[str] = []
        route_coords: List[Tuple[float, float]] = []
        total_dist_m = 0.0
        total_time_s = 0.0
        total_cost = 0.0

        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i + 1]
            edge_data = graph_view[u][v]
            edge_id = edge_data.get("id", f"e_{u}_{v}")
            path_edges.append(edge_id)

            e_dist = edge_data.get("length_m", 0.0)
            e_time = edge_data.get("travel_time_s", 0.0)
            e_cost = weight_func(u, v, edge_data)

            total_dist_m += e_dist
            total_time_s += e_time
            total_cost += e_cost

            geom_dict = edge_data.get("geometry")
            if geom_dict:
                geom = shape(geom_dict)
                coords = list(geom.coords)
                if route_coords:
                    # Avoid duplicate vertex at joint
                    coords = coords[1:] if coords and route_coords[-1] == coords[0] else coords
                route_coords.extend(coords)
            else:
                u_data = graph_view.nodes[u]
                v_data = graph_view.nodes[v]
                if not route_coords:
                    route_coords.append((u_data["x"], u_data["y"]))
                route_coords.append((v_data["x"], v_data["y"]))

        directions = self._generate_directions(graph_view, path_nodes)
        profile_name = profile.name if profile else "driving"
        route_id = f"route_{origin_id_str}_{dest_id_str}"

        return Route(
            route_id=route_id,
            origin_id=origin_id_str,
            destination_id=dest_id_str,
            profile_name=profile_name,
            total_distance_m=total_dist_m,
            total_time_s=total_time_s,
            total_cost=total_cost,
            geometry={"type": "LineString", "coordinates": route_coords},
            path_node_ids=path_nodes,
            path_edge_ids=path_edges,
            directions=directions,
        )

    def _resolve_node(
        self,
        target: Union[Tuple[float, float], str, PointSnappingResult],
        network_dataset: NetworkDataset,
    ) -> Tuple[str, str]:
        """Resolves target input into (graph_node_id, identifier_label)."""
        if isinstance(target, str):
            return target, target
        if isinstance(target, PointSnappingResult):
            return str(target.nearest_node_id), f"pt_{target.snapped_point[0]:.4f}_{target.snapped_point[1]:.4f}"
        if isinstance(target, (tuple, list)) and len(target) >= 2:
            snap_res = self.snapper.snap_point((float(target[0]), float(target[1])), network_dataset)
            return str(snap_res.nearest_node_id), f"pt_{target[0]:.4f}_{target[1]:.4f}"
        raise ValueError(f"Invalid target location format: {target}")

    def _apply_barriers(self, graph: nx.DiGraph, barriers: Optional[List[Barrier]]) -> nx.DiGraph:
        """Applies barriers to a graph copy, removing or penalizing blocked edges."""
        graph_copy = graph.copy()
        if not barriers:
            return graph_copy

        for barrier in barriers:
            b_geom = shape(barrier.geometry)
            factor = barrier.impedance_factor

            # Identify edges intersecting barrier (penalize their impedance).
            edges_to_penalize = []

            for u, v, data in graph_copy.edges(data=True):
                edge_geom_dict = data.get("geometry")
                if edge_geom_dict:
                    edge_shape = shape(edge_geom_dict)
                    if b_geom.intersects(edge_shape):
                        edges_to_penalize.append((u, v))
                else:
                    u_data = graph_copy.nodes[u]
                    v_data = graph_copy.nodes[v]
                    segment = LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
                    if b_geom.intersects(segment):
                        edges_to_penalize.append((u, v))

            if math.isinf(factor) or factor >= 1e6:
                graph_copy.remove_edges_from(edges_to_penalize)
            else:
                for u, v in edges_to_penalize:
                    curr_factor = graph_copy[u][v].get("_barrier_factor", 1.0)
                    graph_copy[u][v]["_barrier_factor"] = curr_factor * factor

        return graph_copy

    def _generate_directions(self, graph: nx.DiGraph, path_nodes: List[Any]) -> List[Dict[str, Any]]:
        """Generates step-by-step turn directions for a node path sequence."""
        directions: List[Dict[str, Any]] = []
        if len(path_nodes) < 2:
            return directions

        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i + 1]
            edge_data = graph[u][v]
            street_name = edge_data.get("properties", {}).get("name", edge_data.get("name", "unnamed road"))
            dist_m = edge_data.get("length_m", 0.0)
            time_s = edge_data.get("travel_time_s", 0.0)

            turn_type = "Depart" if i == 0 else "Continue"
            if i > 0:
                prev_u = path_nodes[i - 1]
                turn_type = self._calculate_turn_type(graph, prev_u, u, v)

            directions.append({
                "step": i + 1,
                "action": turn_type,
                "street_name": street_name,
                "distance_m": round(dist_m, 1),
                "time_s": round(time_s, 1),
                "instruction": f"{turn_type} onto {street_name}" if i > 0 else f"Head on {street_name}",
            })

        # Add arrival step
        directions.append({
            "step": len(path_nodes),
            "action": "Arrive",
            "street_name": "Destination",
            "distance_m": 0.0,
            "time_s": 0.0,
            "instruction": "Arrive at destination",
        })
        return directions

    def _calculate_turn_type(self, graph: nx.DiGraph, node_a: Any, node_b: Any, node_c: Any) -> str:
        """Computes turn bearing angle and categorizes turn movement."""
        data_a = graph.nodes[node_a]
        data_b = graph.nodes[node_b]
        data_c = graph.nodes[node_c]

        v1 = (data_b["x"] - data_a["x"], data_b["y"] - data_a["y"])
        v2 = (data_c["x"] - data_b["x"], data_c["y"] - data_b["y"])

        bearing1 = math.atan2(v1[1], v1[0])
        bearing2 = math.atan2(v2[1], v2[0])

        diff_deg = math.degrees(bearing2 - bearing1)
        while diff_deg > 180:
            diff_deg -= 360
        while diff_deg <= -180:
            diff_deg += 360

        if abs(diff_deg) <= 25:
            return "Continue straight"
        elif 25 < diff_deg <= 65:
            return "Turn slight right"
        elif 65 < diff_deg <= 125:
            return "Turn right"
        elif diff_deg > 125:
            return "Make a U-turn right"
        elif -65 <= diff_deg < -25:
            return "Turn slight left"
        elif -125 <= diff_deg < -65:
            return "Turn left"
        else:
            return "Make a U-turn left"
