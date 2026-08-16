"""Regression tests for issue #447 (P2): A* heuristic used the profile default
speed (40 km/h driving / 4.8 km/h walking), which is INADMISSIBLE whenever the
graph contains edges faster than the profile default (the builder assigns
60-100+ km/h on faster roads with no clamp). An inadmissible heuristic lets A*
settle the goal via a suboptimal path — measured +2.0% travel time, worse for
walking profiles on road networks (~8-20x overestimate).

The fix derives the heuristic bound from the graph itself: the minimum edge
cost per meter of edge length under the active weight function, so
h(u, v) = haversine(u, v) * min_cost_per_meter <= true remaining cost for any
cost field (travel_time_s, length_m, custom).
"""
import random

import pytest

import networkx as nx

from app.services.network.graph_builder import haversine_distance
from app.services.network.models import TravelProfile
from app.services.network.routing import NetworkRoutingService


def _add_two_way(g, a, b, speed_kmh):
    length_m = haversine_distance((g.nodes[a]["x"], g.nodes[a]["y"]), (g.nodes[b]["x"], g.nodes[b]["y"]))
    time_s = length_m / (speed_kmh * 1000.0 / 3600.0)
    for u, v in ((a, b), (b, a)):
        g.add_edge(u, v, length_m=length_m, travel_time_s=time_s,
                   speed_kmh=speed_kmh, highway_type="x", id=f"e_{u}_{v}")


def _express_detour_graph():
    """Collinear chain with a backward express detour (all positions in meters
    relative to lng 116.0 at lat 39.0; ~100 m per 0.00115 deg).

    Slow direct chain: A→B→C→D→G at 40 km/h = 36.0 s.
    Express: A→X (backward 100 m @150) + X→Z (700 m @200) + Z→G (200 m @150)
           = 2.4 + 12.6 + 4.8 = 19.8 s.

    With the buggy v=40 heuristic, h(X) alone (500 m / 40 km/h = 45 s) makes
    the express frontier f=47.4, so A* settles G via the slow chain (36 s)
    before ever expanding X.
    """
    def at(m):
        return f"n{m}"
    coords = {m: (116.0 + m * 1.152e-5, 39.0) for m in (-100, 0, 100, 200, 300, 400, 600)}
    g = nx.DiGraph()
    for m, (x, y) in coords.items():
        g.add_node(at(m), x=x, y=y)

    for a, b in ((0, 100), (100, 200), (200, 300), (300, 400)):
        _add_two_way(g, at(a), at(b), speed_kmh=40.0)
    _add_two_way(g, at(0), at(-100), speed_kmh=150.0)     # A -> X (backward)
    _add_two_way(g, at(-100), at(600), speed_kmh=200.0)   # X -> Z express
    _add_two_way(g, at(600), at(400), speed_kmh=150.0)    # Z -> G (backward)
    return g, at(0), at(400)


class TestAStarAdmissibleHeuristic:
    def test_astar_matches_dijkstra_on_express_detour(self):
        """Handcrafted adversarial case: the optimal route takes a geometrically
        longer but faster detour that the profile-default-speed heuristic
        rejects outright."""
        g, src, dst = _express_detour_graph()
        router = NetworkRoutingService()
        profile = TravelProfile(name="driving")  # default speed 40 km/h

        dij = router.network_shortest_path(g, None, src, dst, profile=profile, algorithm="dijkstra")
        ast = router.network_shortest_path(g, g, src, dst, profile=profile, algorithm="astar")

        assert dij.total_cost == pytest.approx(19.8, abs=0.2), (
            f"dijkstra cost {dij.total_cost:.2f} s — fixture drifted from the "
            f"19.8 s express route"
        )
        assert ast.total_cost == pytest.approx(dij.total_cost, rel=1e-9), (
            f"A* returned {ast.total_cost:.2f} s vs Dijkstra {dij.total_cost:.2f} s — "
            f"inadmissible heuristic produced a suboptimal route"
        )

    def test_walking_profile_on_fast_road_network(self):
        """Walking profile (4.8 km/h default) over a road network with 60-100
        km/h edge speeds: the old heuristic overestimated ~10-20x."""
        g, src, dst = _express_detour_graph()
        router = NetworkRoutingService()
        profile = TravelProfile(name="walking", impedance_field="travel_time_s")

        dij = router.network_shortest_path(g, None, src, dst, profile=profile, algorithm="dijkstra")
        ast = router.network_shortest_path(g, g, src, dst, profile=profile, algorithm="astar")
        assert ast.total_cost == pytest.approx(dij.total_cost, rel=1e-9)


def _random_graph(seed: int, n_nodes: int = 60) -> tuple[nx.DiGraph, list, list]:
    """Random geometric graph with strongly mixed edge speeds (20-140 km/h —
    most far above the 40 km/h profile default)."""
    rng = random.Random(seed)
    g = nx.DiGraph()
    nodes = []
    for i in range(n_nodes):
        x = 116.0 + rng.uniform(0.0, 0.05)
        y = 39.0 + rng.uniform(0.0, 0.05)
        nid = f"n{i}"
        g.add_node(nid, x=x, y=y)
        nodes.append(nid)

    # Ensure connectivity with a random spanning chain, then add random chords.
    order = nodes[:]
    rng.shuffle(order)
    for a, b in zip(order, order[1:]):
        _add_two_way(g, a, b, speed_kmh=rng.uniform(20.0, 140.0))
    for _ in range(n_nodes * 2):
        a, b = rng.sample(nodes, 2)
        if not g.has_edge(a, b):
            _add_two_way(g, a, b, speed_kmh=rng.uniform(20.0, 140.0))

    ods = [(rng.sample(nodes, 2)) for _ in range(25)]
    return g, nodes, ods


class TestAStarDifferentialProperty:
    @pytest.mark.parametrize("seed", [1, 7, 42, 1337, 90210])
    def test_astar_cost_equals_dijkstra_cost_randomized(self, seed):
        """Property: on randomized graphs with mixed speeds (including many
        edges faster than the profile default), A* must return exactly the
        Dijkstra cost for every OD pair."""
        g, nodes, ods = _random_graph(seed)
        router = NetworkRoutingService()
        profile = TravelProfile(name="driving")

        mismatches = []
        for src, dst in ods:
            dij = router.network_shortest_path(g, None, src, dst, profile=profile, algorithm="dijkstra")
            ast = router.network_shortest_path(g, g, src, dst, profile=profile, algorithm="astar")
            if abs(ast.total_cost - dij.total_cost) > 1e-6 * max(1.0, dij.total_cost):
                mismatches.append((src, dst, dij.total_cost, ast.total_cost))
        assert not mismatches, (
            f"seed={seed}: A* suboptimal on {len(mismatches)}/25 OD pairs "
            f"(dijkstra vs astar): {mismatches[:5]}"
        )

    @pytest.mark.parametrize("seed", [3, 11])
    def test_astar_length_impedance_equals_dijkstra(self, seed):
        """Same differential under length impedance (h = dist * min ratio)."""
        g, nodes, ods = _random_graph(seed, n_nodes=40)
        router = NetworkRoutingService()
        profile = TravelProfile(name="driving", impedance_field="length_m")

        for src, dst in ods:
            dij = router.network_shortest_path(g, None, src, dst, profile=profile, algorithm="dijkstra")
            ast = router.network_shortest_path(g, g, src, dst, profile=profile, algorithm="astar")
            assert ast.total_cost == pytest.approx(dij.total_cost, rel=1e-9), (
                f"seed={seed} {src}->{dst}: astar {ast.total_cost} vs dijkstra {dij.total_cost}"
            )


class TestHeuristicBound:
    def test_heuristic_never_exceeds_true_cost(self):
        """h(u, goal) <= shortest-path cost for a sample of (u, goal) pairs."""
        g, nodes, ods = _random_graph(99, n_nodes=50)
        router = NetworkRoutingService()

        from app.services.network.routing import build_weight_func
        wf = build_weight_func("travel_time_s")

        mrpm = router._min_cost_per_meter(g, wf)
        assert mrpm is not None and mrpm > 0

        for goal in nodes[:10]:
            dists = nx.single_source_dijkstra_path_length(g.reverse(copy=True), goal, weight=wf)
            for u in nodes[:10]:
                h = haversine_distance(
                    (g.nodes[u]["x"], g.nodes[u]["y"]),
                    (g.nodes[goal]["x"], g.nodes[goal]["y"]),
                ) * mrpm
                if u in dists:
                    assert h <= dists[u] + 1e-9, (
                        f"h({u},{goal})={h:.3f} exceeds true cost {dists[u]:.3f}"
                    )
