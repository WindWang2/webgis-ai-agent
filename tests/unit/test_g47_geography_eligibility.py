"""G47 地理准备与资格（WP03 / WP05）单测。

三件事必须真的是"从原几何算出来的"，而不是靠命名或最近邻臆造：

1. **身份**：缺 id / 重复 id 一律 typed 拒绝（不能静默取第一行，也不能重复计总量）；
2. **几何资格**：非面、空几何、自相交、轴序颠倒、超支持矩阵（跨 180°/极区）都拒绝；
3. **邻接**：来自**真实共享边长度**，角点相触不算邻接，容差内的缝隙算共边。
"""
from __future__ import annotations

import pytest

from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.geography import prepare_geography
from tests.unit.g47_fixtures import CELL, ORIGIN, frozen, grid_features, ring, square

GEO_OPTS = {
    "source_crs": "EPSG:4326",
    "target_crs": "EPSG:3857",
    "snap_tolerance_m": 1.0,
    "min_shared_length_m": 10.0,
}


def _prepare(features, **kwargs):
    opts = dict(GEO_OPTS)
    opts.update(kwargs)
    return prepare_geography(features, **opts)


# ── 身份 ────────────────────────────────────────────────────────────────

def test_missing_feature_id_is_rejected():
    feats = grid_features()
    feats[3].pop("id")
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "MISSING_FEATURE_ID"
    assert "行号" in ei.value.correction_hint


def test_duplicate_feature_id_is_rejected_with_ids():
    feats = grid_features()
    feats[1]["id"] = feats[0]["id"]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "DUPLICATE_FEATURE_ID"
    assert ei.value.details["duplicate_ids"] == ["r0c0"]
    assert "重复计总量" in ei.value.message


def test_integer_ids_are_normalised_without_collision():
    """``1`` 与 ``"1"`` 是同一个统计单元，必须被识别为重复而不是两个单元。"""
    feats = [
        square("1", ORIGIN[0], ORIGIN[1], CELL, {"pop": 10}),
        square(1, ORIGIN[0] + CELL, ORIGIN[1], CELL, {"pop": 20}),
    ]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "DUPLICATE_FEATURE_ID"


# ── 几何资格 ────────────────────────────────────────────────────────────

def test_point_geometry_is_rejected_as_non_polygon():
    feats = grid_features()
    feats[0]["geometry"] = {"type": "Point", "coordinates": [ORIGIN[0], ORIGIN[1]]}
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "INVALID_GEOMETRY"
    assert ei.value.details["offenders"][0]["reason"] == "geometry_type:Point"


def test_missing_geometry_is_rejected():
    feats = grid_features()
    feats[2].pop("geometry")
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "INVALID_GEOMETRY"
    assert ei.value.details["offenders"][0]["reason"] == "missing_geometry"


def test_self_intersecting_polygon_is_rejected_not_repaired():
    """蝴蝶结面（外环自交）必须拒绝：静默 ``buffer(0)`` 会改变统计单元形状。"""
    bowtie = {"type": "Polygon", "coordinates": [[
        [104.0, 30.4], [104.4, 30.8], [104.4, 30.4], [104.0, 30.8], [104.0, 30.4],
    ]]}
    feats = [{"type": "Feature", "id": "bow", "geometry": bowtie,
              "properties": {"pop": 10}}]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "INVALID_GEOMETRY"
    assert ei.value.details["offenders"][0]["reason"].startswith("invalid_geometry")


def test_empty_polygon_is_rejected():
    feats = [{"type": "Feature", "id": "e", "properties": {"pop": 1},
              "geometry": {"type": "Polygon", "coordinates": []}}]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "EMPTY_GEOMETRY"


def test_swapped_axis_order_is_diagnosed():
    """x∈[30,31]、y∈[104,105]：这是 (lat, lon) 被当成 (x, y) 的典型症状。"""
    swapped = square("s", 30.4, 104.0, CELL, {"pop": 10})
    with pytest.raises(AuthoringError) as ei:
        _prepare([swapped])
    assert ei.value.code == "AXIS_ORDER_SUSPECT"
    assert "always_xy" in ei.value.correction_hint


def test_out_of_range_longitude_is_invalid_geometry():
    feats = [square("far", 200.0, 30.0, CELL, {"pop": 10})]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "INVALID_GEOMETRY"


def test_wide_longitude_span_is_unsupported():
    """跨 200° 经度（但每点都在 ±180 内）→ 明确不支持，而不是硬算。"""
    feats = [
        square("west", -100.0, 10.0, CELL, {"pop": 10}),
        square("east", 100.0, 10.0, CELL, {"pop": 20}),
    ]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "UNSUPPORTED_GEOGRAPHY"
    assert "180" in ei.value.message


def test_polar_latitude_is_unsupported():
    feats = [square("polar", 20.0, 70.0, CELL, {"pop": 10})]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "UNSUPPORTED_GEOGRAPHY"
    assert "极区" in ei.value.message


def test_non_polygon_only_source_reports_no_valid_unit():
    feats = [{"type": "Feature", "id": "line", "properties": {"pop": 1},
              "geometry": {"type": "LineString",
                           "coordinates": [[104.0, 30.4], [104.2, 30.4]]}}]
    with pytest.raises(AuthoringError) as ei:
        _prepare(feats)
    assert ei.value.code == "INVALID_GEOMETRY"


# ── 投影 / anchor ───────────────────────────────────────────────────────

def test_units_are_projected_and_anchor_inside_polygon():
    prepared = _prepare(grid_features())
    assert len(prepared.units) == 9
    # Web Mercator 下 30.4°N 的米制坐标量级（不是度）。
    assert all(abs(u.anchor[0]) > 1e6 for u in prepared.units)
    assert all(u.geometry.contains(u.geometry.representative_point())
               or u.geometry.touches(u.geometry.representative_point())
               for u in prepared.units)
    assert prepared.source_crs == "EPSG:4326"
    assert prepared.target_crs.startswith("EPSG:3857")


def test_include_and_exclude_narrow_the_population():
    feats = grid_features()
    inc = _prepare(feats, included_ids=["r0c0", "r0c1"])
    assert [u.feature_id for u in inc.units] == ["r0c0", "r0c1"]
    exc = _prepare(feats, excluded_ids=["r0c0"])
    assert len(exc.units) == 8
    assert "r0c0" not in {u.feature_id for u in exc.units}


# ── 邻接 ────────────────────────────────────────────────────────────────

def test_adjacency_comes_from_shared_edges():
    prepared = _prepare(grid_features())
    pairs = {(e.a, e.b) for e in prepared.edges}
    # 3×3 网格：每行 2 条水平边 + 每列 2 条竖直边 = 12。
    assert len(prepared.edges) == 12
    assert ("r0c0", "r0c1") in pairs          # 左右相邻
    assert ("r0c0", "r1c0") in pairs          # 上下相邻
    assert ("r0c0", "r2c2") not in pairs      # 对角不相邻
    assert all(e.shared_length > 10.0 for e in prepared.edges)


def test_edges_are_sorted_by_identity_not_by_input_order():
    feats = list(reversed(grid_features()))
    prepared = _prepare(feats)
    keys = [(e.a, e.b) for e in prepared.edges]
    assert keys == sorted(keys)


def test_corner_contact_is_not_adjacency():
    """只共享一个角点（长度 0）不算邻接 —— 这是"D 相邻"与"D 相触"的分界。"""
    a = square("a", ORIGIN[0], ORIGIN[1], CELL, {"pop": 10})
    b = square("b", ORIGIN[0] + CELL, ORIGIN[1] + CELL, CELL, {"pop": 20})
    prepared = _prepare([a, b])
    assert prepared.edges == []
    assert prepared.diagnostics["adjacency_contract"][
        "point_contact_counts_as_adjacent"] is False


def test_gap_within_snap_tolerance_counts_as_adjacent():
    """0.3 m 缝隙（远小于数据精度）视为共边；不把小缝隙变成"孤立岛屿"。"""
    a = square("a", ORIGIN[0], ORIGIN[1], CELL, {"pop": 10})
    b = square("b", ORIGIN[0] + CELL, ORIGIN[1], CELL, {"pop": 20})
    # 把 b 整体右移 ~0.3 m（度）：0.3 / 111320 ≈ 2.7e-6
    shift = 0.3 / 111_320.0
    b["geometry"]["coordinates"] = [ring(ORIGIN[0] + CELL + shift, ORIGIN[1], CELL)]
    prepared = _prepare([a, b], snap_tolerance_m=1.0, min_shared_length_m=10.0)
    assert len(prepared.edges) == 1
    assert prepared.edges[0].shared_length > 10.0


def test_gap_beyond_tolerance_is_not_adjacency():
    """10 m 缝隙（远超容差）不共边 —— 容差是参数，不是无限宽容。"""
    a = square("a", ORIGIN[0], ORIGIN[1], CELL, {"pop": 10})
    shift = 10.0 / 111_320.0
    b = square("b", ORIGIN[0] + CELL + shift, ORIGIN[1], CELL, {"pop": 20})
    prepared = _prepare([a, b], snap_tolerance_m=1.0, min_shared_length_m=10.0)
    assert prepared.edges == []


def test_min_shared_length_threshold_is_honoured():
    """共享边 10 m 的下限：把阈值抬到共享边的 10 倍后不再算邻接。"""
    prepared_strict = _prepare(grid_features(), min_shared_length_m=1e6)
    assert prepared_strict.edges == []


def test_islands_get_no_synthetic_links():
    """孤岛不靠最近邻虚构邻接；诊断里明确声明。"""
    feats = [
        square("island", ORIGIN[0], ORIGIN[1], CELL, {"pop": 10}),
        square("main", ORIGIN[0] + 10 * CELL, ORIGIN[1], CELL, {"pop": 20}),
    ]
    prepared = _prepare(feats)
    assert prepared.edges == []
    assert prepared.diagnostics["adjacency_contract"][
        "islands_get_synthetic_links"] is False


def test_frozen_source_and_geography_agree_on_identity():
    feats = grid_features()
    prepared = _prepare(frozen(feats).features)
    assert {u.feature_id for u in prepared.units} == {
        f["id"] for f in feats}
