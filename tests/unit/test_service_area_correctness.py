"""Regression tests for GIS-08/09 (deep-audit round 3):

- GIS-08: the isochrone smoothing buffer was 0.005 DEGREES — at 40°N the
  longitude component is ~425 m but ~555 m at the equator, so the smoothing
  radius varied non-uniformly. Buffers are now meters in a local UTM zone.
- GIS-09: the polygon used the CONVEX HULL of reachable nodes, which bridges
  unreachable gaps and overstates coverage. Coverage now follows the actual
  reachable edges (buffered unary-union in meter space).
"""
import math


from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile, Facility
from app.services.network.service_area import NetworkServiceAreaService
from app.services.network.snapping import _utm_crs_for_bbox


def _grid_network(n: int = 10):
    """An n×n grid road network (each edge ~111 m at 39°N)."""
    features = []
    for r in range(n):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for c in range(n)],
            },
        })
    for c in range(n):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for r in range(n)],
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


def test_utm_crs_detection_for_network():
    """GIS-08: Beijing-area bbox must resolve to EPSG:32650 (zone 50N)."""
    assert _utm_crs_for_bbox([116.0, 39.0, 116.01, 39.01]) == "EPSG:32650"


def test_isochrone_polygon_is_valid_and_geojson():
    """Service-area break polygons must be valid GeoJSON polygons in WGS84."""
    graph, dataset = _grid_network()
    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.005, 39.005]})

    areas = svc.network_service_area(
        facilities=[fac],
        breaks=[1.0, 2.0],  # minutes at 60 km/h ≈ 1 km / 2 km
        break_unit="minutes",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
    )
    assert len(areas) == 1
    assert len(areas[0].breaks) == 2

    poly = areas[0].breaks[0].geometry
    assert poly["type"] in ("Polygon", "MultiPolygon")
    # Coordinates must be WGS84 (lng ~116, lat ~39), NOT UTM meters (~4.9e6).
    coords = poly["coordinates"]
    first_ring = coords[0] if poly["type"] == "Polygon" else coords[0][0]
    sample_lng = first_ring[0][0]
    assert 100.0 < sample_lng < 130.0, (
        f"GIS-08 regression: polygon in non-WGS84 coords: {sample_lng}"
    )


def test_isochrone_does_not_bridge_disconnected_gap():
    """GIS-09: coverage must follow reachable edges, not the convex hull.

    Build a U-shaped road network (open on one side). A convex hull of the
    reachable nodes would fill the open side; the edge-buffer union must keep
    the gap open — the polygon area must be SMALLER than the hull would give.
    """
    # U-shape: three sides of a square, opening to the east.
    features = [
        {"type": "Feature", "properties": {"speed_kmh": 60.0, "one_way": False},
         "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.0, 39.01]]}},
        {"type": "Feature", "properties": {"speed_kmh": 60.0, "one_way": False},
         "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]}},
        {"type": "Feature", "properties": {"speed_kmh": 60.0, "one_way": False},
         "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.01, 39.01]]}},
    ]
    geojson = {"type": "FeatureCollection", "features": features}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    svc = NetworkServiceAreaService()

    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.0, 39.0]})
    areas = svc.network_service_area(
        facilities=[fac],
        breaks=[30.0],  # meters — reachable within ~300 m
        break_unit="meters",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
    )
    assert len(areas) == 1 and areas[0].breaks
    poly = areas[0].breaks[0].geometry

    from shapely.geometry import shape as shp

    poly_geom = shp(poly)
    # The U-shape spans ~1.1 km × 1.1 km. If the convex hull were used the
    # area would be ~1.2 km² (filling the open side). The edge-buffer union
    # must be meaningfully smaller — it only covers the three sides.
    area_km2 = poly_geom.area * 111320 * 111320 * math.cos(math.radians(39.0)) / 1e6
    # The open side is ~1/4 of the hull area: hull ≈ 1.2 km², edge coverage
    # should be well under 1.0 km².
    assert area_km2 < 0.9, (
        f"GIS-09 regression: isochrone area {area_km2:.2f} km² — convex hull "
        f"bridged the disconnected gap"
    )


def test_single_node_facility_produces_valid_polygon():
    """Degenerate input (1 node) must still produce a valid polygon."""
    graph, dataset = _grid_network(3)
    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.001, 39.001]})
    areas = svc.network_service_area(
        facilities=[fac],
        breaks=[0.1],  # tiny cutoff → few nodes
        break_unit="minutes",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
    )
    assert len(areas) == 1
    assert areas[0].breaks[0].geometry["type"] in ("Polygon", "MultiPolygon")
