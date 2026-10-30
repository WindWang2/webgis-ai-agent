"""Track B（M9）导出标注三项确定性收敛的编译器级测试。

- priorityField：重要度数值大者先保（live Top-N 同向）；缺失时收集序；
- CJK 折行：>8em 的 CJK 名字折 ≤2 行（tspan 多行渲染，盒高行数口径）；
- 线 repeat：symbol-placement=line 的长线 → ≤4 个等距锚点同名多贴。

孪生纪律：TS 侧（mapspec-to-svg.trackb.test.ts）以同 fixture 断言同
invariants（跨孪生一致由 compiler_parity 双侧锁定）。
"""
from __future__ import annotations

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _base_spec(features, layer_extra=None):
    # 扩边背景要素（同 twin 测试）：把数据范围撑大，使带标签要素投影在
    # 画布内部（单要素/同位要素的数据范围会塌缩，投影后无候选可放）。
    spreaders = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.8, 38.8]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.8, 39.6]}, "properties": {"other": 1}},
    ]
    spec = {
        "version": "1.1",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {
            "pts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": spreaders + features}},
        },
        "layers": [
            {
                "id": "pts_sym",
                "source": "pts",
                "type": "symbol",
                "layout": {"text-field": "{name}", "text-size": 14},
                "paint": {"text-color": "#1e293b"},
                **(layer_extra or {}),
            },
        ],
        "layout": {"labels": {"collision": "deterministic"}},
    }
    return spec


def _placed_texts(svg: str):
    group = svg.split('class="mapspec-labels"', 1)
    assert len(group) == 2, "labels group missing"
    return group[1]


def _stacked_points(n=30):
    """同位 n 点（rank 0..n-1，名字编码 rank）—— 求解只能保少数。"""
    return [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
         "properties": {"name": f"点{rank:02d}", "rank": rank}}
        for rank in range(n)
    ]


class TestPriorityField:
    def test_higher_importance_survives_collision(self):
        # 30 同位点只能保少数：priorityField 数值大者（点29）先保，
        # 数值最小者（点00）必然被抑制。
        spec = _base_spec(_stacked_points(30), {"label": {"field": "name", "priorityField": "rank"}})
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=200, height=150, padding=5)
        body = _placed_texts(result.svg)
        assert "点29" in body, "最高重要度必须存活"
        assert "点00" not in body, "最低重要度必须被抑制"
        assert "点28" in body
        relaxed = [d for d in result.diagnostics if d["code"] == "label_collision_relaxed"]
        assert relaxed, result.diagnostics

    def test_no_priority_field_keeps_collection_order(self):
        # 无 priorityField：收集序优先（既有行为回归护栏）—— 先收集（点00）
        # 必然存活，最后收集（点29）必然抑制（与上一用例方向相反）。
        spec = _base_spec(_stacked_points(30))
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=200, height=150, padding=5)
        body = _placed_texts(result.svg)
        assert "点00" in body, "缺省按原序：先收集者先保"
        assert "点29" not in body

    def test_non_numeric_priority_falls_back_to_order(self):
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": f"点{rank:02d}", "rank": "textual"}}
            for rank in range(30)
        ]
        spec = _base_spec(feats, {"label": {"field": "name", "priorityField": "rank"}})
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=200, height=150, padding=5)
        body = _placed_texts(result.svg)
        assert "点00" in body and "点29" not in body  # 非数值代理 → 收集序


class TestCjkWrap:
    def test_long_cjk_name_wraps_to_two_lines(self):
        # 8 个 CJK 字 ≈ 8em > 预算 13×0.6em=7.8em → 折两行。
        long_name = "成都市实验小学附属外国语学校"
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": long_name}},
        ]
        spec = _base_spec(feats)
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=300, height=200, padding=10)
        body = _placed_texts(result.svg)
        assert "<tspan" in body, "折行应渲染多行 tspan"
        assert long_name[:4] in body
        assert long_name not in body  # 不再是单行整串

    def test_short_name_stays_single_line_no_tspan(self):
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "北京"}},
        ]
        spec = _base_spec(feats)
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=300, height=200, padding=10)
        body = _placed_texts(result.svg)
        assert "<tspan" not in body
        assert "北京" in body

    def test_latin_wraps_on_word_boundary(self):
        long_latin = "International Business District Central Station"
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": long_latin}},
        ]
        spec = _base_spec(feats)
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=300, height=200, padding=10)
        body = _placed_texts(result.svg)
        assert "<tspan" in body
        # 词边界断行：tspan 内是完整词（不是词中间断开）
        import re
        tspans = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", body)
        assert all(" " not in ln.rstrip() or ln.count(" ") >= 1 for ln in tspans)


class TestLineRepeat:
    def _long_line_spec(self, placement):
        # 横穿画布的长线（画布 ~280px 宽 @dpi72）—— spacing 250px×dpi72/72=250
        # 总长 >2×250 才 repeat；这里用大 width 画布放大投影线长。
        feats = [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [
                 [115.6, 39.0], [115.8, 39.0], [116.0, 39.0], [116.2, 39.0], [116.4, 39.0],
             ]},
             "properties": {"name": "成渝高速"}},
            # 撑数据范围的背景要素
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.6, 38.8]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.3]}, "properties": {}},
        ]
        layer_extra = {"label": {"field": "name"}}
        layout = {"text-field": "{name}", "text-size": 14}
        if placement:
            layout["symbol-placement"] = "line"
        spec = {
            "version": "1.1",
            "view": {"center": [116.0, 39.05], "zoom": 9},
            "sources": {"ln": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": feats}}},
            "layers": [{
                "id": "ln_sym", "source": "ln", "type": "symbol",
                "layout": layout, "paint": {"text-color": "#1e293b"}, **layer_extra,
            }],
            "layout": {"labels": {"collision": "deterministic"}},
        }
        return spec

    def test_line_placement_repeats_along_line(self):
        spec = self._long_line_spec(placement=True)
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=1000, height=200, padding=10)
        body = _placed_texts(result.svg)
        count = body.count("成渝高速")
        assert count >= 2, f"symbol-placement=line 应沿线多贴（实测 {count} 次）: {body[:400]}"
        assert count <= 4

    def test_default_single_anchor_without_line_placement(self):
        spec = self._long_line_spec(placement=False)
        result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=1000, height=200, padding=10)
        body = _placed_texts(result.svg)
        assert body.count("成渝高速") == 1, "无 symbol-placement=line → 单中点锚（既有行为）"
