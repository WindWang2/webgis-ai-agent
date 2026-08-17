"""Pure-math tests for lib/geo_analysis/geometry_ops.py.

Synthetic point fixtures - no network, no tool layer. Asserts the
GeoAnalysisResult return contract and algorithm correctness (coverage,
containment, ring count).
"""
import math

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import shape, Point

from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_analysis.geometry_ops import voronoi_polygons, convex_hull, multi_ring_buffer


def _synthetic_points(n: int = 10, seed: int = 42) -> dict:
    """N synthetic points scattered around Beijing (116.3-116.5, 39.8-40.0)."""
    import random
    random.seed(seed)
    features = []
    for i in range(n):
        lng = 116.3 + random.uniform(0, 0.2)
        lat = 39.8 + random.uniform(0, 0.2)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": i, "category": "A" if i % 2 == 0 else "B"},
        })
    return {"type": "FeatureCollection", "features": features}


# ── voronoi_polygons ────────────────────────────────────────────────────────


def test_voronoi_returns_geoanalysisresult():
    res = voronoi_polygons(_synthetic_points(10))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_voronoi_produces_cells_covering_inputs():
    pts = _synthetic_points(10)
    res = voronoi_polygons(pts)
    assert res.success
    fc = res.data
    assert fc["type"] == "FeatureCollection"
    # at most N cells (some input points may not get a cell if degenerate)
    assert fc["count"] <= 10
    assert fc["count"] >= 1
    # every input point should fall within (or on the boundary of) some cell.
    # covers (not contains) - contains returns False for boundary points.
    cells = [shape(f["geometry"]) for f in fc["features"]]
    for feat in pts["features"]:
        pt = Point(feat["geometry"]["coordinates"])
        assert any(c.covers(pt) for c in cells), \
            f"input point {feat['properties']['id']} not covered by any Voronoi cell"


def test_voronoi_cells_have_area():
    res = voronoi_polygons(_synthetic_points(10))
    assert res.success
    for feat in res.data["features"]:
        assert "area_km2" in feat["properties"]
        assert feat["properties"]["area_km2"] >= 0


def test_voronoi_too_few_points():
    res = voronoi_polygons(_synthetic_points(2))
    assert not res.success
    assert res.error_type == "ValueError"


# ── convex_hull ──────────────────────────────────────────────────────────────


def test_convex_hull_returns_geoanalysisresult():
    res = convex_hull(_synthetic_points(20))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_convex_hull_contains_all_points():
    pts = _synthetic_points(20)
    res = convex_hull(pts)
    assert res.success
    fc = res.data
    assert fc["count"] == 1
    hull = shape(fc["features"][0]["geometry"])
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    # every input point must be inside or on the hull boundary. The hull is
    # built in UTM then reprojected to WGS84, so boundary points may drift by
    # ~1e-14 (sub-nanometer). Use distance < 1e-9 as "on or inside" rather than
    # covers (which is exact and fails on reprojection precision artifacts).
    for feat in pts["features"]:
        pt = Point(feat["geometry"]["coordinates"])
        assert hull.distance(pt) < 1e-9, \
            f"point {feat['properties']['id']} outside convex hull"


def test_convex_hull_has_area_and_count():
    res = convex_hull(_synthetic_points(20))
    assert res.success
    props = res.data["features"][0]["properties"]
    assert props["feature_count"] == 20
    assert props["area_km2"] > 0


def test_convex_hull_group_by():
    res = convex_hull(_synthetic_points(20), group_by="category")
    assert res.success
    fc = res.data
    # two groups: A and B
    assert fc["count"] == 2
    categories = {f["properties"]["category"] for f in fc["features"]}
    assert categories == {"A", "B"}
    for f in fc["features"]:
        assert f["properties"]["feature_count"] == 10


def test_convex_hull_too_few_points():
    res = convex_hull(_synthetic_points(2))
    assert not res.success
    assert res.error_type == "ValueError"


# ── multi_ring_buffer ───────────────────────────────────────────────────────


def test_multi_ring_buffer_returns_geoanalysisresult():
    res = multi_ring_buffer(_synthetic_points(5), distances=[500, 1000, 1500])
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_multi_ring_buffer_count_matches_distances():
    res = multi_ring_buffer(_synthetic_points(5), distances=[500, 1000, 1500])
    assert res.success
    fc = res.data
    assert fc["count"] == 3
    distance_m_values = [f["properties"]["distance_m"] for f in fc["features"]]
    assert distance_m_values == [500.0, 1000.0, 1500.0]


def test_multi_ring_buffer_merged_rings_are_annular():
    """Merged rings: each ring excludes the inner buffer (annular bands)."""
    res = multi_ring_buffer(_synthetic_points(5), distances=[500, 1000], merge_rings=True)
    assert res.success
    fc = res.data
    # outer ring should be smaller than the full 1000m buffer (it's a band)
    outer_area = fc["features"][1]["properties"]["area_km2"]
    inner_area = fc["features"][0]["properties"]["area_km2"]
    assert outer_area > inner_area  # outer band is larger


def test_multi_ring_buffer_independent_circles():
    """merge_rings=False: each circle covers the full buffer to its distance."""
    res = multi_ring_buffer(_synthetic_points(5), distances=[500, 1000], merge_rings=False)
    assert res.success
    fc = res.data
    assert fc["count"] == 2
    # independent circles: outer should be larger
    assert fc["features"][1]["properties"]["area_km2"] > fc["features"][0]["properties"]["area_km2"]


def test_multi_ring_buffer_empty_distances():
    res = multi_ring_buffer(_synthetic_points(5), distances=[])
    assert not res.success
    assert res.error_type == "ValueError"


def test_multi_ring_buffer_default_distances():
    res = multi_ring_buffer(_synthetic_points(5))
    assert res.success
    fc = res.data
    assert fc["count"] == 3
    assert [f["properties"]["distance_m"] for f in fc["features"]] == [500.0, 1000.0, 1500.0]


# ── Issue #588: unit conversion for non-metre projected CRS ────────────────
#
# to_utm_gdf returns an already-projected input UNCHANGED (state-plane feet
# stays in feet), so a "meters" distance must be converted to CRS units by
# DIVIDING by the axis unit_conversion_factor (metres-per-unit) and the
# area_km2 must be converted back by factor². Pre-fix a 1000 m ring in
# EPSG:2263 became 1000 ft (304.8 m) and area_km2 overreported by ~10.76×.
# Mirrors the #524 buffer_smart regression-test shape.


def _ft_us_to_m_factor(crs_epsg: int) -> float:
    axis = CRS.from_epsg(crs_epsg).axis_info[0]
    return float(axis.unit_conversion_factor)


def test_multi_ring_buffer_state_plane_feet_distance_in_meters():
    """A 1000 m ring on a foot-based state-plane CRS must be ~1000 m on the
    ground with a true metric area_km2 (pre-#588: 304.8 m ring, 10.76× area)."""
    factor = _ft_us_to_m_factor(2263)
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:2263"}},
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [986705.5, 211835.6]},
            "properties": {},
        }],
    }
    res = multi_ring_buffer(fc, distances=[1000])
    assert res.success, res.summary
    assert res.data["count"] == 1
    feat = res.data["features"][0]
    assert feat["properties"]["distance_m"] == 1000.0

    # Ring radius measured back in the source CRS must be ~1000 m (±1%).
    geom_wgs84 = shape(feat["geometry"])
    geom_ft = gpd.GeoSeries([geom_wgs84], crs="EPSG:4326").to_crs("EPSG:2263").iloc[0]
    radius_m = (float(geom_ft.area) / math.pi) ** 0.5 * factor
    assert 990.0 <= radius_m <= 1010.0, f"radius {radius_m} m outside 1000±1%"

    # area_km2 must be the true metric area (π km² for a 1 km radius), not the
    # ft² figure divided by 1e6 (~10.76× overreport pre-fix).
    assert feat["properties"]["area_km2"] == pytest.approx(math.pi, abs=0.05)
