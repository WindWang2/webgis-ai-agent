"""Issue #693 item 7: service_area break_unit validation; seconds->minutes conversion."""

import pytest
from app.services.network.service_area import _break_to_cutoff, _normalize_break_unit


def test_break_unit_minutes_ok():
    assert _normalize_break_unit("minutes") == "minutes"
    assert _break_to_cutoff(5, "minutes", None) == 300.0  # 5 min -> 300 s


def test_break_unit_meters_ok():
    assert _normalize_break_unit("meters") == "meters"
    assert _break_to_cutoff(500, "meters", None) == 500.0


def test_break_unit_seconds_converts():
    # seconds break: with default seconds graph, passthrough; with minutes impedance, divide
    assert _normalize_break_unit("seconds") == "seconds"
    assert _break_to_cutoff(300, "seconds", None) == 300.0


def test_break_unit_invalid_raises():
    with pytest.raises(ValueError, match="Unsupported break_unit"):
        _normalize_break_unit("hours")
    with pytest.raises(ValueError):
        _break_to_cutoff(5, "hours", None)


def test_break_unit_seconds_vs_time_weight():
    from app.services.network.models import Impedance
    # seconds break on minutes-weighted graph: 120s -> 2 min
    assert _break_to_cutoff(120, "seconds", Impedance(name="x", unit="minutes")) == 2.0


def test_break_unit_km_scales_value():
    """#706: the km alias must scale the value ×1000, not just rename the unit."""
    assert _normalize_break_unit("km") == "meters"
    assert _break_to_cutoff(5, "km", None) == 5000.0
    assert _break_to_cutoff(0.5, "km", None) == 500.0


def test_km_isochrone_equivalent_to_meters_end_to_end():
    """#706: km=[5] must reach the same network as meters=[5000]."""
    from app.services.network.graph_builder import NetworkGraphBuilder
    from app.services.network.models import TravelProfile, Facility
    from app.services.network.service_area import NetworkServiceAreaService

    # per-segment features so each ~865 m hop is its own graph edge
    features = [{
        "type": "Feature",
        "properties": {"speed_kmh": 40.0, "one_way": False},
        "geometry": {
            "type": "LineString",
            "coordinates": [[116.0 + i * 0.01, 39.0], [116.0 + (i + 1) * 0.01, 39.0]],
        },
    } for i in range(20)]
    graph, dataset = NetworkGraphBuilder().build_graph(
        {"type": "FeatureCollection", "features": features},
        profile=TravelProfile(),
    )
    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.0, 39.0]})

    by_km = svc.network_service_area(
        facilities=[fac], breaks=[5.0], break_unit="km",
        graph=graph, network_dataset=dataset, profile=TravelProfile(),
    )
    by_m = svc.network_service_area(
        facilities=[fac], breaks=[5000.0], break_unit="meters",
        graph=graph, network_dataset=dataset, profile=TravelProfile(),
    )
    assert by_km[0].breaks[0].reachable_edge_count == by_m[0].breaks[0].reachable_edge_count
    assert by_km[0].breaks[0].reachable_edge_count > 1  # not a 5 m dot


def test_seconds_isochrone_uses_time_weights_end_to_end():
    """#706: seconds must select travel_time_s weights — 540 s ≡ 9 min."""
    from app.services.network.graph_builder import NetworkGraphBuilder
    from app.services.network.models import TravelProfile, Facility
    from app.services.network.service_area import NetworkServiceAreaService

    # per-segment features so each ~865 m hop is its own graph edge
    features = [{
        "type": "Feature",
        "properties": {"speed_kmh": 40.0, "one_way": False},
        "geometry": {
            "type": "LineString",
            "coordinates": [[116.0 + i * 0.01, 39.0], [116.0 + (i + 1) * 0.01, 39.0]],
        },
    } for i in range(20)]
    graph, dataset = NetworkGraphBuilder().build_graph(
        {"type": "FeatureCollection", "features": features},
        profile=TravelProfile(),
    )
    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.0, 39.0]})

    by_s = svc.network_service_area(
        facilities=[fac], breaks=[540.0], break_unit="seconds",
        graph=graph, network_dataset=dataset, profile=TravelProfile(),
    )
    by_min = svc.network_service_area(
        facilities=[fac], breaks=[9.0], break_unit="minutes",
        graph=graph, network_dataset=dataset, profile=TravelProfile(),
    )
    assert by_s[0].breaks[0].reachable_edge_count == by_min[0].breaks[0].reachable_edge_count
    assert by_s[0].breaks[0].reachable_edge_count > 1  # not a 540 m blob
