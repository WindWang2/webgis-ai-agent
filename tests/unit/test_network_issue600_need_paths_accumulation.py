"""Regression tests for issue #600 (P2): the OD ``need_paths=True`` branch
materialized every node's full path AND then re-walked each path edge-by-edge
in Python to re-sum distance/time — O(sum|path|) ≈ O(V²) per origin (the
measured-48.9s@8k-nodes mode #449 already fixed for the cost-only variant).
The fix accumulates distance/time along each node's predecessor edge in O(V)
per origin, and the snap-index build no longer linear-scans all nodes per
geometry-less edge (O(E·N)).

Acceptance criterion: 8k-node chain closest-facility < 1 s (measured ~0.7 s
on the dev box; the bound below leaves CI headroom).
"""
import math
import random
import time

import pytest

import networkx as nx

from app.services.network.models import NetworkDataset, Node, Edge, DemandPoint, Facility
from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.od_matrix import NetworkODMatrixService
from app.services.network.routing import build_weight_func


def _chain(n):
    """n-node bidirectional chain; edges carry NO geometry so the snapping
    index must fall back to the node-coordinate path (issue #600's O(E·N)
    node-scan hotspot)."""
    g = nx.DiGraph()
    for i in range(n):
        g.add_node(f"n{i}", x=116.0 + i * 0.0001, y=39.0)
    for i in range(n - 1):
        for u, v in ((f"n{i}", f"n{i + 1}"), (f"n{i + 1}", f"n{i}")):
            g.add_edge(u, v, length_m=8.3, travel_time_s=0.5, id=f"e{i}")
    nodes = [Node(id=n, x=d["x"], y=d["y"]) for n, d in g.nodes(data=True)]
    edges = [
        Edge(id=str(i), u=u, v=v, length_m=d["length_m"]) for i, (u, v, d) in enumerate(g.edges(data=True))
    ]
    return g, NetworkDataset(dataset_id="chain", nodes=nodes, edges=edges)


def _random_graph(seed=600):
    rng = random.Random(seed)
    g = nx.DiGraph()
    for i in range(120):
        g.add_node(f"n{i}", x=116.0 + rng.uniform(0, 0.05), y=39.0 + rng.uniform(0, 0.05))
    ids = list(g.nodes)
    for _ in range(480):
        a, b = rng.sample(ids, 2)
        if g.has_edge(a, b):
            continue
        dx = g.nodes[a]["x"] - g.nodes[b]["x"]
        dy = g.nodes[a]["y"] - g.nodes[b]["y"]
        length_m = math.hypot(dx * 85_000, dy * 111_000)
        travel_time_s = length_m / rng.uniform(8.0, 25.0)
        for u, v in ((a, b), (b, a)):
            g.add_edge(u, v, length_m=length_m, travel_time_s=travel_time_s, id=f"e_{u}_{v}")
    nodes = [Node(id=n, x=d["x"], y=d["y"]) for n, d in g.nodes(data=True)]
    edges = [
        Edge(id=str(i), u=u, v=v, length_m=d["length_m"], travel_time_s=d["travel_time_s"])
        for i, (u, v, d) in enumerate(g.edges(data=True))
    ]
    return g, NetworkDataset(dataset_id="rand", nodes=nodes, edges=edges)


def test_need_paths_dist_time_match_reference_path_rewalk():
    """The predecessor-accumulated distance/time must equal re-summing the
    materialized paths edge-by-edge (the OLD behavior) on a graph whose cost
    tree differs from the length/time trees."""
    g, ds = _random_graph()
    wf = build_weight_func("travel_time_s")
    svc = NetworkODMatrixService()

    res = svc.network_od_paths(["n0"], ["n17", "n53", "n99"], graph=g, network_dataset=ds)
    label = res["origin_labels"][0]

    # Reference: the exact per-edge re-walk the fix replaced.
    ref_dists, ref_paths = nx.single_source_dijkstra(g, "n0", weight=wf)
    for d_node, d_label in res["dest_nodes"]:
        pair = res["pairs"][(label, d_label)]
        if d_node not in ref_dists:
            assert not pair["reachable"]
            continue
        assert pair["reachable"]
        path = ref_paths[d_node]
        dist_acc = 0.0
        for u, v in zip(path, path[1:]):
            dist_acc += float(g[u][v].get("length_m", 0.0))
        time_acc = sum(float(g[u][v].get("travel_time_s", 0.0)) for u, v in zip(path, path[1:]))
        assert pair["distance_m"] == pytest.approx(dist_acc, abs=1e-6)
        assert pair["time_s"] == pytest.approx(time_acc, abs=1e-6)
        # The path itself is unchanged (consumers reconstruct routes from it).
        assert pair["path"] == path


def test_need_paths_equals_cost_only_where_costs_are_times():
    """Under the default travel-time impedance the need_paths variant's
    cost/time must match the cost-only matrix variant's travel_time_s
    (same shortest-path tree, same accumulation semantics)."""
    g, ds = _chain(300)
    svc = NetworkODMatrixService()
    pair = svc.network_od_paths(["n0"], ["n299"], graph=g, network_dataset=ds)
    label = pair["origin_labels"][0]
    info = pair["pairs"][(label, "n299")]
    matrix = svc.network_od_matrix(["n0"], ["n299"], graph=g, network_dataset=ds)
    assert matrix[0].reachable
    assert info["time_s"] == pytest.approx(matrix[0].travel_time_s, abs=1e-6)
    assert info["distance_m"] == pytest.approx(matrix[0].distance_m, abs=1e-6)


def test_closest_facility_8k_chain_under_acceptance_bound():
    """#600 acceptance criterion: closest-facility on an 8k-node chain graph
    finishes quickly. The need_paths branch used to re-walk every full path
    edge-by-edge; the snap index used to linear-scan all nodes per edge.
    Measured ~0.7 s cold on the dev box; bound leaves CI headroom."""
    g, ds = _chain(8000)
    svc = NetworkClosestFacilityService()
    demand = DemandPoint(demand_id="d0", weight=1.0,
                         geometry={"type": "Point", "coordinates": [116.0, 39.0]})
    facilities = [Facility(facility_id="f0",
                           geometry={"type": "Point", "coordinates": [116.7999, 39.0]})]

    t0 = time.perf_counter()
    res = svc.network_closest_facility(
        demand_points=[demand], facilities=facilities, graph=g, network_dataset=ds,
        target_facility_count=1,
    )
    elapsed = time.perf_counter() - t0

    assert len(res.routes) == 1
    assert res.routes[0].total_distance_m == pytest.approx(8000 * 8.3, rel=0.01)
    assert elapsed < 2.0, f"8k-chain closest-facility took {elapsed:.2f}s (acceptance: < 1s)"