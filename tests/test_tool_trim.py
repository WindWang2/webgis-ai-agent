"""trim_features tests — payload trim helper for heavy GeoJSON returns."""
import pytest

from app.tools._utils import trim_features


def _point(lon, lat, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props or {},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def test_trim_features_under_threshold_unchanged():
    fc = _fc([_point(116.0, 39.0)])
    out = trim_features(fc, max_features=5000)
    # No _trim envelope when nothing was trimmed.
    assert "_trim" not in out
    assert len(out["features"]) == 1


def test_trim_features_exactly_threshold_unchanged():
    fc = _fc([_point(116.0, 39.0) for _ in range(5000)])
    out = trim_features(fc, max_features=5000)
    assert "_trim" not in out
    assert len(out["features"]) == 5000


def test_trim_features_over_threshold_clips_to_max():
    fc = _fc([_point(116.0, 39.0) for _ in range(5001)])
    out = trim_features(fc, max_features=5000)
    assert len(out["features"]) == 5000
    assert out["_trim"] == {
        "original_count": 5001,
        "kept_count": 5000,
        "precision": 6,
        "reason": "max_features",
    }


def test_trim_features_keeps_first_n_not_random():
    fc = _fc([_point(0, i, idx=i) for i in range(10)])
    out = trim_features(fc, max_features=5)
    kept_indices = [f["properties"]["idx"] for f in out["features"]]
    assert kept_indices == [0, 1, 2, 3, 4]


def test_trim_features_rounds_point_precision():
    fc = _fc([_point(121.123456789, 39.987654321)])
    out = trim_features(fc, max_features=5000, precision=6)
    coords = out["features"][0]["geometry"]["coordinates"]
    assert coords == [121.123457, 39.987654]


def test_trim_features_rounds_polygon_precision():
    poly = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [116.123456789, 39.123456789],
                [117.123456789, 39.123456789],
                [117.123456789, 40.123456789],
                [116.123456789, 39.123456789],
            ]],
        },
        "properties": {},
    }
    out = trim_features(_fc([poly]), precision=6)
    ring = out["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == [116.123457, 39.123457]


def test_trim_features_non_fc_returns_unchanged():
    """非 FeatureCollection 输入：原样返回 + warning。"""
    out = trim_features({"type": "Point", "coordinates": [1, 2]})
    assert out == {"type": "Point", "coordinates": [1, 2]}


def test_trim_features_empty_features_list():
    fc = _fc([])
    out = trim_features(fc)
    assert out["features"] == []
    assert "_trim" not in out


def test_trim_features_default_max_is_5000():
    fc = _fc([_point(0, 0) for _ in range(5001)])
    out = trim_features(fc)  # no max_features arg
    assert len(out["features"]) == 5000
