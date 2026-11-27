"""Regression tests: closest_facility grouped its candidate pool by
``demand_id``, so two demand points sharing an id (GeoJSON features with
duplicate ids, or directly constructed ``DemandPoint`` objects) had their
candidate pools merged — top-K was then selected across the union, silently
dropping routes, and the unmatched audit could no longer see a duplicate-id
demand that received no route.

Fix: candidates are grouped by the per-demand index (unique by construction);
the demand_id is still what lands on ``route.origin_id`` /
``route.destination_id`` and in ``unmatched_demand_ids``.
"""

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile


def _line_network(with_island: bool = False):
    """Straight two-edge road 116.0 -> 116.01 -> 116.02 at lat 39.0, optionally
    plus a disconnected island edge far away."""
    features = [
        {
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
        },
        {
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.02, 39.0]]},
        },
    ]
    if with_island:
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {"type": "LineString", "coordinates": [[117.0, 40.0], [117.01, 40.0]]},
        })
    builder = NetworkGraphBuilder()
    return builder.build_graph(
        {"type": "FeatureCollection", "features": features}, profile=TravelProfile()
    )


def _duplicate_id_demands(second_xy):
    """Two demands sharing the id 'dup' — the second one at ``second_xy``."""
    return [
        {"demand_id": "dup", "geometry": {"type": "Point", "coordinates": [116.001, 39.0]}},
        {"demand_id": "dup", "geometry": {"type": "Point", "coordinates": list(second_xy)}},
    ]


def _two_end_facilities():
    return [
        {"facility_id": "f_left", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}},
        {"facility_id": "f_right", "geometry": {"type": "Point", "coordinates": [116.02, 39.0]}},
    ]


class TestDuplicateDemandIdGrouping:
    def test_each_duplicate_id_demand_gets_own_nearest_facility(self):
        """Each duplicate-id demand must receive its OWN nearest facility.

        With the id-keyed pool the two demands' candidates were merged and
        top-K was chosen across the union, so only the globally cheapest
        pair survived — the second demand's route vanished silently.
        """
        graph, dataset = _line_network()
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=_duplicate_id_demands([116.019, 39.0]),
            facilities=_two_end_facilities(),
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
        )
        assert len(res.routes) == 2
        assert sorted(r.destination_id for r in res.routes) == ["f_left", "f_right"]
        assert all(r.origin_id == "dup" for r in res.routes)
        assert res.summary["unmatched_demand_count"] == 0
        assert res.summary["unmatched_demand_ids"] == []

    def test_unmatched_audit_sees_duplicate_id_demand_without_route(self):
        """A duplicate-id demand that matches nothing must be reported in
        ``unmatched_demand_ids`` even though its id-twin matched — the
        id-keyed ``selected`` set made the twin's match hide the gap."""
        graph, dataset = _line_network(with_island=True)
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=_duplicate_id_demands([117.005, 40.0]),
            facilities=[
                {"facility_id": "f_left", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}},
            ],
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
        )
        assert len(res.routes) == 1
        assert res.summary["unmatched_demand_count"] == 1
        assert res.summary["unmatched_demand_ids"] == ["dup"]

    def test_duplicate_ids_reverse_direction(self):
        """Same isolation for travel_direction='facility_to_incident', where
        the demand is the destination of every pair."""
        graph, dataset = _line_network()
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=_duplicate_id_demands([116.019, 39.0]),
            facilities=_two_end_facilities(),
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
            travel_direction="facility_to_incident",
        )
        assert len(res.routes) == 2
        assert sorted(r.origin_id for r in res.routes) == ["f_left", "f_right"]
        assert all(r.destination_id == "dup" for r in res.routes)

    def test_duplicate_id_demand_objects_not_merged(self):
        """Directly constructed DemandPoint objects with a shared id behave
        the same as duplicate GeoJSON ids."""
        from app.services.network.models import DemandPoint

        graph, dataset = _line_network()
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=[
                DemandPoint(demand_id="dup", weight=1.0,
                            geometry={"type": "Point", "coordinates": [116.001, 39.0]}),
                DemandPoint(demand_id="dup", weight=1.0,
                            geometry={"type": "Point", "coordinates": [116.019, 39.0]}),
            ],
            facilities=_two_end_facilities(),
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
        )
        assert len(res.routes) == 2
        assert sorted(r.destination_id for r in res.routes) == ["f_left", "f_right"]

    def test_unique_ids_behaviour_unchanged(self):
        """Guard the non-regression side: with unique ids the result is the
        same per-demand top-K as before the fix."""
        graph, dataset = _line_network()
        svc = NetworkClosestFacilityService()
        res = svc.network_closest_facility(
            demand_points=[
                {"demand_id": "d_a", "geometry": {"type": "Point", "coordinates": [116.001, 39.0]}},
                {"demand_id": "d_b", "geometry": {"type": "Point", "coordinates": [116.019, 39.0]}},
            ],
            facilities=_two_end_facilities(),
            graph=graph,
            network_dataset=dataset,
            target_facility_count=2,
        )
        assert len(res.routes) == 4
        assert res.summary["unmatched_demand_ids"] == []
        by_origin = {}
        for r in res.routes:
            by_origin.setdefault(r.origin_id, []).append(r.destination_id)
        assert by_origin["d_a"] == ["f_left", "f_right"]
        assert by_origin["d_b"] == ["f_right", "f_left"]
