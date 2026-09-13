"""Q053（qc-loop round 1）回归锁：properties:null 的 GeoJSON feature 不炸 build_graduated_spec。

RFC 7946 §6.2 允许 feature 的 ``properties`` 为 null（非对象）。历史缺陷：
``f.get("properties", {})`` 在键存在且值为 null 时返回 None，链式
``.get(field)`` 抛 AttributeError，build_graduated_spec 的 5 个工具接线点
（composite_builder / cartography / advanced_spatial / templates）对用户
上传的合法 GeoJSON 直接 500。null-properties feature 应被当作无值跳过。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.thematic_spec import build_graduated_spec


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_null_properties_feature_is_skipped_not_crash():
    geojson = _fc(
        {"type": "Feature", "geometry": None, "properties": None},
        {"type": "Feature", "geometry": None, "properties": {"v": 1.0}},
        {"type": "Feature", "geometry": None, "properties": {"v": 2.0}},
        {"type": "Feature", "geometry": None, "properties": {"v": 3.0}},
        {"type": "Feature", "geometry": None, "properties": {"v": 5.0}},
        {"type": "Feature", "geometry": None, "properties": {"v": 8.0}},
    )
    spec = build_graduated_spec(geojson, "v")
    assert isinstance(spec, dict) and spec


def test_missing_properties_key_still_works():
    geojson = _fc(
        {"type": "Feature", "geometry": None},
        {"type": "Feature", "geometry": None, "properties": {"v": 1.0}},
        {"type": "Feature", "geometry": None, "properties": {"v": 4.0}},
    )
    spec = build_graduated_spec(geojson, "v")
    assert isinstance(spec, dict) and spec
