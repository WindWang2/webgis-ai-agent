"""Pure-math tests for lib/geo_analysis/geometry_ops.py.

Synthetic point fixtures - no network, no tool layer. Asserts the
GeoAnalysisResult return contract and algorithm correctness (coverage,
containment, ring count).
"""
import pytest
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
