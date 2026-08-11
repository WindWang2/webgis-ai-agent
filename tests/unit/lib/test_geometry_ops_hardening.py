"""Hardening tests for vector geometry (Slice 6).

Covers convex_hull degenerate-group handling (V-F03), voronoi clip_bounds
(V-F02), and multi_ring_buffer negative-distance validation (V-F10).
"""
import pytest

from app.lib.geo_analysis.geometry_ops import convex_hull, multi_ring_buffer, voronoi_polygons


def _pt_fc(pts):
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {"grp": g}}
        for (x, y, g) in pts
    ]
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- #
# V-F03: convex_hull degenerate groups must not emit Point/LineString
# --------------------------------------------------------------------------- #
def test_convex_hull_skips_degenerate_groups():
    pts = (
        [("116.00", "39.90", "a")]  # 1 point -> degenerate
        + [("116.10", "39.90", "b"), ("116.12", "39.90", "b")]  # 2 collinear -> LineString
        + [(f"116.{i:02d}", f"39.{i:02d}", "c") for i in range(20, 26)]  # 6 pts -> real polygon
    )
    # coords as floats
    fc = _pt_fc([(float(x), float(y), g) for x, y, g in pts])
    res = convex_hull(fc, group_by="grp")
    assert res.success
    geoms = [f["geometry"]["type"] for f in res.data["features"]]
    # Only the 6-point group yields a polygon; degenerate groups are skipped.
    assert "Polygon" in geoms
    assert all(g == "Polygon" for g in geoms), geoms
    groups = {f["properties"]["grp"] for f in res.data["features"]}
    assert "c" in groups
    assert "a" not in groups and "b" not in groups


# --------------------------------------------------------------------------- #
# V-F02: voronoi clip_bounds is honored
# --------------------------------------------------------------------------- #
def test_voronoi_honors_clip_bounds():
    import numpy as np

    rng = np.random.default_rng(3)
    pts = [(116.40 + rng.uniform(-0.05, 0.05), 39.90 + rng.uniform(-0.05, 0.05), "g")
           for _ in range(20)]
    fc = _pt_fc(pts)
    # Clip to a tight box well inside the data extent.
    res = voronoi_polygons(fc, clip_bounds=[116.39, 39.89, 116.41, 39.91])
    assert res.success
    from shapely.geometry import shape, box
    clip = box(116.39, 39.89, 116.41, 39.91)
    for f in res.data["features"]:
        poly = shape(f["geometry"])
        # Every output cell must lie inside the requested clip bounds (allow
        # tiny reprojection rounding between UTM clip and WGS84 test box).
        assert poly.intersection(clip).area == pytest.approx(poly.area, rel=1e-4), (
            "voronoi cell escaped the clip_bounds"
        )


def test_voronoi_without_clip_bounds_uses_data_extent():
    import numpy as np

    rng = np.random.default_rng(4)
    pts = [(116.40 + rng.uniform(-0.02, 0.02), 39.90 + rng.uniform(-0.02, 0.02), "g")
           for _ in range(12)]
    res = voronoi_polygons(_pt_fc(pts))
    assert res.success
    assert len(res.data["features"]) > 0


# --------------------------------------------------------------------------- #
# V-F10: multi_ring_buffer rejects non-positive distances
# --------------------------------------------------------------------------- #
def test_multi_ring_buffer_rejects_negative_distance():
    fc = _pt_fc([(116.40, 39.90, "a")])
    res = multi_ring_buffer(fc, distances=[-100, 500])
    assert not res.success
    assert res.error_type == "ValueError"


def test_multi_ring_buffer_rejects_zero_distance():
    fc = _pt_fc([(116.40, 39.90, "a")])
    res = multi_ring_buffer(fc, distances=[0, 500])
    assert not res.success


def test_multi_ring_buffer_positive_distances_succeed():
    fc = _pt_fc([(116.40, 39.90, "a")])
    res = multi_ring_buffer(fc, distances=[500, 1000])
    assert res.success
    assert len(res.data["features"]) == 2
