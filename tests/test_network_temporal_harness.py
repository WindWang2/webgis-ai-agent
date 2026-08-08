"""
Benchmark Evaluation Harness Suite for Network Analyst V2 + Temporal GIS Runtime.
Verifies 12 real-world benchmark scenarios across network routing, snapping,
OD matrix, service areas, 15-min life circle accessibility, location allocation,
route optimization VRP, temporal profiling, temporal filtering, temporal trend,
spatiotemporal hotspots, and network + temporal combined slice.
"""
import pytest
from datetime import datetime
from app.services.network.engine import NetworkGraphEngine
from app.services.network.models import TravelProfile
from app.services.temporal.engine import TemporalEngine
from app.services.temporal.models import TemporalFilter


# Sample grid road network in WGS84 around Beijing (approx 0.01 deg ~ 1.1km)
SAMPLE_ROAD_NETWORK = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"id": "edge_1", "oneway": False, "speed_kmh": 40.0},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.390, 39.900], [116.400, 39.900]]
            }
        },
        {
            "type": "Feature",
            "properties": {"id": "edge_2", "oneway": False, "speed_kmh": 40.0},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.400, 39.900], [116.410, 39.900]]
            }
        },
        {
            "type": "Feature",
            "properties": {"id": "edge_3", "oneway": False, "speed_kmh": 40.0},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.400, 39.900], [116.400, 39.910]]
            }
        },
        {
            "type": "Feature",
            "properties": {"id": "edge_4", "oneway": False, "speed_kmh": 40.0},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.400, 39.910], [116.410, 39.910]]
            }
        },
        {
            "type": "Feature",
            "properties": {"id": "edge_5", "oneway": False, "speed_kmh": 40.0},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.410, 39.900], [116.410, 39.910]]
            }
        },
    ]
}


@pytest.mark.asyncio
async def test_benchmark_1_network_shortest_path():
    """Benchmark 1: Network shortest path routing with point snapping."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    result = await engine.solve_shortest_path(
        network=SAMPLE_ROAD_NETWORK,
        origin=[116.391, 39.901],       # Snaps to edge 1
        destination=[116.409, 39.909],  # Snaps to edge 5
        profile=profile,
    )

    assert result.status == "success"
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.total_distance_m > 0
    assert route.total_time_s > 0
    assert route.geometry["type"] == "LineString"
    assert len(route.path_node_ids) >= 2


@pytest.mark.asyncio
async def test_benchmark_2_network_od_matrix():
    """Benchmark 2: Multi-origin x Multi-destination OD cost matrix."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    origins = [[116.390, 39.900], [116.400, 39.910]]
    destinations = [[116.410, 39.900], [116.410, 39.910]]

    result = await engine.solve_od_matrix(
        network=SAMPLE_ROAD_NETWORK,
        origins=origins,
        destinations=destinations,
        profile=profile,
    )

    assert result.status == "success"
    assert len(result.od_matrix) == 4
    for od in result.od_matrix:
        assert od.reachable is True
        assert od.distance_m > 0


@pytest.mark.asyncio
async def test_benchmark_3_closest_facility():
    """Benchmark 3: Network cost closest facility reachability."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    incidents = [[116.391, 39.900]]
    facilities = [
        {"id": "fac_far", "coordinates": [116.410, 39.910]},
        {"id": "fac_near", "coordinates": [116.401, 39.900]},
    ]

    result = await engine.solve_closest_facility(
        network=SAMPLE_ROAD_NETWORK,
        incidents=incidents,
        facilities=facilities,
        profile=profile,
        number_to_find=1,
    )

    assert result.status == "success"
    assert len(result.routes) == 1
    assert result.routes[0].destination_id == "fac_near"


@pytest.mark.asyncio
async def test_benchmark_4_multi_break_service_area():
    """Benchmark 4: Multi-break network isochrone service area."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    facilities = [[116.400, 39.900]]
    breaks = [5.0, 10.0, 15.0]  # minutes

    result = await engine.solve_service_area(
        network=SAMPLE_ROAD_NETWORK,
        facilities=facilities,
        breaks_minutes=breaks,
        profile=profile,
    )

    assert result.status == "success"
    assert len(result.service_area_breaks) == 3
    for b in result.service_area_breaks:
        assert b.geometry["type"] == "Polygon"


@pytest.mark.asyncio
async def test_benchmark_5_15min_accessibility():
    """Benchmark 5: 15-minute life circle accessibility analysis."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="walking", speed_kmh=4.8)

    demand_points = [
        {"id": "pop_1", "coordinates": [116.395, 39.900], "weight": 500},
        {"id": "pop_2", "coordinates": [116.405, 39.900], "weight": 300},
    ]
    facilities = [{"id": "school_1", "coordinates": [116.400, 39.900]}]

    result = await engine.solve_accessibility(
        network=SAMPLE_ROAD_NETWORK,
        demand_layer=demand_points,
        facilities=facilities,
        cutoff_minutes=15.0,
        profile=profile,
    )

    assert result.status == "success"
    assert result.accessibility is not None
    assert result.accessibility.coverage_percentage > 0.0
    assert result.accessibility.served_demand > 0


@pytest.mark.asyncio
async def test_benchmark_6_location_allocation():
    """Benchmark 6: Location-allocation facility site selection."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    candidates = [
        {"id": "cand_a", "coordinates": [116.390, 39.900]},
        {"id": "cand_b", "coordinates": [116.400, 39.900]},
        {"id": "cand_c", "coordinates": [116.410, 39.910]},
    ]
    demands = [
        {"id": "d1", "coordinates": [116.395, 39.900], "weight": 100},
        {"id": "d2", "coordinates": [116.405, 39.910], "weight": 200},
    ]

    result = await engine.solve_location_allocation(
        network=SAMPLE_ROAD_NETWORK,
        candidate_facilities=candidates,
        demand_points=demands,
        n_to_choose=2,
        objective="minimize_cost",
        profile=profile,
    )

    assert result.status == "success"
    assert len(result.allocated_facilities) == 2


@pytest.mark.asyncio
async def test_benchmark_7_route_optimization_vrp():
    """Benchmark 7: Route optimization VRP multi-stop dispatch."""
    engine = NetworkGraphEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    depot = [116.390, 39.900]
    stops = [
        {"id": "stop_2", "coordinates": [116.410, 39.910]},
        {"id": "stop_1", "coordinates": [116.400, 39.900]},
    ]

    result = await engine.solve_optimize_route(
        network=SAMPLE_ROAD_NETWORK,
        depot=depot,
        stops=stops,
        profile=profile,
    )

    assert result.status == "success"
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.total_distance_m > 0


@pytest.mark.asyncio
async def test_benchmark_8_temporal_profiling():
    """Benchmark 8: Temporal dataset profiling & extent discovery."""
    engine = TemporalEngine()

    dataset = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"timestamp": "2026-01-01T00:00:00Z", "val": 10}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"timestamp": "2026-01-02T00:00:00Z", "val": 15}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"timestamp": "2026-01-03T00:00:00Z", "val": 20}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
        ]
    }

    profile = await engine.profile_dataset(dataset=dataset)
    assert profile.temporal_field == "timestamp"
    assert profile.confidence > 0.8
    assert profile.extent is not None


@pytest.mark.asyncio
async def test_benchmark_9_temporal_filtering():
    """Benchmark 9: Temporal range & window filtering."""
    engine = TemporalEngine()

    dataset = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"timestamp": "2026-01-01T00:00:00Z", "val": 10}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"timestamp": "2026-01-15T00:00:00Z", "val": 15}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"timestamp": "2026-02-01T00:00:00Z", "val": 20}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
        ]
    }

    from datetime import timezone
    t_filter = TemporalFilter(
        filter_type="range",
        start_time=datetime(2026, 1, 10, tzinfo=timezone.utc),
        end_time=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )

    res = await engine.execute_filter(dataset=dataset, t_filter=t_filter)
    assert res.status == "success"
    assert "features" in res.result_geojson


@pytest.mark.asyncio
async def test_benchmark_10_temporal_trend():
    """Benchmark 10: Temporal trend analysis & slope metrics."""
    engine = TemporalEngine()

    records = [
        {"timestamp": "2026-01-01T00:00:00Z", "val": 10.0},
        {"timestamp": "2026-01-02T00:00:00Z", "val": 20.0},
        {"timestamp": "2026-01-03T00:00:00Z", "val": 30.0},
        {"timestamp": "2026-01-04T00:00:00Z", "val": 40.0},
    ]

    res = await engine.execute_trend(dataset=records, value_field="val")
    assert res.status == "success"
    assert "slope" in res.trend_metrics


@pytest.mark.asyncio
async def test_benchmark_11_spatiotemporal_hotspot():
    """Benchmark 11: Spatiotemporal hotspot clustering (ST-DBSCAN)."""
    engine = TemporalEngine()

    dataset = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"timestamp": "2026-01-01T00:00:00Z"}, "geometry": {"type": "Point", "coordinates": [116.400, 39.900]}},
            {"type": "Feature", "properties": {"timestamp": "2026-01-01T01:00:00Z"}, "geometry": {"type": "Point", "coordinates": [116.401, 39.901]}},
            {"type": "Feature", "properties": {"timestamp": "2026-01-01T02:00:00Z"}, "geometry": {"type": "Point", "coordinates": [116.400, 39.902]}},
        ]
    }

    res = await engine.execute_spatiotemporal_hotspot(
        dataset=dataset,
        eps_spatial_m=1000.0,
        eps_temporal_days=1.0,
        min_samples=2,
    )

    assert res.status == "success"
    assert len(res.hotspots) >= 1


@pytest.mark.asyncio
async def test_benchmark_12_network_temporal_combined_slice():
    """Benchmark 12: Network + Temporal combined slice (accessibility changing over time)."""
    net_engine = NetworkGraphEngine()
    temp_engine = TemporalEngine()
    profile = TravelProfile(name="driving", speed_kmh=40.0)

    # Multi-temporal facility opening: school_1 open at T1, school_2 opens at T2
    t1_facilities = [{"id": "school_1", "coordinates": [116.400, 39.900]}]
    t2_facilities = [
        {"id": "school_1", "coordinates": [116.400, 39.900]},
        {"id": "school_2", "coordinates": [116.410, 39.910]},
    ]
    demand_points = [
        {"id": "pop_1", "coordinates": [116.395, 39.900], "weight": 500},
        {"id": "pop_2", "coordinates": [116.408, 39.908], "weight": 500},
    ]

    res_t1 = await net_engine.solve_accessibility(
        network=SAMPLE_ROAD_NETWORK,
        demand_layer=demand_points,
        facilities=t1_facilities,
        cutoff_minutes=5.0,
        profile=profile,
    )

    res_t2 = await net_engine.solve_accessibility(
        network=SAMPLE_ROAD_NETWORK,
        demand_layer=demand_points,
        facilities=t2_facilities,
        cutoff_minutes=5.0,
        profile=profile,
    )

    change_res = await temp_engine.execute_change(
        dataset_t1=res_t1.model_dump(),
        dataset_t2=res_t2.model_dump(),
    )

    assert res_t1.accessibility.coverage_percentage <= res_t2.accessibility.coverage_percentage
    assert change_res.status == "success"
