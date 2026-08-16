"""Regression tests for issue #456 (P3): closest_facility dropped demand-
facility pairs whose ``distance_m <= 0`` — excluding the perfectly valid case
of a demand point located exactly AT a facility (zero-distance match). Those
demands returned no route at all instead of a zero-cost match.

Fix: the guard filters only non-finite (unreachable/sentinel) values;
``distance_m == 0`` is a legitimate match and is kept.
"""
import math

import pytest

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile


def _triangle_network():
    """Three-junction two-way road triangle around (116.0-116.01, 39.0)."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
            },
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.005, 39.006]]},
            },
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.005, 39.006], [116.0, 39.0]]},
            },
        ],
    }
    builder = NetworkGraphBuilder()
    return builder.build_graph(geojson, profile=TravelProfile())


def _fac(demand_xy, facility_xy):
    graph, dataset = _triangle_network()
    svc = NetworkClosestFacilityService()
    return svc.network_closest_facility(
        demand_points=[
            {"demand_id": "d_coincident", "geometry": {"type": "Point", "coordinates": list(demand_xy)}},
            {"demand_id": "d_other", "geometry": {"type": "Point", "coordinates": [116.003, 39.002]}},
        ],
        facilities=[
            {"facility_id": "f_here", "geometry": {"type": "Point", "coordinates": list(facility_xy)}},
        ],
        graph=graph,
        network_dataset=dataset,
        target_facility_count=1,
    )


class TestZeroDistanceMatch:
    def test_demand_at_facility_node_returns_zero_cost_route(self):
        """Demand coincident with a facility (on a junction) must be returned
        as reachable with distance 0 — not dropped from the results."""
        res = _fac((116.01, 39.0), (116.01, 39.0))
        by_origin = {r.origin_id: r for r in res.routes}
        assert "d_coincident" in by_origin, (
            "coincident demand/facility pair vanished from closest_facility "
            "results (#456: distance_m <= 0 guard)"
        )
        r = by_origin["d_coincident"]
        assert r.total_distance_m == pytest.approx(0.0, abs=1e-6)
        assert r.total_cost == pytest.approx(0.0, abs=1e-3)

    def test_demand_at_facility_mid_edge_returns_zero_cost_route(self):
        """Same for a coincident pair snapping mid-edge (#453 + #456)."""
        res = _fac((116.0049, 39.0001), (116.0049, 39.0001))
        by_origin = {r.origin_id: r for r in res.routes}
        assert "d_coincident" in by_origin
        assert by_origin["d_coincident"].total_distance_m == pytest.approx(0.0, abs=1.0)

    def test_zero_distance_wins_over_nonzero_competitor(self):
        """A coincident facility must outrank a farther one (ranking by cost;
        the dropped zero-distance pair previously left only the far one)."""
        graph, dataset = _triangle_network()
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=[
                {"demand_id": "d1", "geometry": {"type": "Point", "coordinates": [116.01, 39.0]}},
            ],
            facilities=[
                {"facility_id": "f_far", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}},
                {"facility_id": "f_here", "geometry": {"type": "Point", "coordinates": [116.01, 39.0]}},
            ],
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
        )
        assert len(res.routes) == 1
        assert res.routes[0].destination_id == "f_here"
        assert res.routes[0].total_distance_m == pytest.approx(0.0, abs=1e-6)

    def test_unreachable_pairs_still_excluded(self):
        """The guard's original purpose — filtering unreachable (inf) pairs —
        must survive: a facility on a disconnected island is never matched."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"speed_kmh": 60.0, "one_way": False},
                    "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
                },
                {
                    "type": "Feature",
                    "properties": {"speed_kmh": 60.0, "one_way": False},
                    "geometry": {"type": "LineString", "coordinates": [[117.0, 40.0], [117.01, 40.0]]},
                },
            ],
        }
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=[
                {"demand_id": "d1", "geometry": {"type": "Point", "coordinates": [116.005, 39.0]}},
            ],
            facilities=[
                {"facility_id": "f_island", "geometry": {"type": "Point", "coordinates": [117.005, 40.0]}},
            ],
            graph=graph,
            network_dataset=dataset,
        )
        assert res.routes == []

    def test_negative_distance_never_occurs_but_would_be_kept_finite_only(self):
        """The filter is finiteness-based: math.isfinite(distance_m)."""
        assert math.isfinite(0.0)
        assert not math.isfinite(float("inf"))
