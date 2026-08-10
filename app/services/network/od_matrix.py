"""
Network Origin-Destination (OD) Matrix Service Component.
Implements fast batch N x M cost matrix calculation using multi-source Dijkstra.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Barrier,
    ODPair,
    PointSnappingResult,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService


class NetworkODMatrixService:
    """
    Service for calculating batch N x M Origin-Destination cost matrices over spatial network datasets.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)

    def network_od_matrix(
        self,
        origins: List[Union[Tuple[float, float], str, PointSnappingResult]],
        destinations: List[Union[Tuple[float, float], str, PointSnappingResult]],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> List[ODPair]:
        """
        Computes batch origin-destination travel costs and distances.

        Args:
            origins: List of origin locations (lng, lat), node IDs, or PointSnappingResult.
            destinations: List of destination locations.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            profile: TravelProfile.
            impedance: Impedance model.
            barriers: Optional list of barriers to avoid.

        Returns:
            List of ODPair objects.
        """
        if not origins or not destinations:
            return []

        # Resolve all origin nodes and labels
        orig_nodes: List[Tuple[str, str]] = [
            self.router._resolve_node(o, network_dataset) for o in origins
        ]
        dest_nodes: List[Tuple[str, str]] = [
            self.router._resolve_node(d, network_dataset) for d in destinations
        ]

        graph_view = self.router._apply_barriers(graph, barriers)

        cost_field = "travel_time_s"
        if impedance and impedance.name:
            cost_field = impedance.name
        elif profile and profile.impedance_field:
            cost_field = profile.impedance_field

        def weight_func(u: Any, v: Any, edge_data: Dict[str, Any]) -> float:
            base_w = edge_data.get(cost_field, edge_data.get("length_m", 1.0))
            if base_w is None or base_w <= 0:
                base_w = 0.001
            barrier_factor = edge_data.get("_barrier_factor", 1.0)
            return max(0.0001, float(base_w * barrier_factor))

        # GIS-19: one Dijkstra per unique origin with the impedance weight, then
        # recover distance and time by walking the shortest-path predecessor
        # tree summing length_m / travel_time_s along each edge. The previous
        # code ran THREE full Dijkstra passes per origin (cost, distance, time)
        # even though distance and time accumulate along the same shortest path.
        unique_orig_nodes = set(n_id for n_id, _ in orig_nodes)
        dijkstra_results: Dict[str, Dict[str, float]] = {}
        dijkstra_dist: Dict[str, Dict[str, float]] = {}
        dijkstra_time: Dict[str, Dict[str, float]] = {}

        for o_node in unique_orig_nodes:
            if o_node in graph_view:
                # nx.single_source_dijkstra returns (dist_dict, path_dict);
                # path_dict maps each reachable node to its FULL path list
                # ([origin, ..., node]). Sum length_m / travel_time_s along each
                # path in one O(path-length) pass per node — no extra Dijkstra.
                dists, paths = nx.single_source_dijkstra(graph_view, o_node, weight=weight_func)
                dijkstra_results[o_node] = dists

                distances: Dict[str, float] = {}
                times: Dict[str, float] = {}
                for node, path in paths.items():
                    if node == o_node or len(path) < 2:
                        distances[node] = 0.0
                        times[node] = 0.0
                        continue
                    dist_acc = 0.0
                    time_acc = 0.0
                    for i in range(len(path) - 1):
                        edge_data = graph_view[path[i]][path[i + 1]]
                        dist_acc += float(edge_data.get("length_m", 0.0))
                        time_acc += float(edge_data.get("travel_time_s", 0.0))
                    distances[node] = dist_acc
                    times[node] = time_acc
                dijkstra_dist[o_node] = distances
                dijkstra_time[o_node] = times
            else:
                dijkstra_results[o_node] = {}
                dijkstra_dist[o_node] = {}
                dijkstra_time[o_node] = {}

        od_matrix: List[ODPair] = []

        for o_node, o_label in orig_nodes:
            costs = dijkstra_results.get(o_node, {})
            dists = dijkstra_dist.get(o_node, {})
            times = dijkstra_time.get(o_node, {})

            for d_node, d_label in dest_nodes:
                if d_node in costs:
                    dist_m = dists.get(d_node, 0.0)
                    time_s = times.get(d_node, 0.0)
                    od_matrix.append(
                        ODPair(
                            origin_id=o_label,
                            destination_id=d_label,
                            distance_m=round(dist_m, 2),
                            travel_time_s=round(time_s, 2),
                            reachable=True,
                        )
                    )
                else:
                    od_matrix.append(
                        ODPair(
                            origin_id=o_label,
                            destination_id=d_label,
                            distance_m=float("inf"),
                            travel_time_s=float("inf"),
                            reachable=False,
                        )
                    )

        return od_matrix
