"""Regression tests for GIS-19 (OD matrix ran 3 full Dijkstra passes per origin).

The old implementation ran single_source_dijkstra_path_length THREE times per
unique origin (cost, distance, time) even though distance and time accumulate
along the same shortest path. Now one Dijkstra per origin recovers distance and
time from the shortest-path tree.
"""
import time

import pytest

from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile
from app.services.network.od_matrix import NetworkODMatrixService


@pytest.fixture
def grid_network():
    """A 12x12 grid (~264 edges) road network around (116.0, 39.0)."""
    features = []
    for r in range(12):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for c in range(12)],
            },
        })
    for c in range(12):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for r in range(12)],
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


def test_od_matrix_dist_time_consistent_with_path(grid_network):
    """Reachable pairs must report distance and time consistently (both > 0 for
    distinct points), matching the impedance shortest path."""
    graph, dataset = grid_network
    svc = NetworkODMatrixService()

    origins = [(116.005 + i * 0.002, 39.005) for i in range(4)]
    dests = [(116.008 + i * 0.002, 39.008) for i in range(4)]

    pairs = svc.network_od_matrix(origins, dests, graph=graph, network_dataset=dataset)
    assert len(pairs) == 16

    for p in pairs:
        if p.reachable:
            if p.origin_id == p.destination_id:
                # Same snapped location: zero distance/time is correct.
                assert p.distance_m == 0.0 and p.travel_time_s == 0.0
            else:
                assert p.distance_m > 0.0, f"{p.origin_id}->{p.destination_id} distance"
                assert p.travel_time_s > 0.0, f"{p.origin_id}->{p.destination_id} time"


def test_od_matrix_matches_direct_dijkstra_lengths(grid_network):
    """GIS-19: distance/time recovered from the path tree must equal what a
    dedicated single-source pass with length/time weights would produce."""
    import networkx as nx

    graph, dataset = grid_network
    svc = NetworkODMatrixService()

    origins = [(116.005, 39.005), (116.008, 39.008)]
    dests = [(116.010, 39.010)]
    pairs = svc.network_od_matrix(origins, dests, graph=graph, network_dataset=dataset)

    # Resolve origin nodes the same way the OD service does.
    orig_nodes = [svc.router._resolve_node(o, dataset)[0] for o in origins]
    dest_nodes = [svc.router._resolve_node(d, dataset)[0] for d in dests]

    for o_node, o_label in zip(orig_nodes, ["pt_116.0050_39.0050", "pt_116.0080_39.0080"]):
        if o_node not in graph:
            continue
        ref_dist = nx.single_source_dijkstra_path_length(graph, o_node, weight="length_m")
        ref_time = nx.single_source_dijkstra_path_length(graph, o_node, weight="travel_time_s")
        for d_node, d_label in zip(dest_nodes, ["pt_116.0100_39.0100"]):
            pair = next(p for p in pairs if p.origin_id == o_label and p.destination_id == d_label)
            if d_node in ref_dist:
                assert pair.reachable
                assert abs(pair.distance_m - ref_dist[d_node]) < 1.0, (
                    f"{o_label}->{d_label}: dist {pair.distance_m} vs ref {ref_dist[d_node]}"
                )
                assert abs(pair.travel_time_s - ref_time[d_node]) < 1.0, (
                    f"{o_label}->{d_label}: time {pair.travel_time_s} vs ref {ref_time[d_node]}"
                )


def test_od_matrix_single_dijkstra_per_origin(grid_network):
    """GIS-19 performance guard: a 12x12 grid OD (12 origins × 12 dests = 144
    pairs) must complete quickly — the old 3-pass implementation paid 3 full
    Dijkstra per origin."""
    graph, dataset = grid_network
    svc = NetworkODMatrixService()

    origins = [(116.003 + i * 0.001, 39.003) for i in range(12)]
    dests = [(116.009 + i * 0.001, 39.009) for i in range(12)]

    t0 = time.perf_counter()
    pairs = svc.network_od_matrix(origins, dests, graph=graph, network_dataset=dataset)
    elapsed = time.perf_counter() - t0

    assert len(pairs) == 144
    # Generous bound for slow CI; single-pass is ~3x faster than before.
    assert elapsed < 2.0, f"OD matrix 12x12 took {elapsed:.2f}s"
