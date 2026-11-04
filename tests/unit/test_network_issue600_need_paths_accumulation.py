"""Regression tests for issue #600 (P2): the OD ``need_paths=True`` branch
materialized every node's full path AND then re-walked each path edge-by-edge
in Python to re-sum distance/time — O(sum|path|) ≈ O(V²) per origin (the
measured-48.9s@8k-nodes mode #449 already fixed for the cost-only variant).
The fix accumulates distance/time along each node's predecessor edge in O(V)
per origin, and the snap-index build no longer linear-scans all nodes per
geometry-less edge (O(E·N)).

The 8k-node correctness scenario stays covered. Its original strict 2s CI
budget runs separately in the required no-coverage performance lane.
"""
import math
import random
import time

import pytest

import networkx as nx

from app.services.network.contracts import ODPaths
from app.services.network.models import (
    NetworkDataset, Node, Edge, DemandPoint, Facility, Impedance, Barrier, NetworkAnalysisResult,
)
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


def run_closest_facility_8k_chain() -> tuple[NetworkAnalysisResult, float]:
    """Shared cold #600 workload; input construction stays outside the timer."""
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
    return res, elapsed


def test_closest_facility_8k_chain_routes_are_correct() -> None:
    res, _ = run_closest_facility_8k_chain()
    assert len(res.routes) == 1
    assert res.routes[0].total_distance_m == pytest.approx(8000 * 8.3, rel=0.01)


def test_need_paths_materializes_only_requested_destinations() -> None:
    """One requested long path must not retain every prefix of that path."""
    g, ds = _chain(300)
    g.add_node("island", x=117.0, y=40.0)
    result = NetworkODMatrixService()._compute_od(
        ["n0", "n0"], ["n299", "n150", "n299", "n0", "island"],
        g, ds, need_paths=True,
    )
    tree = result["results"]["n0"]
    assert set(tree["paths"]) == {"n299", "n150", "n0"}
    assert sum(map(len, tree["paths"].values())) == 300 + 151 + 1
    assert tree["paths"]["n299"] == [f"n{i}" for i in range(300)]
    # Cost and secondary metric maps remain full trees, not destination-only.
    assert set(tree["dists"]) == set(tree["dist_m"]) == set(tree["time_s"]) == set(g) - {"island"}
    for i in range(300):
        assert tree["dists"][f"n{i}"] == pytest.approx(i * 0.5)
        assert tree["dist_m"][f"n{i}"] == pytest.approx(i * 8.3)
        assert tree["time_s"][f"n{i}"] == pytest.approx(i * 0.5)


def test_need_paths_searches_once_per_origin_without_full_path_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g, ds = _chain(30)
    g.add_node("island", x=117.0, y=40.0)
    calls = []
    original = nx.dijkstra_predecessor_and_distance

    def predecessor_search(graph, source, **kwargs):
        calls.append(source)
        return original(graph, source, **kwargs)

    def reject_full_paths(*args, **kwargs):
        raise AssertionError("need_paths must not materialize all reachable paths")

    monkeypatch.setattr(nx, "dijkstra_predecessor_and_distance", predecessor_search)
    monkeypatch.setattr(nx, "single_source_dijkstra", reject_full_paths)
    monkeypatch.setattr(nx, "single_source_dijkstra_path", reject_full_paths)
    result = NetworkODMatrixService().network_od_paths(
        ["n0", "n0", "n10"], ["n29", "n29", "n0", "island"],
        graph=g, network_dataset=ds,
    )
    assert sorted(calls) == ["n0", "n10"]
    assert result["origin_labels"] == ["n0", "n0", "n10"]
    assert result["dest_labels"] == ["n29", "n29", "n0", "island"]
    assert len(result["pairs"]) == 6
    assert result["pairs"][("n0", "n0")]["path"] == ["n0"]
    assert not result["pairs"][("n10", "island")]["reachable"]


def _assert_reference_paths(result: ODPaths, cutoff: float | None = None) -> None:
    """Compare real service outputs to the old full NetworkX path oracle."""
    assert set(result) == {
        "origin_nodes", "origin_labels", "dest_nodes", "dest_labels",
        "graph_view", "weight_func", "pairs",
    }
    for origin, origin_label in result["origin_nodes"]:
        costs, paths = ({}, {}) if origin not in result["graph_view"] else nx.single_source_dijkstra(
            result["graph_view"], origin, weight=result["weight_func"], cutoff=cutoff,
        )
        for destination, destination_label in result["dest_nodes"]:
            pair = result["pairs"][(origin_label, destination_label)]
            assert set(pair) == {"path", "cost", "distance_m", "time_s", "reachable"}
            assert pair["reachable"] == (destination in costs)
            if not pair["reachable"]:
                assert pair == {
                    "path": [], "cost": float("inf"), "distance_m": float("inf"),
                    "time_s": float("inf"), "reachable": False,
                }
                continue
            path = paths[destination]
            assert pair["path"] == path
            assert pair["cost"] == costs[destination]
            edges = [result["graph_view"][u][v] for u, v in zip(path, path[1:])]
            assert pair["distance_m"] == pytest.approx(sum(e["length_m"] for e in edges), abs=1e-6)
            assert pair["time_s"] == pytest.approx(sum(e["travel_time_s"] for e in edges), abs=1e-6)


@pytest.mark.parametrize("seed", [600, 601, 602])
@pytest.mark.parametrize("cost_field", ["travel_time_s", "length_m"])
@pytest.mark.parametrize("cutoff", [None, 0.0, 500.0])
@pytest.mark.parametrize("barrier_factor", [None, 2.0, float("inf")])
def test_need_paths_matches_reference_with_barriers_and_cutoff(
    seed: int, cost_field: str, cutoff: float | None, barrier_factor: float | None,
) -> None:
    g, ds = _random_graph(seed)
    g.add_node("island", x=117.0, y=40.0)
    original_edges = {(u, v): dict(data) for u, v, data in g.edges(data=True)}
    barriers = None if barrier_factor is None else [Barrier(
        barrier_id="crossing", impedance_factor=barrier_factor,
        geometry={"type": "LineString", "coordinates": [[116.025, 39.0], [116.025, 39.05]]},
    )]
    result = NetworkODMatrixService().network_od_paths(
        ["n0", "n0", "n17"], ["n0", "n17", "n53", "n99", "n99", "island"],
        graph=g, network_dataset=ds, impedance=Impedance(name=cost_field),
        barriers=barriers, cutoff_s=cutoff,
    )
    _assert_reference_paths(result, cutoff)
    assert result["origin_labels"] == ["n0", "n0", "n17"]
    assert result["dest_labels"] == ["n0", "n17", "n53", "n99", "n99", "island"]
    assert {(u, v): dict(data) for u, v, data in g.edges(data=True)} == original_edges


@pytest.mark.parametrize("first", ["left", 7])
@pytest.mark.parametrize("cost_field", ["travel_time_s", "length_m"])
@pytest.mark.parametrize("first_cost", [1.0, 1e16])
def test_need_paths_preserves_ties_mixed_ids_and_float_rounding(
    first: str | int, cost_field: str, first_cost: float,
) -> None:
    g = nx.DiGraph()
    other = 7 if first == "left" else "left"
    for index, middle in enumerate((first, other)):
        attrs = {"travel_time_s": 11.0 + index, "length_m": 21.0 + index}
        g.add_edge("source", middle, **{**attrs, cost_field: first_cost})
        g.add_edge(middle, "target", **{**attrs, cost_field: 0.0001})
    # 1e16 + 0.0001 rounds back to 1e16: equal settled costs must not reorder
    # a parent after its child or replace the first equal-cost predecessor.
    result = NetworkODMatrixService().network_od_paths(
        ["source", "target"], ["target", "source"], graph=g,
        network_dataset=NetworkDataset(dataset_id="mixed-ties"),
        impedance=Impedance(name=cost_field),
    )
    _assert_reference_paths(result)
    assert result["pairs"][("source", "target")]["path"] == ["source", first, "target"]
    assert not result["pairs"][("target", "source")]["reachable"]


@pytest.mark.parametrize("cost_field", ["travel_time_s", "length_m"])
def test_need_paths_keeps_positive_cost_clamp_and_raw_secondary_metrics(cost_field: str) -> None:
    g = nx.DiGraph()
    g.add_edge("source", "via", **{"length_m": 7.0, "travel_time_s": 11.0, cost_field: 0.0})
    g.add_edge("via", "target", **{"length_m": 13.0, "travel_time_s": 17.0, cost_field: -2.0})
    g.add_edge("source", "target", length_m=100.0, travel_time_s=100.0)
    result = NetworkODMatrixService().network_od_paths(
        ["source"], ["target"], graph=g, network_dataset=NetworkDataset(dataset_id="clamped"),
        impedance=Impedance(name=cost_field),
    )
    _assert_reference_paths(result)
    assert result["pairs"][("source", "target")]["path"] == ["source", "via", "target"]
    assert result["pairs"][("source", "target")]["cost"] == 0.002


def test_need_paths_missing_nodes_keep_explicit_unreachable_pairs() -> None:
    g, ds = _chain(4)
    result = NetworkODMatrixService().network_od_paths(
        ["missing", "n0"], ["missing", "n3", "n0"], graph=g, network_dataset=ds,
    )
    _assert_reference_paths(result)
    assert len(result["pairs"]) == 6
    assert not result["pairs"][("missing", "missing")]["reachable"]


@pytest.mark.parametrize("origins,destinations", [([], ["n3"]), (["n0"], []), ([], [])])
def test_need_paths_empty_inputs_keep_public_shape(origins: list[str], destinations: list[str]) -> None:
    g, ds = _chain(4)
    result = NetworkODMatrixService().network_od_paths(origins, destinations, graph=g, network_dataset=ds)
    assert result == {
        "origin_nodes": [], "origin_labels": [], "dest_nodes": [], "dest_labels": [],
        "graph_view": g, "weight_func": None, "pairs": {},
    }


@pytest.mark.parametrize("cost_field", ["travel_time_s", "length_m"])
@pytest.mark.parametrize("cutoff", [None, 0.5])
@pytest.mark.parametrize("barrier_factor", [None, 2.0, float("inf")])
def test_need_paths_snapped_mid_edges_match_reference(
    cost_field: str, cutoff: float | None, barrier_factor: float | None,
) -> None:
    g, ds = _chain(4)
    barriers = None if barrier_factor is None else [Barrier(
        barrier_id="middle", impedance_factor=barrier_factor,
        geometry={"type": "LineString", "coordinates": [[116.00015, 38.999], [116.00015, 39.001]]},
    )]
    result = NetworkODMatrixService().network_od_paths(
        [(116.000025, 39.0)], [(116.000275, 39.0)], graph=g, network_dataset=ds,
        impedance=Impedance(name=cost_field), barriers=barriers, cutoff_s=cutoff,
    )
    _assert_reference_paths(result, cutoff)
    assert result["origin_nodes"][0][0].startswith("vt_")
    assert result["dest_nodes"][0][0].startswith("vt_")
    assert set(g) == {"n0", "n1", "n2", "n3"}
    assert all("_barrier_factor" not in data for _, _, data in g.edges(data=True))
