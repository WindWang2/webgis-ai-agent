"""GIS-105 regression: declared CRS in target_resolver must never be silently
ignored (raw coordinates labeled WGS84 with confidence=1.0).
"""
import pytest

from app.services.spatial_decision.target_resolver import TargetAreaResolver
from app.utils.coord_transform import gcj02_to_wgs84


def _resolver():
    return TargetAreaResolver(session_store=None, geocode_provider=None)


def test_gcj02_declared_geometry_is_normalized_to_wgs84():
    raw = [116.40, 39.90]
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": raw, "crs": "gcj02"}, "q"
    )
    assert spec is not None and spec.confidence == 1.0
    expected = gcj02_to_wgs84(raw[0], raw[1])
    assert spec.center == pytest.approx((expected[0], expected[1]), abs=1e-6)
    # the offset is on the order of hundreds of metres — raw must not survive
    assert abs(spec.center[0] - raw[0]) > 0.001
    assert abs(spec.center[1] - raw[1]) > 0.001


def test_bd09_declared_geometry_is_normalized_to_wgs84():
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": [116.40, 39.90], "crs": "BD09"}, "q"
    )
    assert spec is not None and spec.confidence == 1.0
    assert abs(spec.center[0] - 116.40) > 0.001
    assert abs(spec.center[1] - 39.90) > 0.001


def test_projected_declared_crs_is_reprojected():
    import pyproj

    t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = t.transform(116.40, 39.90)
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": [x, y], "crs": "EPSG:3857"}, "q"
    )
    assert spec is not None
    assert spec.center[0] == pytest.approx(116.40, abs=1e-4)
    assert spec.center[1] == pytest.approx(39.90, abs=1e-4)


def test_unparseable_declared_crs_fails_loudly():
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": [123456.0, 987654.0], "crs": "EPSG:999999"},
        "q",
    )
    assert spec is not None
    assert spec.confidence == 0.0
    assert spec.geometry is None and spec.center is None and spec.bbox is None
    assert spec.correction_hint and "EPSG:999999" in spec.correction_hint


def test_wgs84_string_declaration_is_identity():
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": [116.40, 39.90], "crs": "WGS84"}, "q"
    )
    assert spec is not None and spec.confidence == 1.0
    assert spec.center == pytest.approx((116.40, 39.90), abs=1e-9)


def test_undeclared_geojson_still_parses():
    spec = _resolver()._try_parse_geojson(
        {"type": "Point", "coordinates": [116.40, 39.90]}, "q"
    )
    assert spec is not None and spec.confidence == 1.0
    assert spec.center == pytest.approx((116.40, 39.90), abs=1e-9)
