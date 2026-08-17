"""Regression tests for issue #581 (P1): OD pair labels were formatted with
4-decimal coordinates (``pt_{lng:.4f}_{lat:.4f}``), so points ~8-11 m apart
(1e-4° ≈ 11 m lat / 8.5 m lng at 40°N) shared ONE label. The OD pair dict is
keyed by ``(origin_label, dest_label)`` and written overwrite-style, so the
later pair silently clobbered the earlier one's reachability / cost —
closest-facility and VRP made wrong decisions with no error.

Fix: labels carry the full-precision coordinate repr (``pt_{lng!r}_{lat!r}``),
so distinct points always get distinct labels.
"""
import pytest

import networkx as nx

from app.services.network.models import NetworkDataset, Node, Edge
from app.services.network.od_matrix import NetworkODMatrixService


def _street_with_isolated_stub():
    """A bidirectional street (nodes s0..s5 spaced ~11 m northward from
    39.9090 at lng 116.39720) plus an ISOLATED stub edge starting at
    (116.39721, 39.90914) — 0.9 m east of the street line, 8.9 m north of A.

    - Fac A = (116.39720, 39.90906) snaps onto the street → reachable to s5.
    - Fac B = (116.39721, 39.90914) snaps onto the stub (distance 0) rather
      than the street (0.9 m) → unreachable to s5.
    A and B are ~9 m apart and, under the old 4-decimal formatting, BOTH
    became ``pt_116.3972_39.9091`` — one pair entry overwriting the other.
    """
    g = nx.DiGraph()
    street_lng = 116.3972
    for i in range(6):
        g.add_node(f"s{i}", x=street_lng, y=39.9090 + i * 0.0001)
    for i in range(5):
        for u, v in ((f"s{i}", f"s{i + 1}"), (f"s{i + 1}", f"s{i}")):
            g.add_edge(u, v, length_m=11.1, travel_time_s=0.67, id=f"e_s{i}")
    stub_lng = 116.39721
    g.add_node("t0", x=stub_lng, y=39.90914)
    g.add_node("t1", x=stub_lng + 0.0001, y=39.90914)
    for u, v in (("t0", "t1"), ("t1", "t0")):
        g.add_edge(u, v, length_m=8.5, travel_time_s=0.51, id="e_t0")

    nodes = [Node(id=n, x=d["x"], y=d["y"]) for n, d in g.nodes(data=True)]
    edges = [
        Edge(id=str(i), u=u, v=v, length_m=d["length_m"], travel_time_s=d["travel_time_s"])
        for i, (u, v, d) in enumerate(g.edges(data=True))
    ]
    return g, NetworkDataset(dataset_id="street", nodes=nodes, edges=edges)


def test_points_9m_apart_keep_distinct_od_pairs():
    """#581: two origins 8.9 m apart must yield two independent pairs with the
    correct per-origin results, each matching its standalone query."""
    g, ds = _street_with_isolated_stub()
    svc = NetworkODMatrixService()

    a = (116.39720, 39.90906)  # on the street: reachable to s5
    b = (116.39721, 39.90914)  # ~9 m from A, on the isolated stub: unreachable
    res = svc.network_od_paths([a, b], ["s5"], graph=g, network_dataset=ds)

    assert len(res["origin_labels"]) == 2
    assert len(set(res["origin_labels"])) == 2, "#581: labels collided"
    assert len(res["pairs"]) == 2, "#581: one pair overwrote the other"

    label_a, label_b = res["origin_labels"]
    pair_a = res["pairs"][(label_a, "s5")]
    pair_b = res["pairs"][(label_b, "s5")]

    assert pair_a is not pair_b
    assert pair_a["reachable"] is True
    assert pair_b["reachable"] is False
    assert pair_b["cost"] == float("inf")

    # Each entry equals the standalone one-origin query (no cross-talk).
    solo_a = svc.network_od_paths([a], ["s5"], graph=g, network_dataset=ds)
    solo_a_pair = solo_a["pairs"][(solo_a["origin_labels"][0], "s5")]
    assert pair_a["cost"] == pytest.approx(solo_a_pair["cost"])
    assert pair_a["distance_m"] == pytest.approx(solo_a_pair["distance_m"])
    assert pair_a["time_s"] == pytest.approx(solo_a_pair["time_s"])


def test_adjacent_facilities_get_correct_closest_facility_result():
    """#581 end-to-end: closest-facility with two facilities ~9 m apart must
    not lose the reachable facility's entry to the overwrite bug."""
    g, ds = _street_with_isolated_stub()
    from app.services.network.facility import NetworkClosestFacilityService

    svc = NetworkClosestFacilityService()
    res = svc.network_closest_facility(
        demand_points=[(116.3972, 39.9090)],
        facilities=[(116.39720, 39.90906), (116.39721, 39.90914)],
        graph=g, network_dataset=ds, target_facility_count=2,
    )
    # The reachable facility (on the street) must be matched; the stub one is
    # unreachable and filtered out. Before the fix the collision could drop
    # the reachable entry and return nothing.
    assert len(res.routes) == 1, f"routes={len(res.routes)}: {res.routes}"
    assert res.routes[0].destination_id == "f_0"
    assert res.routes[0].total_time_s > 0.0