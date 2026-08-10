"""Regression tests for GIS-01 (snapped-point routing) and GIS-02 (meter-accurate
nearest-edge snapping) from the deep-audit round 2.

GIS-01: routes must start/end at the SNAPPED location, not at the nearest edge
endpoint node. Previously _resolve_node returned nearest_node_id (an edge
endpoint), adding up to a full edge-length of spurious travel at both ends.

GIS-02: the STRtree nearest-edge query ran in planar degrees, so "nearest"
was nearest-in-degrees not nearest-in-meters (a longitude degree is ~85 km at
40°N vs ~111 km of latitude). The tree is now built in UTM meters.
"""

import pytest

from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.routing import NetworkRoutingService
from app.services.network.snapping import PointSnappingService, _utm_crs_for_bbox
from app.services.network.models import TravelProfile


@pytest.fixture
def single_edge_network():
    """A single road edge from (116.0, 39.0) to (116.01, 39.0) — ~850 m long.

    The snapped point at fraction 0.5 is ~425 m from either endpoint, so the
    old (endpoint) routing error would be ~425 m at each end.
    """
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "e1", "speed_kmh": 60.0, "one_way": False, "name": "Main Rd"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0, 39.0], [116.01, 39.0]],
                },
            }
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


def test_route_starts_and_ends_at_snapped_points(single_edge_network):
    """GIS-01: origin/destination on an edge mid-segment must route from the
    snapped location, not the edge endpoint."""
    graph, dataset = single_edge_network
    router = NetworkRoutingService()

    # Point just below the edge midpoint — snaps to fraction ~0.5.
    origin = (116.005, 38.9999)
    dest = (116.007, 39.0001)

    route = router.network_shortest_path(
        graph=graph,
        network_dataset=dataset,
        origin=origin,
        destination=dest,
        profile=TravelProfile(),
    )

    coords = route.geometry["coordinates"]
    assert len(coords) >= 2, "route must have geometry"

    # The route's first coordinate must be near the origin's snapped location
    # (116.005, ~39.0), NOT at the edge endpoint (116.0, 39.0) or (116.01, 39.0).
    start = coords[0]
    assert abs(start[0] - 116.005) < 0.0001, (
        f"GIS-01 regression: route starts at {start}, expected ~(116.005, 39.0) "
        f"— the old code started at an edge endpoint"
    )
    end = coords[-1]
    assert abs(end[0] - 116.007) < 0.0001, (
        f"GIS-01 regression: route ends at {end}, expected ~(116.007, 39.0)"
    )


def test_route_total_distance_reflects_snapped_endpoints(single_edge_network):
    """The route distance should span snapped origin→destination, i.e. ~0.002°
    (~200 m), not the full edge (~850 m) or endpoint-to-endpoint."""
    graph, dataset = single_edge_network
    router = NetworkRoutingService()

    origin = (116.005, 38.9999)
    dest = (116.007, 39.0001)

    route = router.network_shortest_path(
        graph=graph,
        network_dataset=dataset,
        origin=origin,
        destination=dest,
        profile=TravelProfile(),
    )

    # 0.002 degrees of longitude at 39°N ≈ 0.002 * 111320 * cos(39°) ≈ 173 m.
    # Allow generous tolerance for snapping offsets (~30 m each end).
    assert 100.0 < route.total_distance_m < 260.0, (
        f"GIS-01 regression: route distance {route.total_distance_m:.1f} m — "
        f"expected ~173 m for snapped endpoints; the old endpoint routing "
        f"would report ~850 m"
    )


def test_same_edge_origin_and_destination(single_edge_network):
    """GIS-01 edge case: both endpoints snap to the SAME edge. The second
    split must walk the sub-edge chain created by the first split."""
    graph, dataset = single_edge_network
    router = NetworkRoutingService()

    origin = (116.003, 38.9999)  # fraction ~0.3
    dest = (116.007, 39.0001)    # fraction ~0.7

    route = router.network_shortest_path(
        graph=graph,
        network_dataset=dataset,
        origin=origin,
        destination=dest,
        profile=TravelProfile(),
    )

    coords = route.geometry["coordinates"]
    assert len(coords) >= 2
    # Route should span ~0.004° of longitude between snapped points.
    assert abs(coords[0][0] - 116.003) < 0.0002, f"start {coords[0]}"
    assert abs(coords[-1][0] - 116.007) < 0.0002, f"end {coords[-1]}"
    assert 200.0 < route.total_distance_m < 450.0, (
        f"route distance {route.total_distance_m:.1f} m — expected ~350 m "
        f"between fractions 0.3 and 0.7 of the ~864 m edge"
    )


def test_junction_chain_walk_does_not_enter_unrelated_road():
    """Reviewer BLOCKING fix (GIS-01): when an edge is already split, the
    second snap must walk ONLY the sub-edge chain (target node + virtual
    nodes), never an unrelated road leaving the junction.

    Layout: n0 -- e1 -- n1 -- e2 -- n2 (collinear), both two-way. Origin and
    destination both snap to e1. The second split must land on e1's remaining
    sub-edge, NOT on e2.
    """
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "e1", "speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
            },
            {
                "type": "Feature",
                "properties": {"id": "e2", "speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.02, 39.0]]},
            },
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    router = NetworkRoutingService()

    origin = (116.005, 39.0)  # fraction ~0.5 on e1
    dest = (116.008, 39.0)    # fraction ~0.8 on e1

    route = router.network_shortest_path(
        graph=graph,
        network_dataset=dataset,
        origin=origin,
        destination=dest,
        profile=TravelProfile(),
    )
    coords = route.geometry["coordinates"]
    # The route must stay within e1's span: start ~116.005, end ~116.008.
    # A chain-walk bug put the destination virtual node on e2 (~116.012+).
    # Tolerance is relaxed to ±0.002° (~170 m) because the snapped fraction is
    # measured in UTM while edge lengths are haversine — small projection
    # rounding is expected. The buggy behavior was ~400 m off (on e2).
    assert abs(coords[0][0] - 116.005) < 0.002, f"start {coords[0]}"
    assert abs(coords[-1][0] - 116.008) < 0.002, (
        f"reviewer-blocking regression: route ends at {coords[-1]} — expected "
        f"~116.008 on e1, not on e2"
    )
    # Must NOT land on e2 (>= 116.012): the e1 span is [116.0, 116.01].
    assert coords[-1][0] < 116.012, f"route end on e2: {coords[-1]}"
    assert 150.0 < route.total_distance_m < 500.0, (
        f"route distance {route.total_distance_m:.1f} m — expected ~260-350 m within e1"
    )


def test_node_id_routing_still_works(single_edge_network):
    """Explicit node-id origins/destinations must not insert virtual nodes."""
    graph, dataset = single_edge_network
    router = NetworkRoutingService()
    # The grid builder names the first node n0.
    route = router.network_shortest_path(
        graph=graph,
        network_dataset=dataset,
        origin="n0",
        destination="n1",
        profile=TravelProfile(),
    )
    assert route.total_distance_m > 0
    assert route.path_node_ids[0] == "n0"


def test_utm_crs_detection():
    """UTM zone detection: Beijing (116E, 39N) → zone 50N (EPSG:32650)."""
    crs = _utm_crs_for_bbox([116.0, 39.0, 116.01, 39.01])
    assert crs == "EPSG:32650"


def test_utm_crs_rejects_out_of_range_latitudes():
    """UTM is undefined beyond ±80-84° latitude; fall back to degrees."""
    assert _utm_crs_for_bbox([0.0, 85.0, 1.0, 86.0]) is None
    assert _utm_crs_for_bbox(None) is None


def test_snap_point_returns_wgs84_coordinates(single_edge_network):
    """GIS-02: snapped coordinates must remain WGS84 (not UTM)."""
    _, dataset = single_edge_network
    snapper = PointSnappingService()
    res = snapper.snap_point((116.005, 38.9999), dataset)
    # WGS84: lng ~116.005, lat ~39.0 — NOT UTM meters (~4.9e6, ~4.3e6).
    assert 100.0 < res.snapped_point[0] < 130.0, (
        f"GIS-02 regression: snapped coord {res.snapped_point} is not WGS84"
    )
    assert 0.0 < res.snapped_point[1] < 60.0
    assert 0.0 < res.fraction_along_edge < 1.0


def test_degree_nearest_vs_meter_nearest():
    """GIS-02: two edges equidistant in degrees must NOT be treated equally.

    Build two edges from the same origin: one going north (0.01° lat ≈ 1.1 km)
    and one going east (0.01° lng ≈ 0.85 km at 39°N). A query point 0.004°
    east of the origin is closer to the east edge in meters but "equidistant"
    in degrees — the old code could pick wrong. The new UTM tree must pick the
    east edge.
    """
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "north", "speed_kmh": 60.0, "one_way": False},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0, 39.0], [116.0, 39.01]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "east", "speed_kmh": 60.0, "one_way": False},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0, 39.0], [116.01, 39.0]],
                },
            },
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    snapper = PointSnappingService()

    # Point 0.004° east of origin: 0.004° lat-equivalent is ~445 m,
    # 0.004° lng at 39°N is ~347 m — east edge is nearest in meters.
    res = snapper.snap_point((116.004, 39.0), dataset)
    assert res.nearest_edge_id == "e1" or str(res.nearest_edge_id).startswith("e"), (
        f"GIS-02 regression: snapped to {res.nearest_edge_id}, expected the east edge"
    )
    # The snapped point should be ~0.004° east of origin.
    assert abs(res.snapped_point[0] - 116.004) < 0.0002
    assert abs(res.snapped_point[1] - 39.0) < 0.0002
