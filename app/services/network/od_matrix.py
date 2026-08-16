"""
Network Origin-Destination (OD) Matrix Service Component.
Implements fast batch N x M cost matrix calculation using multi-source Dijkstra.
"""
from __future__ import annotations
import heapq
import itertools
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


def _single_source_costs(
    graph: nx.DiGraph,
    source: Any,
    weight_func,
    cutoff: Optional[float] = None,
) -> Tuple[Dict[Any, float], Dict[Any, float], Dict[Any, float]]:
    """Single-source Dijkstra accumulating cost, length and time per node (#449).

    Unlike ``nx.single_source_dijkstra`` this never materializes path lists —
    networkx builds the FULL path ([origin, ..., node]) for every reachable
    node, O(sum|path|) ≈ O(V²) worst case (measured 48.9 s for one origin on
    an 8k-node path graph) — and the previous code then re-walked those paths
    in Python to re-sum distance/time. Here each settled node carries its
    accumulated cost / length_m / travel_time_s directly (GIS-19 semantics:
    secondary metrics accumulate along the chosen shortest-path tree).

    ``cutoff`` (in cost units of the active impedance) prunes both the frontier
    pushes and the settling loop; nodes beyond it are simply absent from the
    returned maps.
    """
    dist: Dict[Any, float] = {}
    acc_len: Dict[Any, float] = {}
    acc_time: Dict[Any, float] = {}
    counter = itertools.count()
    # (cost, tiebreak, length, time, node) — the int tiebreak keeps heap
    # comparisons total even when node ids have mixed types.
    heap = [(0.0, next(counter), 0.0, 0.0, source)]
    while heap:
        cost, _, alen, atime, node = heapq.heappop(heap)
        if node in dist:
            continue  # stale entry
        if cutoff is not None and cost > cutoff:
            break  # everything left on the heap is beyond the cutoff
        dist[node] = cost
        acc_len[node] = alen
        acc_time[node] = atime
        for nbr, edata in graph[node].items():
            if nbr in dist:
                continue
            nc = cost + weight_func(node, nbr, edata)
            if cutoff is not None and nc > cutoff:
                continue
            nl = alen + float(edata.get("length_m") or 0.0)
            nt = atime + float(edata.get("travel_time_s") or 0.0)
            heapq.heappush(heap, (nc, next(counter), nl, nt, nbr))
    return dist, acc_len, acc_time


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
        cutoff_s: Optional[float] = None,
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
            cutoff_s: Optional cost cutoff in the ACTIVE impedance's units
                (seconds for travel_time_s, meters for length_m). Pairs whose
                cost exceeds it are returned unreachable (#449).

        Returns:
            List of ODPair objects.
        """
        if not origins or not destinations:
            return []

        res = self._compute_od(
            origins, destinations, graph, network_dataset,
            profile=profile, impedance=impedance, barriers=barriers,
            cutoff_s=cutoff_s, need_paths=False,
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
        cutoff_s: Optional[float] = None,
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
            cutoff_s=cutoff_s, need_paths=True,
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
        cutoff_s: Optional[float] = None,
        need_paths: bool = False,
    ) -> Dict[str, Any]:
        """Shared multi-source Dijkstra core used by matrix and path variants.

        #449: ``need_paths=False`` (cost-only matrix) runs
        ``_single_source_costs`` — an accumulating Dijkstra with genuine
        ``cutoff_s`` pruning and NO path materialization (networkx's
        ``single_source_dijkstra`` builds full path lists for every reachable
        node, O(V²) worst case, which the old code then re-walked in Python
        to re-sum distance/time). ``need_paths=True`` keeps networkx's
        path-returning variant (closest-facility / VRP reconstruct routes
        from the trees) and forwards the cutoff to it.
        """
        # #453: coordinate / PointSnappingResult inputs are resolved on a
        # single working-copy graph with the SAME virtual-node mid-edge
        # splitting that network_shortest_path uses (GIS-01). The previous
        # endpoint-only resolution made the OD family report up to ~2 edge
        # lengths more than routing for the same physical OD pair. Node-id
        # inputs keep using the caller's graph untouched (no copy).
        needs_split = any(not isinstance(t, str) for t in origins) or any(
            not isinstance(t, str) for t in destinations
        )
        graph_work = graph.copy() if needs_split else graph

        # Resolve all origin nodes and labels
        orig_nodes: List[Tuple[str, str]] = [
            self.router._resolve_with_snap(o, network_dataset, graph=graph_work)[:2]
            for o in origins
        ]
        dest_nodes: List[Tuple[str, str]] = [
            self.router._resolve_with_snap(d, network_dataset, graph=graph_work)[:2]
            for d in destinations
        ]

        graph_view = self.router._apply_barriers(graph_work, barriers)

        cost_field = "travel_time_s"
        if impedance and impedance.name:
            cost_field = impedance.name
        elif profile and profile.impedance_field:
            cost_field = profile.impedance_field

        # #455: OD trees carry pure edge costs — a turn penalty depends on the
        # full path context that a shortest-path tree does not have. See
        # build_weight_func's docstring for the cross-tool semantics.
        weight_func = build_weight_func(cost_field)

        # GIS-19: one Dijkstra per unique origin with the impedance weight;
        # distance and time accumulate along the same shortest-path tree.
        # #449: the cost-only variant accumulates them during the search.
        unique_orig_nodes = set(n_id for n_id, _ in orig_nodes)
        dijkstra_results: Dict[str, Dict[str, float]] = {}
        dijkstra_dist: Dict[str, Dict[str, float]] = {}
        dijkstra_time: Dict[str, Dict[str, float]] = {}
        dijkstra_paths: Dict[str, Dict[str, list]] = {}

        for o_node in unique_orig_nodes:
            if o_node not in graph_view:
                dijkstra_results[o_node] = {}
                dijkstra_paths[o_node] = {}
                dijkstra_dist[o_node] = {}
                dijkstra_time[o_node] = {}
                continue
            if not need_paths:
                dists, distances, times = _single_source_costs(
                    graph_view, o_node, weight_func, cutoff=cutoff_s
                )
                dijkstra_results[o_node] = dists
                dijkstra_dist[o_node] = distances
                dijkstra_time[o_node] = times
                dijkstra_paths[o_node] = {}
            else:
                # Path variant: networkx returns (dist_dict, path_dict); the
                # cutoff keeps nodes beyond it out of both maps.
                dists, paths = nx.single_source_dijkstra(
                    graph_view, o_node, weight=weight_func, cutoff=cutoff_s
                )
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
