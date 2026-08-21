"""Issue #693 item 4: within->intersects + null vs zero distinction."""

import math
from app.lib.geo_analysis.aggregation import spatial_aggregate


def _pt(lon, lat, props=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props or {}}


def _poly(coords, props=None):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": props or {}}


def test_within_vs_intersects_boundary_point():
    """Point on polygon boundary must be counted under intersects (aggregate convention)."""
    poly = _poly([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], {"id": 1})
    pt_on_edge = _pt(1, 0.5, {"val": 10})
    res = spatial_aggregate(
        {"type": "FeatureCollection", "features": [pt_on_edge]},
        {"type": "FeatureCollection", "features": [poly]},
        stats=["count", "sum"], value_field="val",
    )
    assert res.success
    feat = res.data["features"][0]
    # intersects counts boundary point; within would not
    assert feat["properties"]["count"] == 1


def test_null_vs_zero_empty_polygon():
    """Polygon with no points: count=0, has_data False, stats null (not 0)."""
    poly_empty = _poly([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], {"id": 1})
    poly_filled = _poly([[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]], {"id": 2})
    pt = _pt(2.5, 2.5, {"val": 0})  # true zero value inside poly_filled
    res = spatial_aggregate(
        {"type": "FeatureCollection", "features": [pt]},
        {"type": "FeatureCollection", "features": [poly_empty, poly_filled]},
        stats=["count", "sum"], value_field="val",
    )
    assert res.success
    by_id = {f["properties"]["id"]: f["properties"] for f in res.data["features"]}
    empty = by_id[1]
    filled = by_id[2]
    # empty polygon: count 0 but no data
    assert empty["count"] == 0
    assert empty["has_data"] is False
    # stats are null (None in JSON, NaN in DataFrame)
    assert empty.get("sum") is None or (isinstance(empty.get("sum"), float) and math.isnan(empty["sum"]))
    # filled polygon with true zero sum: count 1, has_data true, sum 0
    assert filled["count"] == 1
    assert filled["has_data"] is True
    assert filled["sum"] == 0
