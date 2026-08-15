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
from app.services.network.routing import NetworkRoutingService, build_weight_func


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

        res = self._compute_od(
            origins, destinations, graph, network_dataset,
            profile=profile, impedance=impedance, barriers=barriers,
        )

        od_matrix: List[ODPair] = []
        for o_node, o_label in res["origin_nodes"]:
            costs = res["results"].get(o_node, {}).get("dists", {})
            dists = res["results"].get(o_node, {}).get("dist_m", {})
            times = res["results"].get(o_node, {}).get("time_s", {})

            for d_node, d_label in res["dest_nodes"]:
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

    def network_od_paths(
        self,
        origins: List[Union[Tuple[float, float], str, PointSnappingResult]],
        destinations: List[Union[Tuple[float, float], str, PointSnappingResult]],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> Dict[str, Any]:
        """Batch OD with full shortest-path trees, one Dijkstra per unique origin.

        Returns a dict with:
        - ``origin_nodes`` / ``origin_labels``: resolved (node_id, label) per origin
        - ``dest_nodes`` / ``dest_labels``: resolved (node_id, label) per destination
        - ``graph_view`` / ``weight_func``: the (barrier-applied) graph and the
          exact weight function used, so callers can reconstruct full Route
          geometries from the paths without re-running Dijkstra or copying the
          graph per pair.
        - ``pairs``: {(origin_label, dest_label): {"path": [node ids...],
          "cost": float, "distance_m": float, "time_s": float, "reachable": bool}}
        """
        if not origins or not destinations:
            return {
                "origin_nodes": [], "origin_labels": [],
                "dest_nodes": [], "dest_labels": [],
                "graph_view": graph, "weight_func": None,
                "pairs": {},
            }

        res = self._compute_od(
            origins, destinations, graph, network_dataset,
            profile=profile, impedance=impedance, barriers=barriers,
        )

        pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for o_node, o_label in res["origin_nodes"]:
            origin_res = res["results"].get(o_node, {})
            costs = origin_res.get("dists", {})
            paths = origin_res.get("paths", {})
            dists = origin_res.get("dist_m", {})
            times = origin_res.get("time_s", {})
            for d_node, d_label in res["dest_nodes"]:
                if d_node in costs:
                    pairs[(o_label, d_label)] = {
                        "path": list(paths.get(d_node, [])),
                        "cost": float(costs[d_node]),
                        "distance_m": float(dists.get(d_node, 0.0)),
                        "time_s": float(times.get(d_node, 0.0)),
                        "reachable": True,
                    }
                else:
                    pairs[(o_label, d_label)] = {
                        "path": [], "cost": float("inf"),
                        "distance_m": float("inf"), "time_s": float("inf"),
                        "reachable": False,
                    }

        return {
            "origin_nodes": res["origin_nodes"],
            "origin_labels": [label for _, label in res["origin_nodes"]],
            "dest_nodes": res["dest_nodes"],
            "dest_labels": [label for _, label in res["dest_nodes"]],
            "graph_view": res["graph_view"],
            "weight_func": res["weight_func"],
            "pairs": pairs,
        }

    def _compute_od(
        self,
        origins: List[Union[Tuple[float, float], str, PointSnappingResult]],
        destinations: List[Union[Tuple[float, float], str, PointSnappingResult]],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
        barriers: Optional[List[Barrier]] = None,
    ) -> Dict[str, Any]:
        """Shared multi-source Dijkstra core used by matrix and path variants."""
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

        turn_penalty = impedance.turn_penalty_s if impedance else 0.0
        if profile and profile.turn_penalty_s:
            turn_penalty = max(turn_penalty, profile.turn_penalty_s)

        # #455: OD trees carry pure edge costs — a turn penalty depends on the
        # full path context that a shortest-path tree does not have. See
        # build_weight_func's docstring for the cross-tool semantics.
        weight_func = build_weight_func(cost_field)

        # GIS-19: one Dijkstra per unique origin with the impedance weight, then
        # recover distance and time by walking the shortest-path predecessor
        # tree summing length_m / travel_time_s along each edge. The previous
        # code ran THREE full Dijkstra passes per origin (cost, distance, time)
        # even though distance and time accumulate along the same shortest path.
        unique_orig_nodes = set(n_id for n_id, _ in orig_nodes)
        dijkstra_results: Dict[str, Dict[str, float]] = {}
        dijkstra_dist: Dict[str, Dict[str, float]] = {}
        dijkstra_time: Dict[str, Dict[str, float]] = {}
        dijkstra_paths: Dict[str, Dict[str, list]] = {}

        for o_node in unique_orig_nodes:
            if o_node in graph_view:
                # nx.single_source_dijkstra returns (dist_dict, path_dict);
                # path_dict maps each reachable node to its FULL path list
                # ([origin, ..., node]). Sum length_m / travel_time_s along each
                # path in one O(path-length) pass per node — no extra Dijkstra.
                dists, paths = nx.single_source_dijkstra(graph_view, o_node, weight=weight_func)
                dijkstra_results[o_node] = dists
                dijkstra_paths[o_node] = paths

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
                dijkstra_paths[o_node] = {}
                dijkstra_dist[o_node] = {}
                dijkstra_time[o_node] = {}

        return {
            "origin_nodes": orig_nodes,
            "dest_nodes": dest_nodes,
            "graph_view": graph_view,
            "weight_func": weight_func,
            "results": {
                o_node: {
                    "dists": dijkstra_results.get(o_node, {}),
                    "paths": dijkstra_paths.get(o_node, {}),
                    "dist_m": dijkstra_dist.get(o_node, {}),
                    "time_s": dijkstra_time.get(o_node, {}),
                }
                for o_node, _ in orig_nodes
            },
        }
