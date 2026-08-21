"""
Network Route Optimization (VRP / TSP) Service Component.
Implements optimize_route using 2-opt local search heuristic and multi-stop route stitching.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Route,
    DemandPoint,
    Facility,
)
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService
from app.services.network.od_matrix import NetworkODMatrixService


class NetworkRouteOptimizationService:
    """
    Service for optimizing multi-stop routing orders (Traveling Salesperson Problem / VRP).
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.router = NetworkRoutingService(snapper=self.snapper)
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    def optimize_route(
        self,
        stops: List[Union[Tuple[float, float], DemandPoint, Dict[str, Any]]],
        depot: Optional[Union[Tuple[float, float], Facility, Dict[str, Any]]] = None,
        end_at_depot: bool = True,
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
        impedance: Optional[Impedance] = None,
    ) -> Route:
        """
        Optimizes route traversal sequence across multiple stops using 2-opt search.

        Args:
            stops: List of stop locations ((lng, lat) or DemandPoint/dict).
            depot: Optional starting/ending depot location.
            end_at_depot: Whether route must return to depot at the end.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            profile: TravelProfile.
            impedance: Impedance model.

        Returns:
            Optimized combined Route object.
        """
        norm_stops = self._normalize_coords(stops)
        if not norm_stops:
            return Route(
                route_id="vrp_empty",
                origin_id="none",
                destination_id="none",
                profile_name=profile.name if profile else "driving",
                total_distance_m=0.0,
                total_time_s=0.0,
                total_cost=0.0,
                geometry={"type": "LineString", "coordinates": []},
            )

        all_points: List[Tuple[float, float]] = []
        if depot is not None:
            depot_coord = self._normalize_coords([depot])[0]
            all_points.append(depot_coord)

        all_points.extend(norm_stops)

        if len(all_points) == 1:
            return self.router.network_shortest_path(
                graph=graph,
                network_dataset=network_dataset,
                origin=all_points[0],
                destination=all_points[0],
                profile=profile,
                impedance=impedance,
            )

        # Compute N x N cost matrix + shortest-path trees in one multi-source
        # Dijkstra pass per unique stop. The predecessor trees are then reused
        # to stitch the tour legs — previously every leg re-ran a full
        # network_shortest_path (including a per-call graph copy) even though
        # the OD pass had already computed every cost.
        od = self.od_service.network_od_paths(
            origins=all_points,
            destinations=all_points,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
            impedance=impedance,
        )
        labels = od["origin_labels"]

        n = len(all_points)
        cost_mat: List[List[float]] = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                info = od["pairs"].get((labels[i], labels[j]))
                if info and info["reachable"]:
                    # #710: optimize the tour under the ACTIVE impedance —
                    # od["cost"] is impedance-resolved (od_matrix weight_func),
                    # matching build_route_from_path's total_cost accumulation;
                    # time_s made the tour time-optimal under any impedance.
                    cost_mat[i][j] = info["cost"]
                else:
                    cost_mat[i][j] = 1e9

        # Nearest Neighbor initial tour
        unvisited = set(range(1, n))
        tour = [0]
        curr = 0
        while unvisited:
            nxt = min(unvisited, key=lambda node: cost_mat[curr][node])
            tour.append(nxt)
            unvisited.remove(nxt)
            curr = nxt

        if depot is not None and end_at_depot:
            tour.append(0)

        # 2-Opt local search improvement
        tour = self._two_opt(tour, cost_mat, is_roundtrip=(depot is not None and end_at_depot))

        # Stitch detailed routes between consecutive stops in optimized tour
        route_coords: List[Tuple[float, float]] = []
        path_nodes: List[Union[int, str]] = []
        path_edges: List[Union[int, str]] = []
        combined_directions: List[Dict[str, Any]] = []

        total_dist_m = 0.0
        total_time_s = 0.0
        total_cost = 0.0

        for idx in range(len(tour) - 1):
            src_idx, dst_idx = tour[idx], tour[idx + 1]
            info = od["pairs"].get((labels[src_idx], labels[dst_idx]))

            if info and info["reachable"]:
                sub_route = self.router.build_route_from_path(
                    od["graph_view"], info["path"],
                    origin_label=labels[src_idx], destination_label=labels[dst_idx],
                    profile_name=profile.name if profile else "driving",
                    route_id=f"leg_{src_idx}_{dst_idx}",
                    weight_func=od["weight_func"],
                )
            else:
                sub_route = Route(
                    route_id=f"leg_{src_idx}_{dst_idx}",
                    origin_id=labels[src_idx],
                    destination_id=labels[dst_idx],
                    profile_name=profile.name if profile else "driving",
                    total_distance_m=0.0,
                    total_time_s=0.0,
                    total_cost=float("inf"),
                    geometry={"type": "LineString", "coordinates": []},
                )

            total_dist_m += sub_route.total_distance_m
            total_time_s += sub_route.total_time_s
            total_cost += sub_route.total_cost

            coords = sub_route.geometry.get("coordinates", [])
            if coords:
                if route_coords and route_coords[-1] == coords[0]:
                    route_coords.extend(coords[1:])
                else:
                    route_coords.extend(coords)

            path_nodes.extend(sub_route.path_node_ids)
            path_edges.extend(sub_route.path_edge_ids)
            combined_directions.extend(sub_route.directions)

        origin_label = f"stop_{tour[0]}"
        dest_label = f"stop_{tour[-1]}"

        return Route(
            route_id=f"vrp_opt_{len(tour)}_stops",
            origin_id=origin_label,
            destination_id=dest_label,
            profile_name=profile.name if profile else "driving",
            total_distance_m=round(total_dist_m, 2),
            total_time_s=round(total_time_s, 2),
            total_cost=round(total_cost, 2),
            geometry={"type": "LineString", "coordinates": route_coords},
            path_node_ids=path_nodes,
            path_edge_ids=path_edges,
            directions=combined_directions,
        )

    def _two_opt(self, tour: List[int], cost_mat: List[List[float]], is_roundtrip: bool) -> List[int]:
        """Refines tour order using 2-opt edge swap heuristic.

        PERF (#540): the previous implementation rebuilt the reversed tour list
        and re-summed the FULL tour cost for every candidate — O(n) per
        candidate, O(n³) per improvement scan (measured 0.2 s @ 80 stops → 3.1 s
        @ 160 → 51 s @ 320 on the adversarial interleaved "ladder", ~n^3.9). A
        reversal of segment [i..j] removes edges (i-1,i) and (j,j+1), adds
        (i-1,j) and (i,j+1), and flips the ORIENTATION of every internal edge
        (k,k+1) → (k+1,k). The cost matrix is DIRECTED (one-way roads), so the
        orientation flip is charged via a per-scan prefix over
        (cost[k+1][k] − cost[k][k+1]): every candidate is then O(1). The
        accepted-move sequence — and therefore the resulting tour — is
        bit-identical to the naive recompute (equivalence-tested).
        """
        best_tour = list(tour)
        n = len(best_tour)
        improved = True
        max_iter = 100
        iteration = 0

        best_cost = sum(cost_mat[best_tour[k]][best_tour[k + 1]] for k in range(n - 1))

        end_idx = n - 1 if is_roundtrip else n

        while improved and iteration < max_iter:
            improved = False
            iteration += 1
            # rev_prefix[p] = Σ_{k=0..p} (cost[tour[k+1]][tour[k]] − cost[tour[k]][tour[k+1]])
            # → internal-edge orientation cost of reversing [i..j] =
            #   rev_prefix[j-1] − rev_prefix[i-1]  (edges i..j-1).
            rev_prefix = [0.0] * n
            acc = 0.0
            for p in range(n - 1):
                acc += cost_mat[best_tour[p + 1]][best_tour[p]] - cost_mat[best_tour[p]][best_tour[p + 1]]
                rev_prefix[p] = acc
            for i in range(1, end_idx - 1):
                prev_idx, cur_idx = best_tour[i - 1], best_tour[i]
                for j in range(i + 1, end_idx):
                    # delta over the three changed edge groups (see docstring)
                    delta = (
                        cost_mat[prev_idx][best_tour[j]]
                        - cost_mat[prev_idx][cur_idx]
                    )
                    if j + 1 < n:
                        delta += (
                            cost_mat[cur_idx][best_tour[j + 1]]
                            - cost_mat[best_tour[j]][best_tour[j + 1]]
                        )
                    delta += rev_prefix[j - 1] - rev_prefix[i - 1]
                    new_c = best_cost + delta
                    if new_c < best_cost - 1e-4:
                        best_cost = new_c
                        best_tour = best_tour[:i] + best_tour[i:j + 1][::-1] + best_tour[j + 1:]
                        improved = True
                        break
                if improved:
                    break

        return best_tour

    def _normalize_coords(self, items: List[Any]) -> List[Tuple[float, float]]:
        res: List[Tuple[float, float]] = []
        for item in items:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                res.append((float(item[0]), float(item[1])))
            elif isinstance(item, (DemandPoint, Facility)):
                c = item.geometry["coordinates"]
                res.append((float(c[0]), float(c[1])))
            elif isinstance(item, dict):
                g = item.get("geometry", {})
                c = g.get("coordinates", [0.0, 0.0])
                res.append((float(c[0]), float(c[1])))
        return res
