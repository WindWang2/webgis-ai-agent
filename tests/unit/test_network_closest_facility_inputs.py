"""Regression tests for closest_facility input contracts.

- ``contracts.DemandInput`` / ``contracts.FacilityInput`` declare a
  ``coordinates`` field, but normalization only accepted ``geometry`` and
  raised ValueError for ``{"coordinates": [lng, lat]}`` dicts.
- An unknown ``travel_direction`` was silently treated as
  ``incident_to_facility`` with no error and no disclosure.
"""
import pytest

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile


def _line_network():
    features = [
        {
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.02, 39.0]]},
        },
    ]
    builder = NetworkGraphBuilder()
    builder.clear_cache()
    return builder.build_graph(
        {"type": "FeatureCollection", "features": features}, profile=TravelProfile()
    )


class TestCoordinatesInputContract:
    def test_coordinates_dict_accepted_for_demands_and_facilities(self):
        graph, dataset = _line_network()
        res = NetworkClosestFacilityService().network_closest_facility(
            demand_points=[{"demand_id": "d1", "coordinates": [116.001, 39.0]}],
            facilities=[{"facility_id": "f1", "name": "A", "coordinates": [116.019, 39.0]}],
            graph=graph,
            network_dataset=dataset,
        )
        assert len(res.routes) == 1
        assert res.routes[0].origin_id == "d1"
        assert res.routes[0].destination_id == "f1"
        assert res.summary["unmatched_demand_count"] == 0


class TestTravelDirectionValidation:
    def test_unknown_travel_direction_rejected(self):
        graph, dataset = _line_network()
        with pytest.raises(ValueError, match="unknown travel_direction"):
            NetworkClosestFacilityService().network_closest_facility(
                demand_points=[
                    {"demand_id": "d1", "geometry": {"type": "Point", "coordinates": [116.001, 39.0]}},
                ],
                facilities=[
                    {"facility_id": "f1", "geometry": {"type": "Point", "coordinates": [116.019, 39.0]}},
                ],
                graph=graph,
                network_dataset=dataset,
                travel_direction="facility-to-incident",  # typo; used to fall through silently
            )
