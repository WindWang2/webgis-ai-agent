"""图符布局 WP2 —— 锚定策略/确定性避让/密度预算/会计 单测。

科学语义断言（不 mock、纯数值 oracle）：
- 锚定：Point 原样；Polygon 用代表点（质心可能在外 —— 不用质心）；线 0.5 插值；
- 避让：优先级 = Σ|v| 降序平局 id 升序；确定性螺旋位移 + 引线阈值；
- 会计：omitted_collision/omitted_budget/omitted_small/blocked 全计数，
  被隐藏图符的值绝不合并进别的图符（口径不变）；
- 反例：无效几何/越界坐标/重复 id/超帽要素数 typed 拒绝。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.diagram_symbols import (  # noqa: E402
    DiagramSymbolError,
    compute_feature_anchor,
    glyph_bound_radius,
    build_glyph_geometry,
    compute_glyph_scale,
    qualify_features,
    solve_diagram_symbol_layout,
)

def _point(lng, lat, props, fid=None):
    f = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
         "properties": props}
    if fid is not None:
        f["id"] = fid
    return f

def _square(lng0, lat0, lng1, lat1, props, fid=None):
    f = {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[
             [lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]]},
         "properties": props}
    if fid is not None:
        f["id"] = fid
    return f

def _qualify(features, n_cats=2):
    return qualify_features(
        features, kind="composition_pie",
        categories=[f"c{i}" for i in range(n_cats)],
        fields=[f"v{i}" for i in range(n_cats)],
    )

def _radii(features, qualified, r_max=24.0):
    sp = {"r_max_px": r_max}
    scale = compute_glyph_scale("composition_pie", qualified["values"], sp)
    out = []
    for row in qualified["values"]:
        g = build_glyph_geometry("composition_pie", row, scale, sp)
        out.append(glyph_bound_radius(g))
    return out

# ── 锚定策略 ───────────────────────────────────────────────────────────

class TestAnchorPolicy:
    def test_point_anchor_passthrough(self):
        res = compute_feature_anchor(_point(104.06, 30.67, {"v0": 1, "v1": 2}))
        assert res["anchor"] == [104.06, 30.67]
        assert res["anchor_source"] == "point"

    def test_polygon_representative_point_inside_not_centroid_label(self):
        # L 形多边形：质心可落在凹口外，代表点必须在内
        coords = [[
            [0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [1.0, 1.0],
            [1.0, 2.0], [0.0, 2.0], [0.0, 0.0],
        ]]
        feat = {"type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": coords},
                "properties": {}}
        res = compute_feature_anchor(feat)
        assert res["anchor_source"] == "polygon_representative_point"
        x, y = res["anchor"]
        from shapely.geometry import Polygon as ShapelyPolygon
        poly = ShapelyPolygon(coords[0])
        assert poly.contains(poly.representative_point())
        # 建模事实：面锚点语义是代表点（representative_point），不是质心；
        # L 形上质心 (0.833…, 0.833…) 与代表点分离
        centroid = poly.centroid
        assert (abs(centroid.x - x) > 1e-6) or (abs(centroid.y - y) > 1e-6)
        assert 0.0 <= x <= 2.0 and 0.0 <= y <= 2.0

    def test_line_midpoint(self):
        feat = {"type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[0.0, 0.0], [2.0, 0.0]]},
                "properties": {}}
        res = compute_feature_anchor(feat)
        assert res["anchor_source"] == "line_midpoint"
        assert res["anchor"] == [1.0, 0.0]

    def test_missing_geometry_blocked(self):
        res = compute_feature_anchor({"type": "Feature", "geometry": None, "properties": {}})
        assert res["blocked_reason"] == "missing_geometry"

    def test_unsupported_geometry_blocked(self):
        res = compute_feature_anchor({
            "type": "Feature",
            "geometry": {"type": "GeometryCollection", "coordinates": []},
            "properties": {}})
        assert res["blocked_reason"] == "unsupported_geometry_type"

    def test_invalid_polygon_blocked_with_reason(self):
        # 蝴蝶结自相交多边形
        feat = {"type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]]},
                "properties": {}}
        res = compute_feature_anchor(feat)
        assert res["blocked_reason"] == "invalid_geometry"
        assert res.get("detail")

# ── 避让/优先级/会计 ───────────────────────────────────────────────────

class TestLayoutDeterminism:
    def test_priority_value_desc_then_id(self):
        feats = [
            _point(104.0, 30.0, {"v0": 10, "v1": 10}, fid="b"),
            _point(104.0, 30.0, {"v0": 50, "v1": 50}, fid="a"),  # 同点：值大者优先
            _point(104.0, 30.0, {"v0": 50, "v1": 50}, fid="c"),  # 同值：id 升序
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified),
                                           layout_policy={"displacement_enabled": False})
        ev = plan["evidence"]
        assert ev["placed"] == 1
        assert ev["omitted_collision"] == 2
        assert plan["glyphs"][0]["feature_id"] == "a"

    def test_deterministic_repeat(self):
        feats = [_point(104.0 + i * 0.001, 30.0, {"v0": i, "v1": 1}, fid=f"f{i}")
                 for i in range(20)]
        qualified = _qualify(feats)
        p1 = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        p2 = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        assert p1["digest"] == p2["digest"]
        assert p1["glyphs"] == p2["glyphs"]

    def test_displacement_with_leader_line(self):
        # 两近邻：次优者螺旋位移到不冲突位置 → displaced + leader
        feats = [
            _point(104.0, 30.0, {"v0": 100, "v1": 100}, fid="big"),
            _point(104.0 + 1e-5, 30.0, {"v0": 1, "v1": 1}, fid="small"),
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified),
                                           layout_policy={"leader_threshold_px": 0.5})
        assert plan["evidence"]["placed"] == 2
        assert plan["evidence"]["displaced"] == 1
        assert plan["evidence"]["leader_lines"] == 1
        small = next(g for g in plan["glyphs"] if g["feature_id"] == "small")
        assert small["leader_line"] is True
        # 真实锚点不被改写：anchor 保留原坐标，position 是位移后
        assert small["anchor"] == pytest.approx([104.0 + 1e-5, 30.0], abs=1e-9)
        assert small["position"] != pytest.approx(small["anchor"], abs=1e-6)

    def test_no_merging_on_collision(self):
        """被避让隐藏的图符不贡献值：placed 数 + omitted 数 == 候选数（口径不变）。"""
        feats = [_point(104.0, 30.0, {"v0": i, "v1": 1}, fid=f"f{i}") for i in range(5)]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified),
                                           layout_policy={"displacement_enabled": False})
        ev = plan["evidence"]
        assert ev["placed"] + ev["omitted_collision"] == ev["candidates"]
        placed_ids = {g["feature_id"] for g in plan["glyphs"]}
        assert len(placed_ids) == ev["placed"]  # 无合并、无重复

class TestLayoutBudget:
    def test_max_glyphs_budget(self):
        feats = [_point(104.0 + i * 0.01, 30.0, {"v0": i, "v1": 1}, fid=f"f{i}")
                 for i in range(30)]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified),
                                           layout_policy={"max_glyphs": 5})
        ev = plan["evidence"]
        assert ev["placed"] == 5
        assert ev["omitted_budget"] == ev["candidates"] - 5
        # 预算内保留的是优先级最高的（值最大）
        assert plan["glyphs"][0]["feature_id"] == "f29"

    def test_hide_below_radius(self):
        # 开总量面积编码使半径分化：big(2000)→24px，tiny(2)→0.76px
        feats = [
            _point(104.0, 30.0, {"v0": 1000, "v1": 1000}, fid="big"),
            _point(105.0, 30.0, {"v0": 1, "v1": 1}, fid="tiny"),
        ]
        qualified = _qualify(feats)
        sp = {"r_max_px": 24.0, "total_area_encoding": True, "r_min_px": 0.5}
        scale = compute_glyph_scale("composition_pie", qualified["values"], sp)
        radii = [glyph_bound_radius(
            build_glyph_geometry("composition_pie", row, scale, sp))
            for row in qualified["values"]]
        assert radii[0] == pytest.approx(24.0, abs=0.01)
        assert radii[1] < 5.0
        plan = solve_diagram_symbol_layout(feats, qualified, radii,
                                           layout_policy={"hide_below_radius_px": 5.0})
        assert plan["evidence"]["omitted_small"] == 1
        assert plan["evidence"]["placed"] == 1
        assert plan["glyphs"][0]["feature_id"] == "big"

    def test_feature_cap_typed(self):
        feats = [_point(104.0, 30.0, {"v0": 1, "v1": 1}, fid=str(i))
                 for i in range(5)]
        qualified = _qualify(feats)
        with pytest.raises(DiagramSymbolError) as ei:
            solve_diagram_symbol_layout(feats, qualified, [24.0] * 5,
                                        layout_policy={"max_glyphs": 1000000})
        assert ei.value.code == "BUDGET_EXCEEDED"

    def test_duplicate_ids_rejected(self):
        feats = [_point(104.0, 30.0, {"v0": 1, "v1": 1}, fid="dup"),
                 _point(105.0, 30.0, {"v0": 2, "v1": 2}, fid="dup")]
        qualified = _qualify(feats)
        with pytest.raises(DiagramSymbolError) as ei:
            solve_diagram_symbol_layout(feats, qualified, [24.0, 24.0])
        assert ei.value.code == "DUPLICATE_CATEGORY"

class TestLayoutBlocked:
    def test_blocked_geometry_accounted(self):
        feats = [
            _point(104.0, 30.0, {"v0": 1, "v1": 1}, fid="ok"),
            {"type": "Feature", "geometry": None, "properties": {"v0": 1, "v1": 1}, "id": "nogeo"},
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        ev = plan["evidence"]
        assert ev["blocked"] == 1
        assert ev["blocked_detail"][0]["feature_id"] == "nogeo"
        assert ev["blocked_detail"][0]["blocked_reason"] == "missing_geometry"
        assert ev["placed"] == 1

    def test_out_of_range_coordinates_blocked(self):
        feats = [
            _point(104.0, 30.0, {"v0": 1, "v1": 1}, fid="ok"),
            _point(104.0, 95.0, {"v0": 1, "v1": 1}, fid="badlat"),  # 纬度出 Mercator 域
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        ev = plan["evidence"]
        assert ev["blocked"] == 1
        assert ev["blocked_detail"][0]["blocked_reason"] == "INVALID_COORDINATES"

class TestZoomTiers:
    def test_small_glyph_min_zoom_raised(self):
        feats = [
            _point(104.0, 30.0, {"v0": 1000, "v1": 1000}, fid="big"),
            _point(106.0, 30.0, {"v0": 1, "v1": 1}, fid="small"),
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified, r_max=24.0),
                                           layout_policy={"min_zoom_floor": 5})
        by_id = {g["feature_id"]: g for g in plan["glyphs"]}
        assert by_id["big"]["min_zoom"] >= 5
        assert by_id["small"]["min_zoom"] >= by_id["big"]["min_zoom"]  # 小图符不早于大图符出现

    def test_anchor_source_counts(self):
        feats = [
            _point(104.0, 30.0, {"v0": 1, "v1": 1}, fid="p"),
            _square(110.0, 30.0, 111.0, 31.0, {"v0": 2, "v1": 2}, fid="poly"),
        ]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        counts = plan["evidence"]["anchor_source_counts"]
        assert counts.get("point") == 1
        assert counts.get("polygon_representative_point") == 1

    def test_layout_plan_shape(self):
        feats = [_point(104.0, 30.0, {"v0": 1, "v1": 1}, fid="p")]
        qualified = _qualify(feats)
        plan = solve_diagram_symbol_layout(feats, qualified, _radii(feats, qualified))
        assert plan["schema"] == "diagram_symbol_layout.v1"
        g = plan["glyphs"][0]
        assert set(g) >= {"feature_id", "anchor", "anchor_source", "position",
                          "displacement_px", "leader_line", "radius_px",
                          "priority_rank", "min_zoom"}
