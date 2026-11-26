"""point_aggregate 引擎测试 —— 格网确定性 / 成员与计数守恒 oracle。

格网换算 oracle：cell=1000m @lat0 → lon 格宽 = 1000/111320 ≈ 0.008983°、
lat 格宽 = 1000/110574 ≈ 0.009044°；构造同格/邻格点验证 floor 归属。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.lib.cartography.multiscale.point_aggregate import aggregate_points

CELL_M = 1000.0
CELL_LON_DEG = 1000.0 / 111_320.0   # ≈0.008983
CELL_LAT_DEG = 1000.0 / 110_574.0   # ≈0.009044


def _pt(fid: str, lon: float, lat: float,
        props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": dict(props or {}),
    }


class TestCellAssignment:
    def test_same_cell_points_aggregated(self):
        # 同格两点（格 0:0）+ 邻格一点（lon 超过一格宽）
        feats = [
            _pt("a", 0.001, 0.001),
            _pt("b", 0.004, 0.004),
            _pt("c", CELL_LON_DEG * 1.5, 0.001),   # 格 1:0
        ]
        out = aggregate_points(feats, cell_size_m=CELL_M)
        assert out.receipt["representative_count"] == 1
        assert out.receipt["aggregated_count"] == 2
        assert out.receipt["passthrough_count"] == 1  # c（单点格直通）
        rep = next(f for f in out.feature_collection["features"]
                   if str(f.get("id", "")).startswith("agg:"))
        assert rep["properties"]["agg_member_count"] == 2
        assert rep["properties"]["agg_member_ids"] == ["a", "b"]
        assert rep["properties"]["display_only"] is True
        # 质心 oracle
        assert rep["geometry"]["coordinates"] == [
            pytest_approx((0.001 + 0.004) / 2), pytest_approx(0.0025)]

    def test_grid_anchored_at_zero_dataset_independent(self):
        # 同样相对位置的点，无论输入顺序/子集 → 同格同代表
        # 取点避开格边界（lat 边界 ≈ 50.00275、lon 边界 ≈ 10.0063）
        pts = [_pt("a", 10.001, 50.005), _pt("b", 10.003, 50.008)]
        o1 = aggregate_points(pts, cell_size_m=CELL_M, latitude_hint=50.0)
        o2 = aggregate_points(list(reversed(pts)), cell_size_m=CELL_M,
                              latitude_hint=50.0)
        c1 = o1.feature_collection["features"][0]["properties"]["agg_cell"]
        c2 = o2.feature_collection["features"][0]["properties"]["agg_cell"]
        assert c1 == c2

    def test_member_and_sum_conservation(self):
        feats = [
            _pt("a", 0.001, 0.001, {"school_count": 5}),
            _pt("b", 0.004, 0.004, {"school_count": 7}),
            _pt("c", 0.002, 0.002, {"school_count": 2}),
        ]
        out = aggregate_points(feats, cell_size_m=CELL_M,
                               sum_fields=["school_count"])
        rep = next(f for f in out.feature_collection["features"]
                   if str(f.get("id", "")).startswith("agg:"))
        # 计数/总量守恒 oracle：Σ成员 == 代表值 == 14
        assert rep["properties"]["school_count"] == 14
        manifest = out.manifest[rep["properties"]["agg_cell"]]
        assert manifest["member_count"] == 3
        assert manifest["sum_fields"] == {"school_count": 14.0}
        assert sorted(manifest["member_ids"]) == ["a", "b", "c"]
        assert out.receipt["stats_match"] is True

    def test_missing_sum_field_counted(self):
        feats = [
            _pt("a", 0.001, 0.001, {"school_count": 5}),
            _pt("b", 0.004, 0.004),   # 缺字段
        ]
        out = aggregate_points(feats, cell_size_m=CELL_M,
                               sum_fields=["school_count"])
        rep = next(f for f in out.feature_collection["features"]
                   if str(f.get("id", "")).startswith("agg:"))
        assert rep["properties"]["school_count"] == 5
        assert rep["properties"]["agg_missing_school_count"] == 1

    def test_protected_ids_stay_raw(self):
        feats = [
            _pt("landmark", 0.001, 0.001, {"name": "钟楼"}),
            _pt("a", 0.002, 0.002),
            _pt("b", 0.003, 0.003),
        ]
        out = aggregate_points(feats, cell_size_m=CELL_M,
                               protected_ids=frozenset({"landmark"}))
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert "landmark" in ids
        # landmark 直通；a/b 同格聚合
        rep = next(f for f in out.feature_collection["features"]
                   if str(f.get("id", "")).startswith("agg:"))
        assert rep["properties"]["agg_member_ids"] == ["a", "b"]


class TestHonestSemantics:
    def test_singletons_passthrough(self):
        feats = [_pt("a", 0.001, 0.001), _pt("b", 5.0, 5.0)]
        out = aggregate_points(feats, cell_size_m=CELL_M)
        assert out.receipt["representative_count"] == 0
        assert out.receipt["passthrough_count"] == 2
        assert any("单点格" in d for d in out.receipt["disclosures"])

    def test_non_point_and_bad_geometry_passthrough(self):
        feats = [
            _pt("a", 0.001, 0.001),
            _pt("b", 0.002, 0.002),
            {"type": "Feature", "id": "ln",
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": {}},
            _pt("bad", float("nan"), 0.0),
        ]
        out = aggregate_points(feats, cell_size_m=CELL_M)
        assert out.receipt["parse_failures"] == 2
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert "ln" in ids and "bad" in ids

    def test_nonpositive_cell_noop(self):
        feats = [_pt("a", 0.001, 0.001)]
        out = aggregate_points(feats, cell_size_m=0)
        assert out.receipt["representative_count"] == 0
        assert out.feature_collection["features"][0]["id"] == "a"

    def test_empty_input(self):
        out = aggregate_points([], cell_size_m=CELL_M)
        assert out.feature_collection["features"] == []
        assert out.receipt["stats_match"] is True

    def test_determinism_and_input_not_mutated(self):
        feats = [
            _pt("a", 0.001, 0.001, {"school_count": 5}),
            _pt("b", 0.004, 0.004, {"school_count": 7}),
            _pt("c", CELL_LON_DEG * 1.5, 0.001),
        ]
        o1 = aggregate_points(feats, cell_size_m=CELL_M,
                              sum_fields=["school_count"])
        o2 = aggregate_points(feats, cell_size_m=CELL_M,
                              sum_fields=["school_count"])
        assert o1.manifest == o2.manifest
        assert o1.feature_collection == o2.feature_collection
        # 输入不被修改
        assert feats[0]["properties"]["school_count"] == 5
        assert all(f.get("properties", {}).get("display_only") is not True
                   for f in feats)


def pytest_approx(v: float) -> float:
    """坐标断言按 1e-9 度归一（测试内独立实现，绕过 pytest.approx 对
    列表精确相等的限制）。"""
    return round(v, 9)
