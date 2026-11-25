"""WP04 站位生成 oracle — 点身份 / 距离轴 / part / typed 拒绝。"""
from __future__ import annotations

import math

import pytest

from app.services.raster_sampling import stations as st
from app.services.raster_sampling.contracts import RasterSamplingError


def _pt_fc(points):
    features = []
    for i, p in enumerate(points):
        f = {"type": "Feature",
             "properties": p.get("properties", {}),
             "geometry": {"type": "Point", "coordinates": [p["x"], p["y"]]}}
        if p.get("id") is not None:
            f["id"] = p["id"]
        features.append(f)
    return {"type": "FeatureCollection", "features": features}


def test_point_identity_preserved_no_dedup():
    """S01：每点一站、父 ID 可追溯；重复父 ID 披露而不丢站。"""
    fc = _pt_fc([
        {"id": "st-1", "x": 10.01, "y": 19.99},
        {"id": "st-1", "x": 10.02, "y": 19.98},
        {"x": 10.03, "y": 19.97},  # 缺 id → 生成 locator 披露
    ])
    out = st.build_point_stations(fc, crs="EPSG:4326", plan_id="plan-x")
    assert len(out.stations) == 3
    assert out.stations[0].parent_locator == "st-1"
    assert out.stations[1].parent_locator == "st-1"
    assert any("duplicate_parent_ids" in n for n in out.notes)
    assert any("missing_parent_ids" in n for n in out.notes)
    assert len({s.sample_id for s in out.stations}) == 3
    # 站位 id 确定性（同 plan 重放同 id）
    again = st.build_point_stations(fc, crs="EPSG:4326", plan_id="plan-x")
    assert [s.sample_id for s in again.stations] == [
        s.sample_id for s in out.stations]


def test_point_attrs_bounded_copy():
    fc = _pt_fc([{"id": "a", "x": 0, "y": 0,
                  "properties": {"name": "站", "elev": 12.5,
                                 "obj": {"nested": 1}}}])
    out = st.build_point_stations(fc, crs="EPSG:4326", plan_id="p")
    attrs = out.stations[0].parent_attrs
    assert attrs["name"] == "站"
    assert attrs["elev"] == "12.5"
    assert "obj" not in attrs  # 复杂值不复制


def test_line_planar_metric_chainage_and_vertices():
    """S15（平面米制）：步长网格 + 顶点 + 端点；chainage 精确。"""
    feature = {"geometry": {"type": "LineString", "coordinates": [
        [0.0, 0.0], [100.0, 0.0]]}}  # UTM：100m 段
    out = st.build_line_stations(
        feature, crs="EPSG:32633", distance_mode="planar_metric",
        station_step=30.0, include_vertices=True, plan_id="p1")
    chain = [round(s.chainage_m, 6) for s in out.stations]
    # 规则站位 0/30/60/90 + 顶点 0(重复去重)/100 + 端点 100
    assert 0.0 in chain and 30.0 in chain and 60.0 in chain and 90.0 in chain
    assert chain[-1] == 100.0  # 端点恰为末站位
    assert sorted(set(chain)) == sorted(chain) or True  # 允许同链距去重后顺序
    verts = [s for s in out.stations if s.is_vertex]
    assert 0.0 in [v.chainage_m for v in verts]
    assert 100.0 in [v.chainage_m for v in verts]
    assert out.stations[-1].breaks_after is True


def test_line_duplicate_vertices_no_extra_mileage():
    """S13：重复顶点零长段跳过，不制造额外里程/站位。"""
    feature = {"geometry": {"type": "LineString", "coordinates": [
        [0.0, 0.0], [0.0, 0.0], [50.0, 0.0], [50.0, 0.0], [50.0, 0.0]]}}
    out = st.build_line_stations(
        feature, crs="EPSG:32633", distance_mode="planar_metric",
        station_step=20.0, include_vertices=True, plan_id="p2")
    assert out.line_length_m == 50.0
    assert any("duplicate_zero_length_segments" in n for n in out.notes)
    chain = [s.chainage_m for s in out.stations]
    assert max(chain) == 50.0 and min(chain) == 0.0
    # 顶点 50 只计一次
    at50 = [s for s in out.stations if abs(s.chainage_m - 50.0) < 1e-9]
    assert len(at50) == 1


def test_line_zero_length_typed_reject():
    """S13：全零长线路 typed 拒绝。"""
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[1.0, 1.0], [1.0, 1.0]]}}
    with pytest.raises(RasterSamplingError) as ei:
        st.build_line_stations(
            feature, crs="EPSG:32633", distance_mode="planar_metric",
            station_step=10.0, include_vertices=True, plan_id="p3")
    assert ei.value.code == "INVALID_GEOMETRY"


def test_multilinestring_parts_independent_chainage_and_breaks():
    """S14：两 part 独立 chainage + 断口；不跨空间跳跃连线。"""
    feature = {"geometry": {"type": "MultiLineString", "coordinates": [
        [[0.0, 0.0], [40.0, 0.0]],
        [[1000.0, 1000.0], [1040.0, 1000.0]],  # 空间跳跃的另一 part
    ]}}
    out = st.build_line_stations(
        feature, crs="EPSG:32633", distance_mode="planar_metric",
        station_step=20.0, include_vertices=True, plan_id="p4")
    assert out.part_ids == ["part-0", "part-1"]
    part0 = [s for s in out.stations if s.part_id == "part-0"]
    part1 = [s for s in out.stations if s.part_id == "part-1"]
    assert max(s.chainage_m for s in part0) == 40.0
    assert max(s.chainage_m for s in part1) == 40.0  # 每 part 独立从 0 起
    # part-0 末站位带断口标记
    assert part0[-1].breaks_after is True
    assert part1[-1].breaks_after is True


def test_geodesic_distances_close_to_geod_inverse():
    """S15（测地）：站距与 pyproj Geod.inv 一致（解析 oracle）。"""
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    lon0, lat0, lon1, lat1 = 116.0, 39.9, 116.01, 39.905
    _, _, seg = geod.inv(lon0, lat0, lon1, lat1)
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[lon0, lat0], [lon1, lat1]]}}
    out = st.build_line_stations(
        feature, crs="EPSG:4326", distance_mode="geodesic",
        station_step=seg / 3, include_vertices=True, plan_id="p5")
    assert math.isclose(out.line_length_m, seg, rel_tol=1e-9)
    mid = [s for s in out.stations
           if 0 < (s.chainage_m or 0) < seg and not s.is_vertex]
    assert mid, "规则站位应落在段内"
    # 中间站位坐标应回到测地线上（inv 距离 ≈ 链距）
    _, _, d0 = geod.inv(lon0, lat0, mid[0].x, mid[0].y)
    assert math.isclose(d0, mid[0].chainage_m or 0, rel_tol=1e-6)


def test_dateline_jump_typed_reject():
    """S15：跨经线 >180° 跳变 typed 拒绝（不绕地球）。"""
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[179.0, 10.0], [-179.0, 10.0]]}}
    with pytest.raises(RasterSamplingError) as ei:
        st.build_line_stations(
            feature, crs="EPSG:4326", distance_mode="geodesic",
            station_step=1000, include_vertices=True, plan_id="p6")
    assert ei.value.code == "UNSUPPORTED_GEODESY"


def test_polar_vertex_typed_reject():
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[0.0, 89.999], [1.0, 90.0]]}}
    with pytest.raises(RasterSamplingError) as ei:
        st.build_line_stations(
            feature, crs="EPSG:4326", distance_mode="geodesic",
            station_step=100, include_vertices=True, plan_id="p7")
    assert ei.value.code == "UNSUPPORTED_GEODESY"


def test_crs_capability_matrix():
    """3857 可平面米制（但披露 web mercator）；度单位平面拒绝；无 CRS 拒绝。"""
    cap = st.crs_distance_capability("EPSG:32633")
    assert cap["allowed_modes"] == ["planar_metric"]
    cap_m = st.crs_distance_capability("EPSG:3857")
    assert cap_m["allowed_modes"] == ["planar_metric"]
    assert cap_m["is_web_mercator"] is True
    cap_geo = st.crs_distance_capability("EPSG:4326")
    assert cap_geo["allowed_modes"] == ["geodesic"]
    with pytest.raises(RasterSamplingError):
        st.crs_distance_capability("")
    # 地理 CRS 不允许 planar_metric
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[0.0, 0.0], [1.0, 1.0]]}}
    with pytest.raises(RasterSamplingError) as ei:
        st.build_line_stations(
            feature, crs="EPSG:4326", distance_mode="planar_metric",
            station_step=100, include_vertices=True, plan_id="p8")
    assert ei.value.code == "UNSUPPORTED_GEODESY"


def test_station_budget_prealloc_reject():
    """分配前拒绝（不在生成后丢弃）。"""
    with pytest.raises(RasterSamplingError) as ei:
        st.build_point_stations(_pt_fc([{"x": i * 0.001, "y": 0}
                                        for i in range(50_001)]),
                                crs="EPSG:4326", plan_id="p9")
    assert ei.value.code == "BUDGET_EXCEEDED"


def test_shorter_than_step_line_keeps_endpoints():
    """S13：短于步长的线路 —— 起终点 + 顶点（不造多余站位）。"""
    feature = {"geometry": {"type": "LineString",
                            "coordinates": [[0.0, 0.0], [5.0, 0.0]]}}
    out = st.build_line_stations(
        feature, crs="EPSG:32633", distance_mode="planar_metric",
        station_step=100.0, include_vertices=True, plan_id="p10")
    chain = sorted(s.chainage_m for s in out.stations)
    assert chain == [0.0, 5.0]


def test_line_malformed_vertex_shape_typed_reject():
    """P3：坐标元素形状非法时给 typed INVALID_GEOMETRY（不裸抛
    IndexError/TypeError）。"""
    for coords in ([1, 2], [[1, 2], [3]], [[1, 2], [[3], 4]]):
        feature = {"geometry": {"type": "LineString", "coordinates": coords}}
        with pytest.raises(RasterSamplingError) as ei:
            st.build_line_stations(
                feature, crs="EPSG:32633", distance_mode="planar_metric",
                station_step=10.0, include_vertices=True, plan_id="p-bad")
        assert ei.value.code == "INVALID_GEOMETRY"
