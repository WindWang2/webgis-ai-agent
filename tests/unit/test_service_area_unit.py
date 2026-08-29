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


def test_multi_break_edge_counts_are_monotone_and_match_reference():
    """#1063: 单趟边分类重构后，多 break 的可达边计数/几何与参考语义一致。

    参考语义：break b 的可达边 = 两端点代价均 ≤ cutoff(b) 的边 + 远端超限
    边的截断段；计数随 break 单调不减。
    """
    from app.services.network.graph_builder import NetworkGraphBuilder
    from app.services.network.models import TravelProfile, Facility
    from app.services.network.service_area import NetworkServiceAreaService
    from shapely.geometry import shape

    n = 8
    features = [{
        "type": "Feature",
        "properties": {"speed_kmh": 40.0, "one_way": False},
        "geometry": {"type": "LineString", "coordinates": [
            [104.0 + j * 0.011, 30.0], [104.0 + (j + 1) * 0.011, 30.0]]},
    } for j in range(n - 1)]
    graph, dataset = NetworkGraphBuilder().build_graph(
        {"type": "FeatureCollection", "features": features}, profile=TravelProfile())
    svc = NetworkServiceAreaService()
    res = svc.network_service_area(
        facilities=[Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [104.0, 30.0]})],
        breaks=[1.0, 2.0, 3.0], break_unit="minutes",
        graph=graph, network_dataset=dataset, profile=TravelProfile(),
    )
    sa = res[0]
    counts = [b.reachable_edge_count for b in sa.breaks]
    assert counts == sorted(counts), f"break 计数应单调不减: {counts}"
    # 40km/h ≈ 666.7 m/min；每边 ~1.02km ≈ 1.53min —— 1 分钟可走 667m，
    # 第一条边的截断段即计入 reachable_edge_count。
    assert counts[0] == 1
    # 2 分钟 ≈ 1333m：第一边全量（1）+ 第二边截断段（1）+ 设施投影至起点
    # 所在边的双向邻接（graph 视 one_way=False 双向）→ 视实际拓扑为 2-3。
    assert counts[1] in (2, 3)
    assert counts[2] >= counts[1]
    areas = [shape(b.geometry).area if b.geometry else 0.0 for b in sa.breaks]
    assert areas == sorted(areas)
