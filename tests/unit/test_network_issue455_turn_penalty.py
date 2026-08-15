"""Regression tests for issue #455 (P3): turn penalty charged on EVERY edge
(including the departure edge and straight-through continuations).

``build_weight_func`` added ``turn_penalty`` to each edge weight when
cost_field == "travel_time_s", so an N-edge path accrued N penalties: a
3-edge/2-turn path with a 30 s penalty was overcounted by +90 s instead of
+60 s, and a perfectly straight N-edge path was charged N penalties instead
of 0. Length/custom impedance ignored the penalty entirely while
``Route.total_cost`` stopped matching its documented composition.

New semantics (#455):
- the penalty is charged at INTERIOR path vertices whose bearing change
  exceeds the "Continue straight" threshold (25°, same boundary as the
  turn-by-turn directions generator) — the departure edge is never charged;
- route SELECTION honours the same costs via an edge-state (turn-aware)
  Dijkstra/A* search;
- the penalty is a duration and therefore applies only to travel_time_s
  impedance; for length/custom impedance it does not affect selection or
  cost (documented).
"""
import pytest

import networkx as nx

from app.services.network.graph_builder import haversine_distance
from app.services.network.models import Impedance, TravelProfile
from app.services.network.routing import NetworkRoutingService

PENALTY = 30.0
M_PER_DEG = 111_111.11  # latitude meters per degree (fixtures run along lat)


def _chain_graph(coords):
    """Two-way chain through the given (lng, lat) waypoints, 60 km/h."""
    g = nx.DiGraph()
    for i, (x, y) in enumerate(coords):
        g.add_node(f"n{i}", x=x, y=y)
    for i in range(len(coords) - 1):
        a, b = f"n{i}", f"n{i+1}"
        length_m = haversine_distance(coords[i], coords[i + 1])
        time_s = length_m / (60.0 * 1000.0 / 3600.0)
        for u, v in ((a, b), (b, a)):
            g.add_edge(u, v, length_m=length_m, travel_time_s=time_s,
                       speed_kmh=60.0, id=f"e_{u}_{v}", highway_type="residential")
    return g


def _straight_chain(n_edges=4):
    """Perfectly straight collinear chain: n_edges edges, zero turns."""
    d = 100.0 / M_PER_DEG  # 100 m per edge
    return _chain_graph([(116.0 + i * d, 39.0) for i in range(n_edges + 1)])


def _l_shape():
    """Two edges meeting at 90°: exactly one turn."""
    d = 100.0 / M_PER_DEG
    return _chain_graph([(116.0, 39.0), (116.0 + d, 39.0), (116.0 + d, 39.0 + d)])


def _z_shape():
    """Three edges, two 90° turns (the issue's 3-edge/2-turn example)."""
    d = 100.0 / M_PER_DEG
    return _chain_graph([
        (116.0, 39.0),
        (116.0 + d, 39.0),
        (116.0 + d, 39.0 + d),
        (116.0 + 2 * d, 39.0 + d),
    ])


def _route(g, src="n0", dst=None, algorithm="dijkstra", penalty=PENALTY, impedance=None):
    router = NetworkRoutingService()
    imp = impedance or Impedance(name="travel_time_s", turn_penalty_s=penalty)
    dst = dst or f"n{len(g.nodes) - 1}"
    return router.network_shortest_path(
        g, None, src, dst, profile=TravelProfile(name="driving"), impedance=imp,
        algorithm=algorithm,
    )


class TestPenaltyChargedOnlyAtActualTurns:
    def test_straight_path_accrues_zero_penalty(self):
        """N collinear edges: zero penalty (the old code charged N)."""
        g = _straight_chain(4)
        r = _route(g)
        assert r.total_cost == pytest.approx(r.total_time_s, rel=1e-9), (
            f"straight 4-edge path cost {r.total_cost:.1f} s vs time "
            f"{r.total_time_s:.1f} s — accrued {(r.total_cost - r.total_time_s):.0f} s "
            f"of penalties, expected 0"
        )

    def test_single_90_degree_turn_accrues_one_penalty(self):
        g = _l_shape()
        r = _route(g)
        assert r.total_cost == pytest.approx(r.total_time_s + PENALTY, rel=1e-9)

    def test_two_90_degree_turns_accrue_two_penalties(self):
        """The issue's arithmetic: 3-edge/2-turn path → +60 s, not +90 s."""
        g = _z_shape()
        r = _route(g)
        assert r.total_cost == pytest.approx(r.total_time_s + 2 * PENALTY, rel=1e-9), (
            f"3-edge/2-turn path: cost {r.total_cost:.1f} s vs time "
            f"{r.total_time_s:.1f} s + {2 * PENALTY:.0f} s"
        )

    @pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
    def test_astar_and_dijkstra_agree_under_penalty(self, algorithm):
        g = _z_shape()
        r = _route(g, algorithm=algorithm)
        assert r.total_cost == pytest.approx(r.total_time_s + 2 * PENALTY, rel=1e-9)

    def test_departure_edge_not_charged(self):
        """A turn AT the destination vertex is a real turn (charged); the
        departure edge itself never adds a penalty — verified by composition
        on a path whose only turn is at the middle."""
        g = _l_shape()
        r = _route(g)
        assert r.total_cost - r.total_time_s == pytest.approx(PENALTY, abs=1e-9)


class TestPenaltyAffectsSelection:
    def _choice_graph(self):
        """Two parallel routes n0→n4:
        - turny:  2 edges, 10 s total, one 90° turn;
        - smooth: 3 collinear edges, 11 s total, zero turns.
        Without penalty the turny route wins (10 < 11); with a 30 s penalty
        the smooth route wins (10+30 > 11). The old per-edge charge also
        picked smooth here but for the wrong reason (charging 3 vs 2
        penalties); the discriminator is that smooth must win by EXACTLY its
        edge-cost sum, and turny must still win at penalty=0.
        """
        g = nx.DiGraph()
        d = 100.0 / M_PER_DEG
        pts = {
            "n0": (116.0, 39.0),
            "t1": (116.0 + d, 39.0 + d),       # turn waypoint (n0→t1→n4 turns 90°)
            "n4": (116.0 + 2 * d, 39.0),
            "s1": (116.0 + d * 0.6667, 39.0 + 1e-7),  # smooth bypass waypoints
            "s2": (116.0 + d * 1.3333, 39.0 + 1e-7),
        }
        for nid, (x, y) in pts.items():
            g.add_node(nid, x=x, y=y)

        def add(a, b, time_s):
            length_m = haversine_distance(pts[a], pts[b])
            for u, v in ((a, b), (b, a)):
                g.add_edge(u, v, length_m=length_m, travel_time_s=time_s,
                           speed_kmh=60.0, id=f"e_{u}_{v}", highway_type="residential")

        add("n0", "t1", 5.0)
        add("t1", "n4", 5.0)          # 90° turn at t1 (up then right)
        add("n0", "s1", 11.0 / 3)
        add("s1", "s2", 11.0 / 3)
        add("s2", "n4", 11.0 / 3)
        return g

    def test_penalty_switches_route_selection(self):
        g = self._choice_graph()
        # No penalty: the 10 s turny route wins.
        r0 = _route(g, penalty=0.0)
        assert "t1" in r0.path_node_ids

        # With penalty: the 11 s smooth route wins (10 + 30 > 11).
        r1 = _route(g, penalty=PENALTY)
        assert "t1" not in r1.path_node_ids, (
            f"turn-aware selection regression: kept the turny route "
            f"({r1.path_node_ids}) despite a {PENALTY:.0f} s penalty"
        )
        assert r1.total_cost == pytest.approx(11.0, rel=1e-6)

    def test_zero_penalty_route_matches_plain_dijkstra(self):
        g = self._choice_graph()
        r = _route(g, penalty=0.0)
        plain = nx.dijkstra_path_length(
            g, "n0", "n4", weight=lambda u, v, d: d["travel_time_s"]
        )
        assert r.total_cost == pytest.approx(plain, rel=1e-9)


class TestNonTimeCostModes:
    def test_length_impedance_ignores_penalty_and_is_documented(self):
        """Decision (#455): the penalty is a duration, so it applies only to
        travel_time_s impedance; under length impedance it does not affect
        selection or cost."""
        g = _z_shape()
        r = _route(g, impedance=Impedance(name="length_m", turn_penalty_s=PENALTY))
        assert r.total_cost == pytest.approx(r.total_distance_m, rel=1e-9)

    def test_od_tree_costs_unaffected_by_penalty(self):
        """OD-family trees keep pure edge costs (a turn penalty needs the full
        path context); documented cross-tool semantics."""
        from app.services.network.od_matrix import NetworkODMatrixService

        g = _z_shape()
        svc = NetworkODMatrixService()
        from app.services.network.models import NetworkDataset
        ds = NetworkDataset(dataset_id="z", nodes=[], edges=[])
        pairs = svc.network_od_matrix(
            origins=["n0"], destinations=["n3"], graph=g, network_dataset=ds,
            impedance=Impedance(name="travel_time_s", turn_penalty_s=PENALTY),
        )
        total_time = sum(
            g[u][v]["travel_time_s"] for u, v in zip(["n0", "n1", "n2"], ["n1", "n2", "n3"])
        )
        assert pairs[0].travel_time_s == pytest.approx(total_time, abs=0.01)
