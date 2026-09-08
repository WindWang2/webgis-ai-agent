"""Harness V5 经度约定 / antimeridian 硬化契约（ADR-0118 D3）。

验收锚点（Epic V5）：0-360 / antimeridian 有测试 ——
- 约定检测确定性（pm180 / e360 / ambiguous 三值，不猜等价情形）；
- AM 穿越几何拆分（pm180 环绕表示与 e360 连续表示都拆）；
- 不可解析 CRS 收口为 typed InvalidCRS（KNOWN-GAP #1 修复）；
- DatasetProfile/planner 经度语义事实（缺失证据 = ambiguous，不虚构）。
"""
from __future__ import annotations

import pytest

from app.lib.gis.longitude import (
    bbox_crosses_antimeridian,
    geometry_crosses_antimeridian,
    describe_longitude_semantics,
    detect_longitude_convention,
    normalize_geojson_geometry,
    normalize_lon_e360,
    normalize_lon_pm180,
    split_geometry_at_antimeridian,
)


# ---------------------------------------------------------------- 标量换算

def test_lon_wrapping_roundtrip():
    assert normalize_lon_pm180(190.0) == -170.0
    assert normalize_lon_pm180(-180.0) == -180.0
    assert normalize_lon_pm180(180.0) == -180.0
    assert normalize_lon_e360(-170.0) == 190.0
    assert normalize_lon_e360(370.0) == 10.0
    # 往返一致
    for lon in (-180.0, -90.0, 0.0, 90.0, 179.9, 200.0):
        assert normalize_lon_pm180(normalize_lon_e360(lon)) == \
            pytest.approx(normalize_lon_pm180(lon))


# ---------------------------------------------------------------- 约定检测

@pytest.mark.parametrize("lons,expected", [
    ([116.4, 121.5, 100.0], "ambiguous"),   # 全落 0-180：等价，不猜
    ([116.4, -122.0], "pm180"),
    ([190.0, 210.0], "e360"),
    ([], "ambiguous"),
    ([0.0, 180.0], "ambiguous"),
])
def test_detect_convention(lons, expected):
    assert detect_longitude_convention(lons) == expected


# ---------------------------------------------------------------- AM 判定

def test_bbox_crosses_antimeridian():
    # 斐济一带：177E → -179（环绕表示，span≈356 > 180）
    assert bbox_crosses_antimeridian(177.0, -20.0, -179.0, -16.0) is True
    assert bbox_crosses_antimeridian(100.0, 0.0, 120.0, 10.0) is False
    assert bbox_crosses_antimeridian(-10.0, 0.0, 10.0, 10.0) is False


# ---------------------------------------------------------------- AM 拆分

def _polygon(*rings):
    return {"type": "Polygon", "coordinates": [list(r) for r in rings]}


def test_split_e360_polygon_crossing_180():
    """e360 表示的跨 180 多边形（170→190）拆成两片 pm180 合法几何。"""
    poly = {"type": "Polygon", "coordinates": [[
        [170.0, 10.0], [190.0, 10.0], [190.0, 20.0], [170.0, 20.0],
        [170.0, 10.0],
    ]]}
    parts = split_geometry_at_antimeridian(poly)
    assert len(parts) == 2
    for p in parts:
        lons = [c[0] for c in p["coordinates"][0]]
        assert all(-180.0 <= v <= 180.0 for v in lons), lons
        assert not geometry_crosses_antimeridian(lons), \
            f"piece still crosses AM: {lons}"


def test_split_pm180_wrapped_polygon():
    """pm180 环绕表示（-175→175 连成一片）同样拆分。"""
    poly = {"type": "Polygon", "coordinates": [[
        [175.0, 0.0], [-175.0, 0.0], [-175.0, 10.0], [175.0, 10.0],
        [175.0, 0.0],
    ]]}
    parts = split_geometry_at_antimeridian(poly)
    assert len(parts) == 2
    for p in parts:
        lons = [c[0] for c in p["coordinates"][0]]
        assert not geometry_crosses_antimeridian(lons), \
            f"piece still crosses AM: {lons}"


def test_split_non_crossing_polygon_returns_single():
    poly = _polygon(
        [100.0, 0.0], [110.0, 0.0], [110.0, 10.0], [100.0, 10.0],
        [100.0, 0.0],
    )
    parts = split_geometry_at_antimeridian(poly)
    assert len(parts) == 1


def test_normalize_e360_to_pm180_yields_collection_for_am_geometry():
    poly = {"type": "Polygon", "coordinates": [[
        [170.0, 10.0], [190.0, 10.0], [190.0, 20.0], [170.0, 20.0],
        [170.0, 10.0],
    ]]}
    out = normalize_geojson_geometry(poly, target_convention="pm180")
    assert out["type"] == "GeometryCollection"
    assert len(out["geometries"]) == 2


def test_normalize_never_raises_on_junk():
    junk = {"type": "Unknown", "coordinates": None}
    assert normalize_geojson_geometry(junk) == junk


# ---------------------------------------------------------------- 语义事实

def test_describe_longitude_semantics_from_bbox():
    facts = describe_longitude_semantics([177.0, -20.0, -179.0, -16.0])
    assert facts["convention"] == "pm180"
    assert facts["crosses_antimeridian"] is True
    assert facts["normalization_required"] is True
    assert facts["evidence"] == "bbox"


def test_describe_longitude_semantics_absent_evidence():
    facts = describe_longitude_semantics(None)
    assert facts["convention"] == "ambiguous"
    assert facts["crosses_antimeridian"] is False
    assert facts["evidence"] == "absent"  # 绝不虚构


# ---------------------------------------------------------------- KNOWN-GAP #1

def test_unparseable_crs_raises_typed_invalid_crs():
    """不可解析 CRS → InvalidCRS（typed），不再逃逸 pyproj 裸错。"""
    pytest.importorskip("geopandas")
    from app.lib.gis.scientific_errors import InvalidCRS
    from app.lib.geo_processor.core import to_utm_gdf_with_note

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:99999999"}},
        "features": [
            {"type": "Feature", "properties": {"v": 1},
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}
        ],
    }
    with pytest.raises(InvalidCRS) as ri:
        to_utm_gdf_with_note(fc)
    assert "99999999" in str(ri.value)
    assert ri.value.correction_hint  # 科学 correction_hint 通道保留


def test_non_crs_construction_error_still_propagates(monkeypatch):
    """非 CRS 语义的构造异常不误折叠 —— 原样上抛。"""
    pytest.importorskip("geopandas")
    import app.lib.geo_processor.core as core

    class Boom(ValueError):
        pass

    def _bad_gdf(*_a, **_k):
        raise Boom("unrelated failure")

    monkeypatch.setattr(core.gpd, "GeoDataFrame", _bad_gdf)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
        ],
    }
    with pytest.raises(Boom):
        core.to_utm_gdf_with_note(fc)


def test_taxonomy_routes_crs_error_via_pipeline():
    """端到端语义：pyproj 族失败在 harness taxonomy 里是 crs_error。"""
    from app.services.gis_harness.failure_taxonomy import (
        HarnessFailureClass,
        classify_harness_failure,
    )

    try:
        from pyproj.exceptions import CRSError
    except Exception:  # noqa: BLE001
        pytest.skip("pyproj 不可用")
    fc = classify_harness_failure(exception=CRSError("bad EPSG"),
                                  message="reproject failed")
    assert fc is HarnessFailureClass.CRS_ERROR
