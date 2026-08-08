"""
Unit tests for Network Analyst V2 Engine components.
Tests graph building, snapping, routing, OD matrix, closest facility,
service area, accessibility, location-allocation, VRP optimization, and the unified engine facade.
"""
import pytest
from typing import Dict, Any

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Impedance,
    Barrier,
    Facility,
    DemandPoint,
    PointSnappingResult,
    Route,
    ODPair,
    AccessibilityResult,
    NetworkAnalysisResult,
)
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.snapping import PointSnappingService
from app.services.network.routing import NetworkRoutingService
from app.services.network.od_matrix import NetworkODMatrixService
from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.service_area import NetworkServiceAreaService
from app.services.network.accessibility import NetworkAccessibilityService
from app.services.network.allocation import NetworkLocationAllocationService
from app.services.network.vrp import NetworkRouteOptimizationService
from app.services.network.engine import NetworkGraphEngine


@pytest.fixture
def sample_geojson_grid() -> Dict[str, Any]:
    """
    Creates a simple 2x2 grid street network in GeoJSON format.
    Nodes at:
      (0.0, 0.0), (1.0, 0.0), (2.0, 0.0)
      (0.0, 1.0), (1.0, 1.0), (2.0, 1.0)
      (0.0, 2.0), (1.0, 2.0), (2.0, 2.0)
    All coordinates in (lng, lat) decimal degrees around origin for clear distance calculations.
    """
    features = [
        # Horizontal lines
        {
            "type": "Feature",
            "properties": {"id": "h1", "speed_kmh": 60.0, "one_way": False, "name": "South St"},
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]},
        },
        {
            "type": "Feature",
            "properties": {"id": "h2", "speed_kmh": 40.0, "one_way": False, "name": "Center St"},
            "geometry": {"type": "LineString", "coordinates": [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]},
        },
        {
            "type": "Feature",
            "properties": {"id": "h3", "speed_kmh": 30.0, "one_way": False, "name": "North St"},
            "geometry": {"type": "LineString", "coordinates": [[0.0, 2.0], [1.0, 2.0], [2.0, 2.0]]},
        },
        # Vertical lines
        {
            "type": "Feature",
            "properties": {"id": "v1", "speed_kmh": 50.0, "one_way": False, "name": "West Ave"},
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]},
        },
        {
            "type": "Feature",
            "properties": {"id": "v2", "speed_kmh": 50.0, "one_way": False, "name": "Central Ave"},
            "geometry": {"type": "LineString", "coordinates": [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]},
        },
        {
            "type": "Feature",
            "properties": {"id": "v3", "speed_kmh": 50.0, "one_way": True, "name": "East Ave (OneWay North)"},
            "geometry": {"type": "LineString", "coordinates": [[2.0, 0.0], [2.0, 1.0], [2.0, 2.0]]},
        },
    ]
    return {"type": "FeatureCollection", "features": features}


class TestNetworkGraphBuilder:
    """Tests for NetworkGraphBuilder including splitting, snapping, speed/cost, and LRU cache."""

    def test_build_graph_from_geojson(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        profile = TravelProfile(name="driving", speed_kmh=40.0)
        graph, dataset = builder.build_graph(sample_geojson_grid, profile=profile)

        assert graph.number_of_nodes() > 0
        assert graph.number_of_edges() > 0
        assert isinstance(dataset, NetworkDataset)
        assert dataset.node_count == graph.number_of_nodes()
        assert dataset.edge_count == graph.number_of_edges()

    def test_one_way_direction(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, _ = builder.build_graph(sample_geojson_grid)

        # Edge v3 (2.0, 0.0) -> (2.0, 1.0) is one-way northbound
        node_start = None
        node_end = None
        for n, data in graph.nodes(data=True):
            if abs(data["x"] - 2.0) < 1e-4 and abs(data["y"] - 0.0) < 1e-4:
                node_start = n
            if abs(data["x"] - 2.0) < 1e-4 and abs(data["y"] - 1.0) < 1e-4:
                node_end = n

        assert node_start is not None and node_end is not None
        assert graph.has_edge(node_start, node_end)
        # Should NOT have reverse edge (node_end -> node_start) because it's one-way
        assert not graph.has_edge(node_end, node_start)

    def test_intersection_splitting_and_endpoint_snapping(self):
        crossing_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": "l1", "speed_kmh": 40.0},
                    "geometry": {"type": "LineString", "coordinates": [[0.0, 0.5], [1.0, 0.5]]},
                },
                {
                    "type": "Feature",
                    "properties": {"id": "l2", "speed_kmh": 40.0},
                    "geometry": {"type": "LineString", "coordinates": [[0.5, 0.0], [0.5, 1.0]]},
                },
            ],
        }
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(crossing_data, split_intersections=True)

        has_intersection_node = any(
            abs(data["x"] - 0.5) < 1e-4 and abs(data["y"] - 0.5) < 1e-4
            for _, data in graph.nodes(data=True)
        )
        assert has_intersection_node

    def test_lru_cache_fingerprint(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        builder.clear_cache()

        profile1 = TravelProfile(name="driving", speed_kmh=50.0)
        graph1, dataset1 = builder.build_graph(sample_geojson_grid, profile=profile1, use_cache=True)

        graph2, dataset2 = builder.build_graph(sample_geojson_grid, profile=profile1, use_cache=True)

        assert graph1 is graph2
        assert dataset1 is dataset2

        profile2 = TravelProfile(name="walking", speed_kmh=5.0)
        graph3, _ = builder.build_graph(sample_geojson_grid, profile=profile2, use_cache=True)

        assert graph3 is not graph1


class TestPointSnappingService:
    """Tests for PointSnappingService snapping points onto network edges."""

    def test_snap_point(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        _, dataset = builder.build_graph(sample_geojson_grid)

        snapper = PointSnappingService()
        result = snapper.snap_point((0.5, 0.001), dataset, max_tolerance_m=500.0)

        assert isinstance(result, PointSnappingResult)
        assert abs(result.snapped_point[0] - 0.5) < 1e-3
        assert abs(result.snapped_point[1] - 0.0) < 1e-3
        assert result.distance_to_network_m < 200.0
        assert result.confidence > 0.5
        assert result.correction_hint is None

    def test_snap_point_tolerance_breach(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        _, dataset = builder.build_graph(sample_geojson_grid)

        snapper = PointSnappingService()
        result = snapper.snap_point((10.0, 10.0), dataset, max_tolerance_m=10.0)

        assert result.confidence == 0.0
        assert result.correction_hint is not None
        assert "exceeding tolerance" in result.correction_hint


class TestNetworkRoutingService:
    """Tests for NetworkRoutingService shortest path routing and barrier avoidance."""

    def test_shortest_path_dijkstra(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        router = NetworkRoutingService()
        route = router.network_shortest_path(
            graph=graph,
            network_dataset=dataset,
            origin=(0.0, 0.0),
            destination=(2.0, 0.0),
            profile=TravelProfile(name="driving", speed_kmh=60.0),
            impedance=Impedance(name="travel_time_s"),
        )

        assert isinstance(route, Route)
        assert route.total_distance_m > 0
        assert route.total_time_s > 0
        assert route.geometry["type"] == "LineString"
        assert len(route.geometry["coordinates"]) >= 2
        assert len(route.directions) > 0

    def test_shortest_path_astar(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        router = NetworkRoutingService()
        route = router.network_shortest_path(
            graph=graph,
            network_dataset=dataset,
            origin=(0.0, 0.0),
            destination=(2.0, 2.0),
            algorithm="astar",
        )

        assert isinstance(route, Route)
        assert route.total_distance_m > 0

    def test_barrier_avoidance(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        barrier = Barrier(
            barrier_id="b1",
            barrier_type="point",
            geometry={"type": "Point", "coordinates": [1.0, 0.0]},
            impedance_factor=float("inf"),
        )

        router = NetworkRoutingService()
        route_blocked = router.network_shortest_path(
            graph=graph,
            network_dataset=dataset,
            origin=(0.0, 0.0),
            destination=(2.0, 0.0),
            barriers=[barrier],
        )

        coords = route_blocked.geometry["coordinates"]
        has_blocked_point = any(abs(c[0] - 1.0) < 1e-4 and abs(c[1] - 0.0) < 1e-4 for c in coords)
        assert not has_blocked_point


class TestNetworkODMatrixService:
    """Tests for NetworkODMatrixService batch matrix calculations."""

    def test_od_matrix_multi_source(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        od_service = NetworkODMatrixService()
        origins = [(0.0, 0.0), (0.0, 2.0)]
        destinations = [(2.0, 0.0), (2.0, 2.0)]

        matrix = od_service.network_od_matrix(
            origins=origins,
            destinations=destinations,
            graph=graph,
            network_dataset=dataset,
        )

        assert len(matrix) == len(origins) * len(destinations)
        for pair in matrix:
            assert isinstance(pair, ODPair)
            assert pair.reachable is True
            assert pair.distance_m > 0
            assert pair.travel_time_s > 0


class TestNetworkClosestFacilityService:
    """Tests for NetworkClosestFacilityService finding nearest facilities."""

    def test_closest_facility(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        fac_service = NetworkClosestFacilityService()
        facilities = [
            Facility(facility_id="f1", name="Hospital West", geometry={"type": "Point", "coordinates": [0.0, 2.0]}),
            Facility(facility_id="f2", name="Hospital East", geometry={"type": "Point", "coordinates": [2.0, 0.0]}),
        ]
        demand_points = [
            DemandPoint(demand_id="d1", weight=100.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
        ]

        res = fac_service.network_closest_facility(
            demand_points=demand_points,
            facilities=facilities,
            graph=graph,
            network_dataset=dataset,
            target_facility_count=1,
        )

        assert isinstance(res, NetworkAnalysisResult)
        assert len(res.routes) == 1
        assert res.routes[0].destination_id in ["f1", "f2"]


class TestNetworkServiceAreaService:
    """Tests for NetworkServiceAreaService isochrone polygon boundaries."""

    def test_service_area_breaks(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        sa_service = NetworkServiceAreaService()
        facilities = [
            Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [1.0, 1.0]}),
        ]
        breaks = [5000.0, 10000.0]

        service_areas = sa_service.network_service_area(
            facilities=facilities,
            breaks=breaks,
            break_unit="meters",
            graph=graph,
            network_dataset=dataset,
        )

        assert len(service_areas) == 1
        sa = service_areas[0]
        assert len(sa.breaks) == len(breaks)
        for brk in sa.breaks:
            assert brk.geometry["type"] in ["Polygon", "MultiPolygon"]


class TestNetworkAccessibilityService:
    """Tests for NetworkAccessibilityService 15-min life circle and 2SFCA."""

    def test_accessibility_15min(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        acc_service = NetworkAccessibilityService()
        demand_points = [
            DemandPoint(demand_id="d1", weight=50.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
            DemandPoint(demand_id="d2", weight=100.0, geometry={"type": "Point", "coordinates": [2.0, 2.0]}),
        ]
        facilities = [
            Facility(facility_id="f1", capacity=1.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
        ]

        res = acc_service.network_accessibility(
            demand_points=demand_points,
            facilities=facilities,
            graph=graph,
            network_dataset=dataset,
            cutoff_minutes=15.0,
            method="15min_circle",
        )

        assert isinstance(res, AccessibilityResult)
        assert res.total_demand == 150.0
        assert res.served_demand > 0
        assert 0.0 <= res.coverage_percentage <= 100.0

    def test_accessibility_2sfca(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        acc_service = NetworkAccessibilityService()
        demand_points = [
            DemandPoint(demand_id="d1", weight=50.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
        ]
        facilities = [
            Facility(facility_id="f1", capacity=10.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
        ]

        res = acc_service.network_accessibility(
            demand_points=demand_points,
            facilities=facilities,
            graph=graph,
            network_dataset=dataset,
            cutoff_minutes=15.0,
            method="2sfca",
        )

        assert isinstance(res, AccessibilityResult)
        assert len(res.per_zone_metrics) == 1
        assert "accessibility_score" in res.per_zone_metrics[0]


class TestNetworkLocationAllocationService:
    """Tests for NetworkLocationAllocationService P-Median and Max Coverage."""

    def test_location_allocation_p_median(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        alloc_service = NetworkLocationAllocationService()
        candidates = [
            Facility(facility_id="c1", geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
            Facility(facility_id="c2", geometry={"type": "Point", "coordinates": [2.0, 2.0]}),
        ]
        demands = [
            DemandPoint(demand_id="d1", weight=10.0, geometry={"type": "Point", "coordinates": [0.0, 0.1]}),
            DemandPoint(demand_id="d2", weight=20.0, geometry={"type": "Point", "coordinates": [1.9, 2.0]}),
        ]

        res = alloc_service.location_allocation(
            candidate_facilities=candidates,
            demand_points=demands,
            p_count=1,
            problem_type="p_median",
            graph=graph,
            network_dataset=dataset,
        )

        assert isinstance(res, NetworkAnalysisResult)
        assert len(res.allocated_facilities) == 1

    def test_location_allocation_max_coverage(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        alloc_service = NetworkLocationAllocationService()
        candidates = [
            Facility(facility_id="c1", geometry={"type": "Point", "coordinates": [0.0, 0.0]}),
            Facility(facility_id="c2", geometry={"type": "Point", "coordinates": [2.0, 2.0]}),
        ]
        demands = [
            DemandPoint(demand_id="d1", weight=10.0, geometry={"type": "Point", "coordinates": [0.0, 0.1]}),
        ]

        res = alloc_service.location_allocation(
            candidate_facilities=candidates,
            demand_points=demands,
            p_count=1,
            problem_type="max_coverage",
            graph=graph,
            network_dataset=dataset,
        )

        assert isinstance(res, NetworkAnalysisResult)
        assert len(res.allocated_facilities) == 1


class TestNetworkRouteOptimizationService:
    """Tests for NetworkRouteOptimizationService TSP / 2-opt VRP route optimization."""

    def test_optimize_route_tsp(self, sample_geojson_grid):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(sample_geojson_grid)

        vrp_service = NetworkRouteOptimizationService()
        stops = [(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)]

        route = vrp_service.optimize_route(
            stops=stops,
            depot=(0.0, 0.0),
            end_at_depot=True,
            graph=graph,
            network_dataset=dataset,
        )

        assert isinstance(route, Route)
        assert route.total_distance_m > 0
        assert route.geometry["type"] == "LineString"


class TestNetworkGraphEngine:
    """Tests for NetworkGraphEngine unified facade."""

    def test_engine_orchestration(self, sample_geojson_grid):
        engine = NetworkGraphEngine()
        graph, dataset = engine.build_network(sample_geojson_grid)

        # Snap
        snap = engine.snap_point((0.5, 0.001), dataset)
        assert snap.snapped_point is not None

        # Route
        route = engine.shortest_path((0.0, 0.0), (2.0, 2.0), dataset)
        assert route.total_distance_m > 0

        # OD matrix
        od = engine.od_matrix([(0.0, 0.0)], [(2.0, 2.0)], dataset)
        assert len(od) == 1

        # Closest facility
        fac_res = engine.closest_facility(
            demand_points=[(0.0, 0.0)],
            facilities=[(2.0, 2.0)],
            network_dataset=dataset,
        )
        assert len(fac_res.routes) == 1

        # Service Area
        sa = engine.service_area(
            facilities=[(1.0, 1.0)],
            breaks=[5.0],
            network_dataset=dataset,
        )
        assert len(sa) == 1

        # Accessibility
        acc = engine.accessibility(
            demand_points=[DemandPoint(demand_id="d1", weight=1.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]})],
            facilities=[Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [0.0, 0.0]})],
            network_dataset=dataset,
        )
        assert acc.total_demand == 1.0

        # Location allocation
        alloc = engine.location_allocation(
            candidate_facilities=[Facility(facility_id="c1", geometry={"type": "Point", "coordinates": [0.0, 0.0]})],
            demand_points=[DemandPoint(demand_id="d1", weight=1.0, geometry={"type": "Point", "coordinates": [0.0, 0.0]})],
            p_count=1,
            network_dataset=dataset,
        )
        assert len(alloc.allocated_facilities) == 1

        # Route optimization
        vrp = engine.optimize_route(
            stops=[(0.0, 0.0), (2.0, 2.0)],
            network_dataset=dataset,
        )
        assert vrp.total_distance_m > 0
