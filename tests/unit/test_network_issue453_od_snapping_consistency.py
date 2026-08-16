"""Regression tests for issue #453 (P2): OD-family tools snapped points to the
nearest edge ENDPOINT NODE while ``network_shortest_path`` splits the edge at
the snapped fraction and routes through a virtual mid-edge node (GIS-01).

The same physical OD pair therefore produced different costs across tools —
measured 432.1 m via network_shortest_path vs 864.1 m via closest_facility
(2x; up to ~2 edge lengths in general).

Fix: the OD family (od_matrix, closest_facility, accessibility,
location-allocation, service_area, VRP legs) resolves coordinate /
PointSnappingResult inputs on a single working-copy graph with the same
virtual-node splitting routing uses.
"""
import math

import pytest

from shapely.geometry import shape

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder, haversine_distance
from app.services.network.models import TravelProfile
from app.services.network.od_matrix import NetworkODMatrixService
from app.services.network.routing import NetworkRoutingService
from app.services.network.service_area import NetworkServiceAreaService


def _road_network():
    """Two-way edge e0 (116.0,39.0)→(116.01,39.0) (~865 m) plus a 200 m branch
    e1 continuing east from the (116.01, 39.0) junction."""
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
                "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.01231, 39.0]]},
            },
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


EDGE_LEN = haversine_distance((116.0, 39.0), (116.01, 39.0))  # ~865 m
MID_F02 = (116.002, 39.0)   # fraction ~0.2 from the west endpoint
JUNCTION = (116.01, 39.0)   # node at the east endpoint of e0


class TestODMatrixMatchesRouting:
    def test_od_matrix_cost_equals_shortest_path_cost(self):
        """Issue acceptance: mid-edge demand → junction facility must cost the
        same via the OD matrix as via network_shortest_path."""
        graph, dataset = _road_network()
        router = NetworkRoutingService()
        od = NetworkODMatrixService()

        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=MID_F02, destination=JUNCTION, profile=TravelProfile(),
        )
        pairs = od.network_od_matrix(
            origins=[MID_F02], destinations=[JUNCTION],
            graph=graph, network_dataset=dataset, profile=TravelProfile(),
        )
        assert pairs[0].reachable
        assert pairs[0].distance_m == pytest.approx(route.total_distance_m, abs=3.0), (
            f"OD matrix reports {pairs[0].distance_m:.1f} m vs routing "
            f"{route.total_distance_m:.1f} m for the same OD pair — endpoint "
            f"snapping divergence (#453)"
        )
        assert pairs[0].travel_time_s == pytest.approx(route.total_time_s, abs=1.0)

    def test_od_matrix_mid_edge_to_both_endpoints(self):
        """The OD matrix must see f·L and (1−f)·L from a mid-edge origin —
        endpoint snapping can only produce 0 or L."""
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        pairs = od.network_od_matrix(
            origins=[MID_F02], destinations=[(116.0, 39.0), JUNCTION],
            graph=graph, network_dataset=dataset, profile=TravelProfile(),
        )
        assert pairs[0].distance_m == pytest.approx(EDGE_LEN * 0.2, abs=3.0)
        assert pairs[1].distance_m == pytest.approx(EDGE_LEN * 0.8, abs=3.0)

    def test_od_paths_geometry_starts_at_snapped_point(self):
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        res = od.network_od_paths(
            origins=[MID_F02], destinations=[JUNCTION],
            graph=graph, network_dataset=dataset, profile=TravelProfile(),
        )
        (info,) = res["pairs"].values()
        assert info["reachable"]
        router = NetworkRoutingService()
        route = router.build_route_from_path(
            res["graph_view"], info["path"],
            origin_label="o", destination_label="d",
            profile_name="driving", route_id="r", weight_func=res["weight_func"],
        )
        coords = route.geometry["coordinates"]
        assert abs(coords[0][0] - 116.002) < 1e-4, (
            f"OD-reconstructed route starts at {coords[0]} — expected the "
            f"snapped mid-edge location (116.002, 39.0)"
        )
        assert route.total_distance_m == pytest.approx(EDGE_LEN * 0.8, abs=3.0)


class TestClosestFacilityConsistency:
    def test_closest_facility_cost_matches_routing(self):
        """The issue's reproducer shape: mid-edge demand, junction facility."""
        graph, dataset = _road_network()
        router = NetworkRoutingService()
        fac = NetworkClosestFacilityService()

        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=MID_F02, destination=JUNCTION, profile=TravelProfile(),
        )
        res = fac.network_closest_facility(
            demand_points=[{"demand_id": "d1", "geometry": {"type": "Point", "coordinates": list(MID_F02)}}],
            facilities=[{"facility_id": "f1", "geometry": {"type": "Point", "coordinates": list(JUNCTION)}}],
            graph=graph, network_dataset=dataset,
        )
        assert len(res.routes) == 1, "mid-edge demand → junction facility must match"
        assert res.routes[0].total_distance_m == pytest.approx(route.total_distance_m, abs=3.0), (
            f"closest_facility reports {res.routes[0].total_distance_m:.1f} m vs "
            f"routing {route.total_distance_m:.1f} m (#453 divergence)"
        )
        start = res.routes[0].geometry["coordinates"][0]
        assert abs(start[0] - 116.002) < 1e-4

    def test_closest_facility_mid_edge_beats_far_node_facility(self):
        """A facility at the NEAR endpoint must outrank one at the far
        endpoint for a mid-edge demand — endpoint snapping scored both as the
        same full-edge trip."""
        graph, dataset = _road_network()
        fac = NetworkClosestFacilityService()
        res = fac.network_closest_facility(
            demand_points=[{"demand_id": "d1", "geometry": {"type": "Point", "coordinates": list(MID_F02)}}],
            facilities=[
                {"facility_id": "far", "geometry": {"type": "Point", "coordinates": [116.01, 39.0]}},
                {"facility_id": "near", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}},
            ],
            graph=graph, network_dataset=dataset, target_facility_count=2,
        )
        assert [r.destination_id for r in res.routes] == ["near", "far"], (
            "mid-edge demand must reach the near endpoint cheaper than the far one"
        )
        assert res.routes[0].total_distance_m == pytest.approx(EDGE_LEN * 0.2, abs=3.0)
        assert res.routes[1].total_distance_m == pytest.approx(EDGE_LEN * 0.8, abs=3.0)


class TestServiceAreaConsistency:
    def test_service_area_from_mid_edge_facility(self):
        """A mid-edge facility's reachable network must extend
        symmetrically ±budget around the snapped point — NOT the full
        remaining edge plus branches as endpoint snapping produced.

        Layout: facility at fraction 0.8 of the 865 m edge (173 m from the
        east junction n1, which also carries a 200 m branch east). With a
        250 m budget the branch is only 77 m reachable; endpoint snapping
        (tree rooted at n1) swallowed the WHOLE branch (200 <= 250).
        """
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
                    "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.01231, 39.0]]},
                },
            ],
        }
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
        svc = NetworkServiceAreaService()

        areas = svc.network_service_area(
            facilities=[{"facility_id": "f1", "geometry": {"type": "Point", "coordinates": [116.008, 39.0]}}],
            breaks=[250.0],
            break_unit="meters",
            graph=graph,
            network_dataset=dataset,
            profile=TravelProfile(name="driving", impedance_field="length_m"),
        )
        net_geom = shape(areas[0].breaks[0].reachable_network_geometry)
        xs = [c[0] for linestring in (net_geom.geoms if net_geom.geom_type == "MultiLineString" else [net_geom]) for c in linestring.coords]
        # The 200 m branch (up to lng 116.01231) must NOT be fully reachable:
        # from the snapped point only 77 m of it lie within the 250 m budget.
        assert max(xs) < 116.0123, (
            f"service area swallowed the full branch (max x={max(xs):.6f}) — "
            f"isochrone rooted at an edge endpoint instead of the mid-edge facility"
        )
        # The snapped facility position must be inside the reachable network.
        assert min(xs) - 1e-6 <= 116.008 <= max(xs) + 1e-6


class TestVRPConsistency:
    def test_vrp_legs_start_at_snapped_stops(self):
        graph, dataset = _road_network()
        from app.services.network.vrp import NetworkRouteOptimizationService
        vrp = NetworkRouteOptimizationService()
        route = vrp.optimize_route(
            stops=[(116.002, 39.0), (116.006, 39.0)],
            depot=(116.0, 39.0),
            end_at_depot=True,
            graph=graph, network_dataset=dataset, profile=TravelProfile(),
        )
        coords = route.geometry["coordinates"]
        # The tour must visit the mid-edge stop positions exactly.
        xs = [c[0] for c in coords]
        assert any(abs(x - 116.002) < 1e-4 for x in xs), (
            f"VRP geometry misses the snapped stop 116.002: {xs[:5]}"
        )
        assert any(abs(x - 116.006) < 1e-4 for x in xs), (
            f"VRP geometry misses the snapped stop 116.006: {xs[:5]}"
        )


class TestAdversarial:
    def test_node_id_inputs_still_avoid_copy_semantics(self):
        """Node-id inputs resolve exactly as before (no virtual nodes)."""
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        pairs = od.network_od_matrix(
            origins=["n0"], destinations=["n1"],
            graph=graph, network_dataset=dataset,
        )
        assert pairs[0].distance_m == pytest.approx(EDGE_LEN, abs=1.0)

    def test_mixed_node_and_coordinate_inputs(self):
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        pairs = od.network_od_matrix(
            origins=["n0"], destinations=[JUNCTION, MID_F02],
            graph=graph, network_dataset=dataset,
        )
        assert pairs[0].distance_m == pytest.approx(EDGE_LEN, abs=1.0)
        assert pairs[1].distance_m == pytest.approx(EDGE_LEN * 0.2, abs=3.0)

    def test_reversed_dataset_edge_fraction(self):
        """Snapping may land on the REVERSE dataset edge (geometry v→u); the
        inserted virtual node must still sit at the physical location (#453
        + #446 interaction)."""
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        # A point slightly off-axis snaps to whichever orientation the tree
        # returns; the cost must be symmetric either way.
        p = (116.002, 39.0005)
        pairs = od.network_od_matrix(
            origins=[p], destinations=[(116.0, 39.0), JUNCTION],
            graph=graph, network_dataset=dataset,
        )
        total = pairs[0].distance_m + pairs[1].distance_m
        assert total == pytest.approx(EDGE_LEN, abs=6.0), (
            f"split asymmetric across orientations: {pairs[0].distance_m:.1f} + "
            f"{pairs[1].distance_m:.1f} vs edge {EDGE_LEN:.1f}"
        )

    def test_zero_penalty_graph_untouched_for_node_inputs(self):
        """Node-id-only OD calls must not mutate or copy the caller's graph."""
        graph, dataset = _road_network()
        n_before = graph.number_of_nodes()
        od = NetworkODMatrixService()
        od.network_od_matrix(
            origins=["n0", "n1"], destinations=["n1", "n0"],
            graph=graph, network_dataset=dataset,
        )
        assert graph.number_of_nodes() == n_before
        assert not any(str(n).startswith("vt_") for n in graph.nodes)

    def test_duplicate_coordinates_resolve_fine(self):
        """Origins and destinations at identical coordinates (VRP N×N matrix)
        must yield zero-cost self pairs, not errors."""
        graph, dataset = _road_network()
        od = NetworkODMatrixService()
        pts = [MID_F02, JUNCTION, MID_F02]
        pairs = od.network_od_matrix(
            origins=pts, destinations=pts,
            graph=graph, network_dataset=dataset,
        )
        assert len(pairs) == 9
        for p in pairs:
            assert p.reachable
            assert p.distance_m >= 0.0
            assert not math.isnan(p.travel_time_s)
        self_pairs = [p for p in pairs if p.origin_id == p.destination_id]
        assert all(p.distance_m == pytest.approx(0.0, abs=1e-6) for p in self_pairs)
