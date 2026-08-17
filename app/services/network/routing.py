"""
Network Routing Service Component.
Implements network_shortest_path using Dijkstra / A* over NetworkX DiGraph.
Supports custom impedance metrics, turn penalties, point and polygon barrier avoidance,
route GeoJSON line generation, and turn-by-turn directions.
"""
from __future__ import annotations
import heapq
import itertools
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
from shapely.geometry import LineString, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

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

# Issue #455: a vertex counts as an actual TURN when the incoming/outgoing
# bearings differ by more than this threshold. Same boundary as
# ``_calculate_turn_type``'s "Continue straight" band, so a step is charged a
# turn penalty exactly when the turn-by-turn directions call it a turn.
_TURN_STRAIGHT_THRESHOLD_DEG = 25.0


def build_weight_func(cost_field: str):
    """Edge weight function factory shared by routing and OD-matrix analyses.

    Cost semantics (#455): the weight is the pure edge cost — the impedance
    field adjusted for barrier factors. TURN PENALTIES ARE NOT PART OF IT:

    an edge-local weight cannot see the preceding edge, so the old per-edge
    ``+turn_penalty`` charged the departure edge and every straight-through
    continuation, overcounting each path by ~1 penalty and biasing selection
    toward fewer-edge paths. Turn penalties are now applied only where the
    full path is known:

    * point-to-point routing (``network_shortest_path``) charges the penalty
      at interior vertices whose bearing change exceeds the straight threshold
      — both during search (edge-state Dijkstra/A*, see
      ``_turn_aware_shortest_path``) and when reporting ``total_cost``;
    * OD-family trees (closest facility / VRP / accessibility) resolve pure
      edge costs: a turn penalty depends on path context that a shortest-path
      tree does not have, so penalties do not influence batch tree selection.

    The penalty is a DURATION and therefore applies only to the
    ``travel_time_s`` impedance; under length/custom impedance it does not
    affect selection or cost.
    """
    def weight_func(u: Any, v: Any, edge_data: Dict[str, Any]) -> float:
        base_w = edge_data.get(cost_field, edge_data.get("length_m", 1.0))
        if base_w is None or base_w <= 0:
            base_w = 0.001
        barrier_factor = edge_data.get("_barrier_factor", 1.0)
        w = base_w * barrier_factor
        return max(0.0001, float(w))
    return weight_func


class NetworkRoutingService:
    """
    Service for calculating shortest path routes over spatial network graphs.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()

    # --- GIS-01: snapped-point routing (deep-audit round 2) ---
    #
    # Previously, a (lng, lat) origin/destination was resolved to the NEAREST
    # NODE (an endpoint of the snapped edge), so every route silently began/ended
    # up to a full edge-length away from the true location. The snapped point
    # (with fraction_along_edge) was computed but never used.
    #
    # Now the snapped edge is SPLIT at the snapped point: a virtual node is
    # inserted on a working copy of the graph, the edge is divided into two
    # sub-edges with proportionally-divided length_m / travel_time_s, and the
    # route runs node-to-node through the virtual node. The returned geometry
    # therefore starts/ends exactly at the snapped location.

    _VIRTUAL_NODE_PREFIX = "vt_"

    def _split_edge_at_fraction(
        self,
        graph: nx.DiGraph,
        edge_u: Any,
        edge_v: Any,
        fraction: float,
        snapped_coord: Tuple[float, float],
    ) -> str:
        """Split edge (u→v) at ``fraction`` [0,1], inserting a virtual node.

        Mutates ``graph`` in place (callers pass a working copy). Handles both
        directions when the edge is bidirectional. Returns the virtual node id.

        ``fraction`` is defined along the (edge_u→edge_v) ORIENTATION. Issue
        #446: the reverse graph edge (edge_v→edge_u) has reversed geometry, so
        the same physical split point sits at ``1 - fraction`` along it —
        applying ``fraction`` to both orientations swapped the sub-edge
        lengths/times and produced reverse sub-geometries that missed the
        virtual node (a mid-edge route to the near endpoint reported (1−f)·L
        instead of f·L).

        Handles the case where the target edge was ALREADY split by an earlier
        virtual-node insertion (origin and destination snapping to the same
        edge): the walk follows the sub-edge chain from u to v, accumulates
        length, and splits at the position matching the fraction of the ORIGINAL
        edge, so the second split lands on the correct sub-edge.
        """
        vt_id = f"{self._VIRTUAL_NODE_PREFIX}{uuid.uuid4().hex[:12]}"
        graph.add_node(vt_id, x=snapped_coord[0], y=snapped_coord[1], properties={})

        fraction = max(0.0, min(1.0, fraction))

        # Each graph edge's own geometry runs from its u to its v (the builder
        # stores a reversed LineString for the v→u edge), so the fraction must
        # be re-expressed in each orientation's own frame: 1−f for the reverse.
        for u, v, frac in (
            (edge_u, edge_v, fraction),
            (edge_v, edge_u, 1.0 - fraction),
        ):
            if not graph.has_edge(u, v):
                continue
            data = graph[u][v]
            length_m = float(data.get("length_m", 0.0))
            time_s = float(data.get("travel_time_s", 0.0))

            sub_len_1 = length_m * frac
            sub_len_2 = length_m * (1.0 - frac)
            sub_time_1 = time_s * frac
            sub_time_2 = time_s * (1.0 - frac)

            # Subdivide the geometry (if present) at the split point.
            geom_dict = data.get("geometry")
            geom1 = None
            geom2 = None
            if geom_dict:
                try:
                    geom = shape(geom_dict)
                    if isinstance(geom, LineString):
                        coords = list(geom.coords)
                        split_pt = geom.interpolate(frac, normalized=True)
                        # Split the coordinate sequence at the projected split
                        # point: vertices before it go to sub1, the split point
                        # joins them, and the remainder goes to sub2.
                        sub1 = [coords[0]]
                        inserted = False
                        for i in range(1, len(coords)):
                            if not inserted:
                                seg = LineString([coords[i - 1], coords[i]])
                                if split_pt.distance(seg) < 1e-9:
                                    sub1.append((split_pt.x, split_pt.y))
                                    inserted = True
                                    break
                                sub1.append(coords[i])
                            else:
                                break
                        if len(sub1) >= 2:
                            geom1 = LineString(sub1)
                        sub2 = [sub1[-1]] + list(coords[len(sub1) - 1:]) if len(sub1) >= 1 else list(coords)
                        if len(sub2) >= 2:
                            geom2 = LineString(sub2)
                except Exception:
                    geom1 = None
                    geom2 = None

            # Remove the original edge and add the two sub-edges.
            graph.remove_edge(u, v)
            for sub_u, sub_v, sub_len, sub_time, sub_geom in (
                (u, vt_id, sub_len_1, sub_time_1, geom1),
                (vt_id, v, sub_len_2, sub_time_2, geom2),
            ):
                edge_attrs = dict(data)
                edge_attrs["u"] = sub_u
                edge_attrs["v"] = sub_v
                edge_attrs["length_m"] = sub_len
                edge_attrs["travel_time_s"] = sub_time
                edge_attrs["id"] = f"{data.get('id', 'e')}_sp{vt_id[-6:]}_{sub_u}_{sub_v}"
                if sub_geom is not None:
                    edge_attrs["geometry"] = {
                        "type": "LineString",
                        "coordinates": [list(c) for c in sub_geom.coords],
                    }
                graph.add_edge(sub_u, sub_v, **edge_attrs)

        return vt_id

    def _split_edge_chain(
        self,
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        edge_id: Union[int, str],
        fraction: float,
        snapped_coord: Tuple[float, float],
    ) -> Optional[str]:
        """Split the edge identified by ``edge_id``, even if already split.

        GIS-01 edge case: when origin and destination snap to the SAME edge,
        the first split replaces the original (u→v) edge with a sub-edge chain
        (u→vt1→v). The second snap's fraction refers to the ORIGINAL edge, so
        we walk the chain from u toward v, accumulate length, locate the
        sub-edge containing ``fraction * orig_length``, and split THAT
        sub-edge at the local fraction. Returns the virtual node id, or None
        if the chain cannot be walked.
        """
        edge_info = self._find_edge_by_id(network_dataset, edge_id)
        if edge_info is None:
            return None
        orig_u, orig_v = edge_info

        # Total original length (from the dataset edge, authoritative).
        orig_length = 0.0
        for edge in network_dataset.edges:
            if str(edge.id) == str(edge_id):
                orig_length = float(edge.length_m or 0.0)
                break

        fraction = max(0.0, min(1.0, fraction))

        # The fraction is measured along the dataset edge's (orig_u→orig_v)
        # orientation. Walking from orig_v (issue #446) the same physical
        # position sits at 1−f, so each direction gets its own target distance
        # and its own fraction when the original edge is still unsplit.
        for start_u, start_v, dir_frac in (
            (orig_u, orig_v, fraction),
            (orig_v, orig_u, 1.0 - fraction),
        ):
            target_dist = orig_length * dir_frac
            if not graph.has_edge(start_u, start_v) and start_u not in graph:
                continue
            # If the original edge still exists directly, split it — the common
            # first-snap case.
            if graph.has_edge(start_u, start_v):
                return self._split_edge_at_fraction(
                    graph, start_u, start_v, dir_frac, snapped_coord
                )
            # Otherwise walk the sub-edge chain created by a previous split.
            # REVIEWER BLOCKING FIX: the previous walk used nx.has_path on
            # arbitrary successors, which at a junction follows ANY road that
            # can reach start_v — inserting the virtual node on the wrong edge.
            # The chain consists ONLY of the target node and virtual nodes
            # (vt_*); never follow a real junction neighbor.
            accumulated = 0.0
            current = start_u
            prev: Any = None
            while current != start_v:
                nbrs = [
                    n for n in graph.successors(current)
                    if n != prev
                    and (n == start_v or str(n).startswith(self._VIRTUAL_NODE_PREFIX))
                ]
                if not nbrs:
                    break  # chain broken in this direction; try the other
                nxt = nbrs[0]
                data = graph[current][nxt]
                e_len = float(data.get("length_m", 0.0))
                if accumulated + e_len >= target_dist:
                    local_frac = (target_dist - accumulated) / e_len if e_len > 0 else 0.0
                    return self._split_edge_at_fraction(
                        graph, current, nxt, local_frac, snapped_coord
                    )
                accumulated += e_len
                prev = current
                current = nxt
        return None

    def _resolve_with_snap(
        self,
        target: Union[Tuple[float, float], str, PointSnappingResult],
        network_dataset: NetworkDataset,
        graph: Optional[nx.DiGraph] = None,
    ) -> Tuple[str, str, Optional[PointSnappingResult]]:
        """Resolve a target into (graph_node_id, label, snap_result).

        For (lng, lat) tuples this snaps and — when a working ``graph`` is
        provided — inserts a virtual node at the snapped point so the route
        truly starts/ends there (GIS-01). For node ids and pre-snapped
        PointSnappingResults the existing node is used (callers that already
        have a snapped result should pass it to avoid a second snap).
        """
        if isinstance(target, str):
            return target, target, None
        if isinstance(target, PointSnappingResult):
            if graph is not None and target.nearest_edge_id is not None:
                # Insert the virtual node on the graph copy so routing honors
                # the snapped location even when a pre-snapped result is given.
                if 1e-6 < target.fraction_along_edge < 1.0 - 1e-6:
                    vt = self._split_edge_chain(
                        graph, network_dataset, target.nearest_edge_id,
                        target.fraction_along_edge, target.snapped_point,
                    )
                    if vt is not None:
                        return vt, f"pt_{target.snapped_point[0]:.4f}_{target.snapped_point[1]:.4f}", target
            return str(target.nearest_node_id), (
                f"pt_{target.snapped_point[0]:.4f}_{target.snapped_point[1]:.4f}"
            ), target
        if isinstance(target, (tuple, list)) and len(target) >= 2:
            snap_res = self.snapper.snap_point((float(target[0]), float(target[1])), network_dataset)
            if graph is not None and snap_res.nearest_edge_id is not None:
                # Skip degenerate splits at edge endpoints (fraction ≈ 0/1).
                if 1e-6 < snap_res.fraction_along_edge < 1.0 - 1e-6:
                    vt = self._split_edge_chain(
                        graph, network_dataset, snap_res.nearest_edge_id,
                        snap_res.fraction_along_edge, snap_res.snapped_point,
                    )
                    if vt is not None:
                        return vt, f"pt_{target[0]:.4f}_{target[1]:.4f}", snap_res
            return str(snap_res.nearest_node_id), f"pt_{target[0]:.4f}_{target[1]:.4f}", snap_res
        raise ValueError(f"Invalid target location format: {target}")

    @staticmethod
    def _find_edge_by_id(
        network_dataset: NetworkDataset, edge_id: Union[int, str]
    ) -> Optional[Tuple[Any, Any]]:
        """Return (u, v) node ids of the edge with the given id, or None."""
        for edge in network_dataset.edges:
            if str(edge.id) == str(edge_id):
                return edge.u, edge.v
        return None

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
        # GIS-01: for coordinate / PointSnappingResult inputs, work on a copy of
        # the graph with virtual nodes inserted at the snapped locations, so the
        # route truly starts/ends at the snapped point rather than at an edge
        # endpoint. Node-id inputs (str) use the graph as-is (no copy).
        needs_snap = isinstance(origin, (tuple, list, PointSnappingResult)) or isinstance(
            destination, (tuple, list, PointSnappingResult)
        )
        if needs_snap:
            graph_work = graph.copy()
        else:
            graph_work = graph

        start_node_id, origin_id_str, _ = self._resolve_with_snap(
            origin, network_dataset, graph=graph_work
        )
        end_node_id, dest_id_str, _ = self._resolve_with_snap(
            destination, network_dataset, graph=graph_work
        )

        # Apply barriers to graph copy (when the graph is already a private
        # working copy from snapping, barriers apply in place — no second copy).
        graph_view = self._apply_barriers(graph_work, barriers, copy_graph=not needs_snap)

        # Determine weight field
        cost_field = "travel_time_s"
        if impedance and impedance.name:
            cost_field = impedance.name
        elif profile and profile.impedance_field:
            cost_field = profile.impedance_field

        turn_penalty = impedance.turn_penalty_s if impedance else 0.0
        if profile and profile.turn_penalty_s:
            turn_penalty = max(turn_penalty, profile.turn_penalty_s)

        weight_func = build_weight_func(cost_field)

        # Issue #455: with a time impedance and a turn penalty, the cost of a
        # path depends on the EDGES it follows (turns at shared vertices), so
        # the search runs over edge states instead of nodes. Without a penalty
        # this reduces to the plain node search.
        use_turn_aware = turn_penalty > 0 and cost_field == "travel_time_s"

        # Run Pathfinding
        try:
            if use_turn_aware:
                heuristic = None
                if algorithm.lower() == "astar":
                    min_cost_per_m = self._min_cost_per_meter(graph_view, weight_func)
                    if min_cost_per_m is not None:
                        def heuristic(u: Any, v: Any) -> float:
                            u_data = graph_view.nodes[u]
                            v_data = graph_view.nodes[v]
                            dist_m = haversine_distance(
                                (u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])
                            )
                            return dist_m * min_cost_per_m
                path_nodes = self._turn_aware_shortest_path(
                    graph_view, start_node_id, end_node_id,
                    weight_func, turn_penalty, heuristic=heuristic,
                )
            elif algorithm.lower() == "astar":
                # Issue #447: the heuristic previously divided the
                # straight-line distance by the PROFILE default speed
                # (driving 40 km/h / walking 4.8 km/h). The builder assigns
                # per-edge speeds of 60-100+ km/h with no clamp, so on any
                # network with edges faster than the default the heuristic
                # OVERESTIMATED the remaining cost — inadmissible, and A*
                # could settle the goal via a suboptimal path (measured +2.0%
                # travel time; ~8-20x overestimate for walking profiles).
                #
                # The bound is now derived from the graph itself: the minimum
                # edge cost per meter of length under the active weight
                # function. Any u→v path has network length ≥ haversine(u, v)
                # and every meter costs at least that ratio, so
                # h = haversine * ratio ≤ true remaining cost for ANY cost
                # field (time, length, custom), staying conservative.
                min_cost_per_m = self._min_cost_per_meter(graph_view, weight_func)

                def heuristic(u: Any, v: Any) -> float:
                    if min_cost_per_m is None:
                        return 0.0
                    u_data = graph_view.nodes[u]
                    v_data = graph_view.nodes[v]
                    dist_m = haversine_distance((u_data["x"], u_data["y"]), (v_data["x"], v_data["y"]))
                    return dist_m * min_cost_per_m

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
        profile_name = profile.name if profile else "driving"
        route_id = f"route_{origin_id_str}_{dest_id_str}"
        return self.build_route_from_path(
            graph_view, path_nodes,
            origin_label=origin_id_str, destination_label=dest_id_str,
            profile_name=profile_name, route_id=route_id, weight_func=weight_func,
            turn_penalty=turn_penalty if use_turn_aware else 0.0,
        )

    @staticmethod
    def _min_cost_per_meter(graph: nx.DiGraph, weight_func) -> Optional[float]:
        """Smallest per-meter edge cost under ``weight_func`` (issue #447).

        Used as the A* heuristic scale: h(u, v) = haversine(u, v) * ratio is a
        guaranteed lower bound of the remaining path cost because network path
        length ≥ straight-line distance and each meter of any edge costs at
        least the minimum ratio. Returns None when the graph has no usable
        positive lengths (callers fall back to h = 0, i.e. Dijkstra behavior —
        always admissible).
        """
        min_ratio: Optional[float] = None
        for u, v, data in graph.edges(data=True):
            length_m = data.get("length_m")
            if not isinstance(length_m, (int, float)) or length_m <= 0:
                continue
            ratio = weight_func(u, v, data) / float(length_m)
            if min_ratio is None or ratio < min_ratio:
                min_ratio = ratio
        return min_ratio

    @staticmethod
    def _bearing_change_deg(graph: nx.DiGraph, node_a: Any, node_b: Any, node_c: Any) -> float:
        """Absolute bearing change (deg, [0, 180]) of the a→b→c polyline."""
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
        return abs(diff_deg)

    def _turn_aware_shortest_path(
        self,
        graph: nx.DiGraph,
        start: Any,
        goal: Any,
        weight_func,
        turn_penalty: float,
        heuristic=None,
    ) -> List[Any]:
        """Edge-state Dijkstra/A* charging ``turn_penalty`` at real turns (#455).

        The cost of ENTERING an edge depends on the edge it follows (the turn
        at the shared vertex), so plain node-state search cannot apply turn
        costs. Search states are therefore directed edges ``(u, v)``:
        transitioning ``(a, b) → (b, c)`` costs ``w(b→c)`` plus the penalty
        when the bearing change at ``b`` exceeds the straight threshold. The
        DEPARTURE edge follows no other edge, so it never carries a penalty.

        ``heuristic(u, v)`` (optional, must be admissible on node pairs, e.g.
        the #447 min-cost-per-meter bound) is applied to each state's head
        node; penalties only ADD cost, so admissibility is preserved.
        """
        if start not in graph:
            raise nx.NodeNotFound(f"Node {start} not in graph")
        if goal not in graph:
            raise nx.NodeNotFound(f"Node {goal} not in graph")
        if start == goal:
            return [start]

        best_g: Dict[Tuple[Any, Any], float] = {}
        parent: Dict[Tuple[Any, Any], Optional[Tuple[Any, Any]]] = {}
        counter = itertools.count()
        heap: List[Tuple[float, int, float, Tuple[Any, Any]]] = []

        def push(state: Tuple[Any, Any], g: float) -> None:
            best_g[state] = g
            h = heuristic(state[1], goal) if heuristic is not None else 0.0
            heapq.heappush(heap, (g + h, next(counter), g, state))

        # Departure edges: no penalty (no preceding edge).
        for nbr, edata in graph[start].items():
            push((start, nbr), weight_func(start, nbr, edata))

        goal_state: Optional[Tuple[Any, Any]] = None
        while heap:
            _, _, g, state = heapq.heappop(heap)
            if g > best_g.get(state, float("inf")):
                continue  # stale heap entry
            u, v = state
            if v == goal:
                goal_state = state
                break
            for nbr, edata in graph[v].items():
                ng = g + weight_func(v, nbr, edata)
                if self._bearing_change_deg(graph, u, v, nbr) > _TURN_STRAIGHT_THRESHOLD_DEG:
                    ng += turn_penalty
                if ng < best_g.get((v, nbr), float("inf")):
                    parent[(v, nbr)] = state
                    push((v, nbr), ng)

        if goal_state is None:
            raise nx.NetworkXNoPath(f"Node {goal} not reachable from {start}")

        states: List[Tuple[Any, Any]] = []
        s: Optional[Tuple[Any, Any]] = goal_state
        while s is not None:
            states.append(s)
            s = parent.get(s)
        states.reverse()
        return [states[0][0]] + [st[1] for st in states]

    def build_route_from_path(
        self,
        graph: nx.DiGraph,
        path_nodes: List[Any],
        origin_label: str,
        destination_label: str,
        profile_name: str,
        route_id: str,
        weight_func,
        turn_penalty: float = 0.0,
    ) -> Route:
        """Build a Route (geometry, totals, directions) from a node path.

        Shared by ``network_shortest_path`` and the batch OD-matrix based
        analyses (closest facility / VRP), so path → route shaping stays
        identical everywhere and routes can be reconstructed from
        multi-source Dijkstra predecessor trees without per-call graph copies.

        Cost composition (#455): ``total_cost`` = Σ per-edge ``weight_func``
        costs + ``turn_penalty`` at each interior vertex whose bearing change
        exceeds the straight threshold. For an unbarriered ``travel_time_s``
        impedance that equals ``total_time_s + actual_turn_penalties`` — the
        documented composition. Callers resolving pure OD trees pass the
        default 0 (batch trees carry no turn penalties; see
        ``build_weight_func``).
        """
        path_edges: List[str] = []
        route_coords: List[Tuple[float, float]] = []
        total_dist_m = 0.0
        total_time_s = 0.0
        total_cost = 0.0

        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i + 1]
            edge_data = graph[u][v]
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
                u_data = graph.nodes[u]
                v_data = graph.nodes[v]
                if not route_coords:
                    route_coords.append((u_data["x"], u_data["y"]))
                route_coords.append((v_data["x"], v_data["y"]))

        # #455: charge the turn penalty at interior vertices that are actual
        # turns (bearing change > the straight threshold). The departure edge
        # has no preceding edge and straight-through vertices are free.
        if turn_penalty > 0 and len(path_nodes) >= 3:
            for i in range(1, len(path_nodes) - 1):
                change = self._bearing_change_deg(graph, path_nodes[i - 1], path_nodes[i], path_nodes[i + 1])
                if change > _TURN_STRAIGHT_THRESHOLD_DEG:
                    total_cost += turn_penalty

        directions = self._generate_directions(graph, path_nodes)

        return Route(
            route_id=route_id,
            origin_id=origin_label,
            destination_id=destination_label,
            profile_name=profile_name,
            total_distance_m=total_dist_m,
            total_time_s=total_time_s,
            total_cost=total_cost,
            geometry={"type": "LineString", "coordinates": route_coords},
            path_node_ids=list(path_nodes),
            path_edge_ids=path_edges,
            directions=directions,
        )

    def _resolve_node(
        self,
        target: Union[Tuple[float, float], str, PointSnappingResult],
        network_dataset: NetworkDataset,
    ) -> Tuple[str, str]:
        """Resolves target input into (graph_node_id, identifier_label).

        DEPRECATED for routing: kept for legacy callers that resolve without a
        working graph. New code should use ``_resolve_with_snap`` which inserts
        virtual nodes at snapped locations (GIS-01).
        """
        return self._resolve_with_snap(target, network_dataset, graph=None)[:2]

    def _apply_barriers(
        self,
        graph: nx.DiGraph,
        barriers: Optional[List[Barrier]],
        copy_graph: bool = True,
    ) -> nx.DiGraph:
        """Applies barriers to a graph copy, removing or penalizing blocked edges.

        ``copy_graph=False`` lets callers that ALREADY hold a private working
        copy (coordinate snapping / virtual-node insertion) skip the redundant
        second copy — PERF (#540): a routing call with barriers used to pay TWO
        full graph copies (snap copy + barrier copy). Never pass ``False`` for
        a graph the caller still owns.
        """
        # PERF-03: the common case (no barriers) previously paid a full graph
        # deep-copy (nodes + per-edge geometry dicts) per routing call. Return
        # the original graph directly when there is nothing to apply.
        if not barriers:
            return graph
        graph_copy = graph.copy() if copy_graph else graph

        # PERF (#540): the previous implementation scanned EVERY edge per
        # barrier and rebuilt each edge geometry with ``shape()`` (B×E GEOS
        # work, plus B full geometry reconstructions). Now one STRtree is built
        # over the edge geometries ONCE per call and each barrier queries only
        # bbox-candidate edges, with the barrier prepared for fast repeated
        # predicates. O(B×E) → O(E log E + B·k·GEOS).
        edge_items: List[Tuple[Any, Any]] = []
        edge_geoms: List[Any] = []
        for u, v, data in graph_copy.edges(data=True):
            edge_geom_dict = data.get("geometry")
            try:
                if edge_geom_dict:
                    edge_geoms.append(shape(edge_geom_dict))
                else:
                    u_data = graph_copy.nodes[u]
                    v_data = graph_copy.nodes[v]
                    edge_geoms.append(
                        LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
                    )
            except Exception:
                # Edge without usable geometry/node coords: cannot be tested;
                # the old code would have raised on such an edge, so skipping
                # it strictly widens robustness without changing valid inputs.
                continue
            edge_items.append((u, v))
        edge_tree = STRtree(edge_geoms)

        for barrier in barriers:
            raw_geom = shape(barrier.geometry)
            b_geom = prep(raw_geom)
            factor = barrier.impedance_factor

            # Identify edges intersecting barrier (penalize their impedance).
            edges_to_penalize = []
            for pos in edge_tree.query(raw_geom):
                idx = int(pos)
                u, v = edge_items[idx]
                if not graph_copy.has_edge(u, v):
                    # Edge removed by an earlier blocking barrier — same
                    # semantics as the old per-barrier rescan (removed edges are
                    # gone from the working copy and cannot be re-penalized).
                    continue
                if b_geom.intersects(edge_geoms[idx]):
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
        diff_deg = self._bearing_change_deg(graph, node_a, node_b, node_c)
        # bearing_change is absolute; recover the signed direction for the
        # left/right classification from the raw bearings.
        data_a = graph.nodes[node_a]
        data_b = graph.nodes[node_b]
        data_c = graph.nodes[node_c]
        v1 = (data_b["x"] - data_a["x"], data_b["y"] - data_a["y"])
        v2 = (data_c["x"] - data_b["x"], data_c["y"] - data_b["y"])
        signed = math.degrees(math.atan2(v2[1], v2[0]) - math.atan2(v1[1], v1[0]))
        while signed > 180:
            signed -= 360
        while signed <= -180:
            signed += 360

        if diff_deg <= 25:
            return "Continue straight"
        elif 25 < signed <= 65:
            return "Turn slight right"
        elif 65 < signed <= 125:
            return "Turn right"
        elif signed > 125:
            return "Make a U-turn right"
        elif -65 <= signed < -25:
            return "Turn slight left"
        elif -125 <= signed < -65:
            return "Turn left"
        else:
            return "Make a U-turn left"
