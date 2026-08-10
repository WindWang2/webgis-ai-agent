"""Regression tests for GIS-11 (location_allocation brute-force C(m,p) hang).

The old implementation enumerated ALL C(m,p) combinations (C(50,5) ≈ 2.1M,
C(100,10) ≈ 1.7e13) — a legitimate "choose 5 of 80 sites" request hung
indefinitely. Now instances beyond a combination budget use polynomial
heuristics (Teitz-Bart for p-median, greedy-add for max coverage).
"""
import time

import pytest

from app.services.network.allocation import (
    NetworkLocationAllocationService,
    _exact_combination_count,
    _MAX_EXACT_COMBINATIONS,
)
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import (
    Facility,
    DemandPoint,
    NetworkAnalysisResult,
    TravelProfile,
)


def _facilities(n: int) -> list:
    return [
        Facility(facility_id=f"f{i}", geometry={"type": "Point", "coordinates": [116.0 + i * 0.001, 39.0]})
        for i in range(n)
    ]


def _demands(n: int) -> list:
    return [
        DemandPoint(demand_id=f"d{i}", weight=1.0, geometry={"type": "Point", "coordinates": [116.0 + i * 0.0005, 39.0]})
        for i in range(n)
    ]


@pytest.fixture
def road_network():
    """A realistic road spine network (one long road + cross streets) so the
    OD matrix has finite costs for every demand/facility pair."""
    features = []
    # Spine: 12 vertices along lng 116.00 → 116.012
    spine = [[116.0 + i * 0.001, 39.0] for i in range(13)]
    features.append({
        "type": "Feature",
        "properties": {"id": "spine", "speed_kmh": 60.0, "one_way": False},
        "geometry": {"type": "LineString", "coordinates": spine},
    })
    # Cross streets at every other vertex so facilities/demands snap cleanly.
    for i in range(0, 13, 2):
        x = 116.0 + i * 0.001
        features.append({
            "type": "Feature",
            "properties": {"id": f"cross{i}", "speed_kmh": 40.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[x, 38.9995], [x, 39.0005]],
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


def test_exact_combination_count_threshold():
    assert _exact_combination_count(80, 5) > _MAX_EXACT_COMBINATIONS  # 24M
    assert _exact_combination_count(20, 3) <= _MAX_EXACT_COMBINATIONS  # 1140
    assert _exact_combination_count(5, 5) == 1
    assert _exact_combination_count(10, 0) == 1


def test_large_p_median_uses_heuristic_and_terminates(road_network):
    """A 60-candidate / 30-demand p=5 problem must complete quickly.

    C(60,5) ≈ 5.5M >> budget → Teitz-Bart path. Before the fix this hung
    (or OOM'd) materializing the combination list.
    """
    graph, dataset = road_network
    svc = NetworkLocationAllocationService()
    t0 = time.perf_counter()
    res = svc.location_allocation(
        candidate_facilities=_facilities(60),
        demand_points=_demands(30),
        p_count=5,
        problem_type="p_median",
        graph=graph,
        network_dataset=dataset,
    )
    elapsed = time.perf_counter() - t0

    assert isinstance(res, NetworkAnalysisResult)
    assert res.summary["solver"] == "heuristic", f"solver={res.summary.get('solver')}"
    assert res.summary["selected_facilities_count"] == 5
    assert len(res.allocated_facilities) == 5
    # Generous bound: heuristic should take well under 2s even on slow CI.
    assert elapsed < 2.0, f"location_allocation took {elapsed:.2f}s"


def test_large_max_coverage_uses_heuristic_and_terminates(road_network):
    graph, dataset = road_network
    svc = NetworkLocationAllocationService()
    t0 = time.perf_counter()
    res = svc.location_allocation(
        candidate_facilities=_facilities(60),
        demand_points=_demands(30),
        p_count=5,
        problem_type="max_coverage",
        cutoff_cost=300.0,
        graph=graph,
        network_dataset=dataset,
    )
    elapsed = time.perf_counter() - t0
    assert res.summary["solver"] == "heuristic"
    assert res.summary["selected_facilities_count"] == 5
    assert elapsed < 2.0, f"location_allocation took {elapsed:.2f}s"


def test_small_exact_still_exact(road_network):
    """Small instances keep the exact (optimal) solver — behavior preserved."""
    graph, dataset = road_network
    svc = NetworkLocationAllocationService()
    res = svc.location_allocation(
        candidate_facilities=_facilities(8),
        demand_points=_demands(5),
        p_count=2,
        problem_type="p_median",
        graph=graph,
        network_dataset=dataset,
    )
    assert res.summary["solver"] == "exact"
    assert res.summary["selected_facilities_count"] == 2


def test_heuristic_selects_reasonable_sites(road_network):
    """On a line of collinear facilities, p-median heuristic should pick
    well-spread sites (not the first p clustered ones)."""
    graph, dataset = road_network
    svc = NetworkLocationAllocationService()
    # 10 facilities spread over 0.01°, 10 demands spread over the same range.
    res = svc.location_allocation(
        candidate_facilities=_facilities(10),
        demand_points=_demands(10),
        p_count=3,
        problem_type="p_median",
        graph=graph,
        network_dataset=dataset,
    )
    # The heuristic must not simply return the first 3 facilities.
    selected_ids = [a["facility_id"] for a in res.allocated_facilities]
    assert len(set(selected_ids)) == 3
    # Sanity: with a uniform line, an optimal-ish spread covers indexes like
    # {0, 4-5, 9}; the first-3 cluster (0,1,2) would be obviously wrong.
    assert not (selected_ids == ["f0", "f1", "f2"]), (
        f"heuristic returned the trivial first-3 cluster: {selected_ids}"
    )

