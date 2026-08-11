"""Contract test for spatial_stats (Slice 8).

The spatial_stats tool documents its return as
{total_area_m2, total_length_m, count, bbox, centroid} but the implementation
previously returned only {count}. Verify all promised keys are now produced.
"""
from app.services.spatial_analyzer import SpatialAnalyzer


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def test_spatial_stats_returns_promised_keys_polygons():
    fc = _fc([
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[116.38, 39.90], [116.42, 39.90], [116.42, 39.93], [116.38, 39.93], [116.38, 39.90]]]},
         "properties": {}},
    ])
    res = SpatialAnalyzer.statistics(fc)
    assert res.success
    d = res.data
    assert d["count"] == 1
    assert "total_area_m2" in d and d["total_area_m2"] > 0
    assert "total_length_m" in d  # 0 for polygons-only, but key present
    assert "bbox" in d and len(d["bbox"]) == 4
    assert "centroid" in d and len(d["centroid"]) == 2


def test_spatial_stats_lines_have_length():
    fc = _fc([
        {"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [[116.38, 39.90], [116.42, 39.90]]},
         "properties": {}},
    ])
    res = SpatialAnalyzer.statistics(fc)
    assert res.success
    assert res.data["total_length_m"] > 0
    assert res.data["total_area_m2"] == 0


def test_spatial_stats_empty_input():
    res = SpatialAnalyzer.statistics(_fc([]))
    assert res.success
    assert res.data["count"] == 0
