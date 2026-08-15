"""Regression tests for network/temporal engine algorithmic hot spots (#403).

1. Intersection splitting uses a STRtree candidate query instead of the O(n²)
   pairwise shapely loop — results must be identical.
2. closest_facility reuses the OD service's multi-source Dijkstra trees and no
   longer performs a per-pair ``graph.copy()``; VRP stitches tour legs from the
   OD predecessor trees instead of re-running shortest path per leg.
3. Sen's slope is vectorized and capped; graph fingerprints are memoized by
   object identity so repeated builds don't re-serialize the whole network.
"""
import json

import networkx as nx
import numpy as np
import pytest

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import (
    DemandPoint,
    Facility,
)
from app.services.network.routing import NetworkRoutingService
from app.services.network.vrp import NetworkRouteOptimizationService
from app.services.temporal.trend import TemporalTrendEngine
from shapely.geometry import LineString
from shapely.ops import split, unary_union


def _line(coords, props=None):
    return {"type": "Feature", "properties": props or {}, "geometry": {"type": "LineString", "coordinates": coords}}


@pytest.fixture
def crossing_network():
    """Four crossing lines + one disjoint line (classic split case)."""
    return {
        "type": "FeatureCollection",
        "features": [
            _line([[0.0, 0.5], [1.0, 0.5]]),          # horizontal
            _line([[0.5, 0.0], [0.5, 1.0]]),          # vertical (crosses at 0.5,0.5)
            _line([[0.25, 0.0], [0.75, 1.0]]),        # diagonal (crosses both)
            _line([[0.0, 0.25], [0.5, 0.25]]),        # T-junction on the vertical
            _line([[5.0, 5.0], [6.0, 5.0]]),          # disjoint — no intersections
        ],
    }


# ── STRtree intersection splitting ──────────────────────────────────────────


def test_intersection_splitting_matches_pairwise_reference(crossing_network):
    """The STRtree path must produce the same split line set as the old O(n²)
    pairwise loop (same intersection multiset → same splits)."""
    builder = NetworkGraphBuilder()
    items = builder._extract_line_items(crossing_network)

    split_items = builder._process_intersections(items, True)

    # Reference: naive pairwise intersection collection + split pipeline
    # (the pre-fix algorithm).
    lines = [item[0] for item in items]
    ref_points = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            inter = lines[i].intersection(lines[j])
            if not inter.is_empty:
                if inter.geom_type == "Point":
                    ref_points.append(inter)
                elif inter.geom_type == "MultiPoint":
                    ref_points.extend(list(inter.geoms))
    assert ref_points, "fixture must contain intersections"

    ref_items = []
    split_cutter = unary_union(ref_points)
    for line, props in items:
        try:
            for sub_geom in split(line, split_cutter).geoms:
                if isinstance(sub_geom, LineString) and sub_geom.length > 1e-9:
                    ref_items.append((sub_geom, props))
        except Exception:
            ref_items.append((line, props))

    assert len(split_items) == len(ref_items)
    # identical total length and identical segment coordinate multisets
    orig_len = sum(line.length for line in lines)
    assert abs(sum(g.length for g, _ in split_items) - orig_len) < 1e-9
    assert sorted(tuple(g.coords) for g, _ in split_items) == sorted(
        tuple(g.coords) for g, _ in ref_items
    )


def test_build_graph_split_intersections_end_to_end(crossing_network):
    """End-to-end: a graph built with split_intersections=True contains the
    crossing point as a node and both endpoint nodes."""
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(crossing_network, split_intersections=True)

    node_coords = {(d["x"], d["y"]) for _, d in graph.nodes(data=True)}
    assert (0.5, 0.5) in node_coords  # main crossing
    assert (0.375, 0.25) in node_coords  # diagonal × T-junction
    assert (0.5, 0.25) in node_coords  # vertical × T-junction endpoint
    assert (0.0, 0.5) in node_coords  # original endpoint survives
    # the disjoint line stays intact
    assert (5.0, 5.0) in node_coords and (6.0, 5.0) in node_coords


def test_build_graph_split_off_by_default(crossing_network):
    """split_intersections=True is the default and must not regress to O(n²)."""
    builder = NetworkGraphBuilder()
    graph, _ = builder.build_graph(crossing_network)
    node_coords = {(d["x"], d["y"]) for _, d in graph.nodes(data=True)}
    assert (0.5, 0.5) in node_coords


# ── closest_facility: no per-pair Dijkstra, no graph copy ───────────────────


def _build_grid():
    features = []
    for r in range(4):
        features.append(_line([[116.0, 39.0 + r * 0.01], [116.03, 39.0 + r * 0.01]]))
    for c in range(4):
        features.append(_line([[116.0 + c * 0.01, 39.0], [116.0 + c * 0.01, 39.03]]))
    builder = NetworkGraphBuilder()
    return builder.build_graph({"type": "FeatureCollection", "features": features})


def test_closest_facility_no_graph_copy(monkeypatch):
    """closest_facility with coordinate inputs must not copy the graph per
    demand×facility pair (the old code ran network_shortest_path per pair,
    which copied the whole graph for every call)."""
    graph, dataset = _build_grid()
    copies = []

    real_copy = nx.DiGraph.copy

    def counting_copy(self, *args, **kwargs):
        copies.append(1)
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(nx.DiGraph, "copy", counting_copy)

    svc = NetworkClosestFacilityService()
    demands = [DemandPoint(demand_id="d1", weight=1.0, geometry={"type": "Point", "coordinates": [116.01, 39.005]})]
    facilities = [
        Facility(facility_id="f_far", geometry={"type": "Point", "coordinates": [116.03, 39.03]}),
        Facility(facility_id="f_near", geometry={"type": "Point", "coordinates": [116.02, 39.005]}),
    ]
    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities,
        graph=graph, network_dataset=dataset, target_facility_count=1,
    )
    assert copies == [], f"graph.copy called {len(copies)} times during closest_facility"
    assert len(res.routes) == 1
    assert res.routes[0].destination_id == "f_near"
    assert res.routes[0].total_distance_m > 0
    assert res.routes[0].geometry["type"] == "LineString"
    assert len(res.routes[0].path_node_ids) >= 2


def test_closest_facility_matches_old_routing_cost_order(monkeypatch):
    """Closest-facility ranking via OD trees must agree with direct routing."""
    graph, dataset = _build_grid()
    svc = NetworkClosestFacilityService()
    # All coordinates sit exactly on grid nodes, so direct routing (no virtual
    # node split) and the OD-tree reconstruction traverse the same node path.
    demands = [DemandPoint(demand_id="d1", weight=1.0, geometry={"type": "Point", "coordinates": [116.01, 39.0]})]
    facilities = [
        Facility(facility_id="f_far", geometry={"type": "Point", "coordinates": [116.03, 39.03]}),
        Facility(facility_id="f_mid", geometry={"type": "Point", "coordinates": [116.01, 39.02]}),
        Facility(facility_id="f_near", geometry={"type": "Point", "coordinates": [116.02, 39.0]}),
    ]
    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities,
        graph=graph, network_dataset=dataset, target_facility_count=3,
    )
    assert [r.destination_id for r in res.routes] == ["f_near", "f_mid", "f_far"]

    # OD-tree route cost == direct Dijkstra over the same graph (node path).
    router = NetworkRoutingService()
    direct = router.network_shortest_path(
        graph=graph, network_dataset=dataset,
        origin=(116.01, 39.0), destination=(116.02, 39.0),
    )
    od_route = next(r for r in res.routes if r.destination_id == "f_near")
    assert od_route.path_node_ids == direct.path_node_ids
    assert abs(od_route.total_distance_m - direct.total_distance_m) < 1e-6
    assert abs(od_route.total_time_s - direct.total_time_s) < 1e-6


# ── VRP: tour legs reuse the OD trees ───────────────────────────────────────


def test_vrp_stitches_legs_without_rerunning_shortest_path(monkeypatch):
    """After the OD pass every tour leg must come from the predecessor trees —
    network_shortest_path (and its per-call graph copy) must not run at all."""
    graph, dataset = _build_grid()

    def _no_shortest_path(*args, **kwargs):
        raise AssertionError("network_shortest_path must not be called during VRP leg stitching")

    monkeypatch.setattr(NetworkRoutingService, "network_shortest_path", _no_shortest_path)

    svc = NetworkRouteOptimizationService()
    route = svc.optimize_route(
        stops=[(116.01, 39.005), (116.03, 39.03), (116.0, 39.03)],
        depot=(116.0, 39.0),
        end_at_depot=True,
        graph=graph,
        network_dataset=dataset,
    )
    assert route.total_distance_m > 0
    assert route.total_time_s > 0
    assert route.geometry["type"] == "LineString"
    assert len(route.geometry["coordinates"]) >= 2


def test_vrp_single_stop_still_routes(monkeypatch):
    """The degenerate single-stop case still routes directly (no OD needed)."""
    graph, dataset = _build_grid()
    svc = NetworkRouteOptimizationService()
    route = svc.optimize_route(
        stops=[(116.01, 39.005)],
        depot=(116.0, 39.0),
        graph=graph,
        network_dataset=dataset,
    )
    assert route.total_distance_m > 0


# ── Sen's slope: vectorized + capped ────────────────────────────────────────


def _reference_sens_slope(values):
    """Brute-force pairwise Sen's slope (the pre-fix algorithm)."""
    n = len(values)
    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            slopes.append((values[j] - values[i]) / (j - i))
    slopes.sort()
    m = len(slopes)
    if m % 2 == 1:
        return slopes[m // 2]
    return (slopes[m // 2 - 1] + slopes[m // 2]) / 2.0


@pytest.mark.parametrize("n", [2, 3, 10, 64, 1024])
def test_sens_slope_matches_reference(n):
    rng = np.random.default_rng(42)
    values = list(rng.normal(size=n))
    assert TemporalTrendEngine.compute_sens_slope(values) == pytest.approx(
        _reference_sens_slope(values), rel=1e-12
    )


def test_sens_slope_linear_exact():
    values = [2.0 * i for i in range(50)]
    assert TemporalTrendEngine.compute_sens_slope(values) == 2.0


def test_sens_slope_large_input_truncated(caplog):
    """50k records previously materialized ~1.25e9 slopes; now the input is
    subsampled to the cap with a warning and still returns the right slope for
    a linear series."""
    n = 5000
    values = [3.0 * i for i in range(n)]
    with caplog.at_level("WARNING", logger="app.services.temporal.trend"):
        slope = TemporalTrendEngine.compute_sens_slope(values)
    assert slope == 3.0
    assert any("truncated" in r.message for r in caplog.records)


def test_analyze_trend_still_uses_sens_slope():
    eng = TemporalTrendEngine()
    res = eng.analyze_trend([1.0, 3.0, 5.0, 7.0])
    assert res.slope == 2.0
    assert res.direction == "increasing"


# ── Fingerprint memoization ─────────────────────────────────────────────────


def test_fingerprint_memoized_by_object_identity(monkeypatch):
    builder = NetworkGraphBuilder()
    dumps_calls = []

    real_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        dumps_calls.append(args)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr("app.services.network.graph_builder.json.dumps", counting_dumps)

    data = {"type": "FeatureCollection", "features": [_line([[0.0, 0.0], [1.0, 1.0]])]}
    fp1 = builder.compute_fingerprint(data, None, 1e-5, True)
    fp2 = builder.compute_fingerprint(data, None, 1e-5, True)
    assert fp1 == fp2
    # the second call must not re-serialize the network
    assert len(dumps_calls) == 1, f"json.dumps called {len(dumps_calls)} times"

    # a distinct object with identical content re-serializes but produces the
    # SAME content-based fingerprint (the memo only skips re-serialization of
    # the same object — cache hit semantics for equal networks are preserved)
    data2 = {"type": "FeatureCollection", "features": [_line([[0.0, 0.0], [1.0, 1.0]])]}
    fp3 = builder.compute_fingerprint(data2, None, 1e-5, True)
    assert fp3 == fp1
    assert len(dumps_calls) == 2


def test_build_graph_cache_hit_skips_reserialization(monkeypatch):
    builder = NetworkGraphBuilder()
    builder.clear_cache()
    dumps_calls = []

    real_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        dumps_calls.append(args)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr("app.services.network.graph_builder.json.dumps", counting_dumps)

    data = {"type": "FeatureCollection", "features": [_line([[0.0, 0.0], [1.0, 1.0]])]}
    g1, _ = builder.build_graph(data, use_cache=True)
    g2, _ = builder.build_graph(data, use_cache=True)
    assert g1 is g2  # cache hit
    assert len(dumps_calls) == 1  # one serialization for the whole sequence
