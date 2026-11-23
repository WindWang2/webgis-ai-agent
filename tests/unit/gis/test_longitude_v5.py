"""Harness V5 经度约定 / antimeridian 硬化契约（ADR-0248 D3）。

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


@pytest.mark.parametrize("minx,maxx,expected", [
    # 宽 bbox 但不触 ±180：span>180 单独不构成环绕证据（#1737）
    (-100.0, 100.0, False),
    (-179.0, 179.0, False),
    # 触边 + span>180：真环绕（含 e360 全球表示）
    (-100.0, 180.0, True),
    (0.0, 350.0, True),
    (-180.0, 180.0, True),
    # 触边但 span ≤ 180：贴边合法 bbox，不是环绕
    (-180.0, -170.0, False),
    (170.0, 180.0, False),
])
def test_bbox_crosses_antimeridian_needs_edge_contact(minx, maxx, expected):
    assert bbox_crosses_antimeridian(minx, 0.0, maxx, 10.0) is expected


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


def test_describe_longitude_semantics_keeps_bbox_e360_evidence():
    """#1737：bbox 的 e360 证据（maxx>180）不得被采样几何整体替换掉。

    e360 全球数据集的几何采样常全部落在 0-180（东半球一角）——旧实现
    ``lons = geo_lons or lons`` 把它报成 ambiguous 且不要求归一。
    """
    bbox = [0.0, -10.0, 200.0, 10.0]  # e360 表示：maxx>180
    geom = {"type": "Polygon", "coordinates": [[
        [10.0, 0.0], [20.0, 0.0], [20.0, 5.0], [10.0, 5.0], [10.0, 0.0],
    ]]}
    facts = describe_longitude_semantics(bbox, geom)
    assert facts["convention"] == "e360"
    assert facts["normalization_required"] is True
    assert facts["evidence"] == "bbox+geometry"


def test_describe_longitude_semantics_merges_geometry_pm180_evidence():
    """bbox 与 geometry 证据取并集 —— 几何里的负经度也活。"""
    bbox = [0.0, -10.0, 50.0, 10.0]  # 单独看 ambiguous（全落 0-180）
    geom = {"type": "Point", "coordinates": [-5.0, 1.0]}
    facts = describe_longitude_semantics(bbox, geom)
    assert facts["convention"] == "pm180"
    assert facts["evidence"] == "bbox+geometry"


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


# ------------------------------------------------- 大几何采样（P2 修复）

def _big_multipolygon_second_part_crosses_am():
    """MultiPolygon：第一部分 600 个 pm180 顶点（先把采样预算吃满），
    第二部分跨 antimeridian 的 e360 环（200…250）。"""
    first_ring = [[float(i % 100), float(i % 7)] for i in range(600)]
    first_ring.append(first_ring[0])
    # e360 连续帧：200 → 250 穿越 180 经线
    second_ring = [[200.0 + i * 0.1, 10.0 + i * 0.01] for i in range(40)]
    second_ring.append(second_ring[0])
    return {
        "type": "MultiPolygon",
        "coordinates": [[first_ring], [second_ring]],
    }


def test_large_geometry_sample_covers_later_rings():
    """>512 顶点的几何：DFS 前缀采样只看得见第一个环，后续环的经度被
    系统性丢掉 —— 约定判定与 AM 拆分据此在残缺证据上下全局结论。等分
    索引采样后第二部分进入样本。"""
    from app.lib.gis.longitude import _sample_lons_with_truncation

    coords = _big_multipolygon_second_part_crosses_am()["coordinates"]
    lons, truncated = _sample_lons_with_truncation(coords)
    assert truncated is True
    assert len(lons) <= 512
    assert max(lons) > 180.0, "第二部分的 e360 经度必须进入样本"


def test_large_geometry_e360_convention_detected_and_split():
    """同一几何：约定必须判为 e360（而非 ambiguous），pm180 归一时跨 AM
    的第二部分必须被拆出来（旧前缀采样下两者都静默失败）。"""
    geom = _big_multipolygon_second_part_crosses_am()
    facts = describe_longitude_semantics(None, geom)
    assert facts["convention"] == "e360"
    assert facts["normalization_required"] is True
    assert facts["geometry_sample_truncated"] is True
    assert facts["evidence"] == "geometry"

    out = normalize_geojson_geometry(geom, "pm180")
    assert out["type"] == "GeometryCollection"
    lons = [p[0] for p in _flat_coords(out)]
    assert min(lons) < 0.0 and max(lons) > 0.0, (
        "跨 AM 部分应被拆分并平回 pm180（出现负经度）")


def test_small_geometry_sample_is_unchanged():
    """≤cap 顶点的几何：样本仍是全量 DFS 顺序（无回归）。"""
    from app.lib.gis.longitude import _sample_lons, _sample_lons_with_truncation

    coords = [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [0.0, 0.0]]]
    assert _sample_lons(coords) == [0.0, 1.0, 2.0, 0.0]
    assert _sample_lons_with_truncation(coords) == ([0.0, 1.0, 2.0, 0.0], False)
    assert describe_longitude_semantics(None, {"type": "Polygon",
                                               "coordinates": coords})[
        "geometry_sample_truncated"] is False


def _flat_coords(geometry):
    """递归摊平几何坐标（测试辅助）。"""
    gtype = geometry.get("type")
    if gtype == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _flat_coords(child)
        return
    coords = geometry.get("coordinates")
    if coords is None:
        return
    if coords and isinstance(coords[0], (int, float)):
        yield coords
        return
    for child in coords:
        yield from _flat_coords({"type": "Point", "coordinates": child})
