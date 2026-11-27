"""diagram_symbol SVG 孪生 v1 —— golden parity + 诚实降级 + 确定性。

锁定（与 oracle fixtures 数值相等，非近似）：
- 图符几何：pie/donut 扇形弧端点坐标 == fixture 角度投影在布局中心的
  逐位结果（0°=12 点钟顺时针、SVG y-down sweep=1）；bars rect y0/y1 ==
  glyph-local 段（y 向上 → SVG y 向下翻折）；segmented 扇形半径 ==
  oracle radius_px；
- 布局消费：SVG 图符画在 solve_diagram_symbol_layout 的 position 上
  （未位移 position==anchor；位移图符带引线，端点 = position↔anchor）；
- 零值会计：零角扇区/零长条不绘制，计数在 data-* 属性（不静默丢类）；
- 诚实降级：字段缺失/非数值/block 政策/词汇表外 kind → 词表内披露码 +
  无图符组 + 无异常逃逸，既有 paint 路径不受影响；
- 确定性：同 spec 两次渲染逐字节一致；DPI 纪律与 circle 半径同式
  （glyph-local px × dpi_scale）。

精度纪律：oracle 已按 GEOMETRY_DECIMALS=3 量化；本测试按孪生统一
``_fmt_num``（2 位最简形）复核几何属性，oracle 数值（data-radius-px 等）
保真 3 位 —— 不做二次 round。
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.diagram_symbols import (  # noqa: E402
    build_glyph_geometry,
    compute_glyph_scale,
    glyph_bound_radius,
    normalize_size_policy,
    qualify_features,
    solve_diagram_symbol_layout,
)
from app.services.mapspec_svg_payloads import _fmt_num  # noqa: E402
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed  # noqa: E402

_FIXTURE_CASES = {
    case["name"]: case
    for case in json.loads(
        (Path(__file__).parent / "golden_corpus" / "diagram_symbols"
         / "oracle_fixtures.json").read_text(encoding="utf-8")
    )["cases"]
}

#: 显式 frame extent（compile 的 bounds 覆写）→ 投影闭包确定，测试可复算。
_VIEW_BOUNDS = [103.9, 29.9, 104.5, 30.5]

#: Set1 权威色板前 3 槽（palettes.COLOR_PALETTES["Set1"]，类别序着色）。
_SET1_SLOTS = ["#e41a1c", "#377eb8", "#4daf4a"]


# ── spec 装配 ──────────────────────────────────────────────────────────


def _point(fid: str, lng: float, lat: float, props: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": props,
    }


def _square(
    fid: str, lng0: float, lat0: float, lng1: float, lat1: float,
    props: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "Polygon", "coordinates": [[
            [lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0],
        ]]},
        "properties": props,
    }


def _diagram_block(kind: str, categories: List[str], fields: List[str], **extra: Any) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "schema_version": "diagram_symbol.v1",
        "kind": kind,
        "categories": categories,
        "fields": fields,
        "missing_policy": "omit",
    }
    block.update(extra)
    return block


def _compile(features: List[Dict[str, Any]], block: Dict[str, Any], **kw: Any):
    spec = {
        "version": "2.2",
        "view": {"center": [104.0, 30.0], "zoom": 10},
        "sources": {"S": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": features}}},
        "layers": [{
            "id": "diag", "source": "S", "type": "circle",
            "paint": {}, "diagram_symbol": block,
        }],
    }
    return compile_mapspec_to_svg_detailed(
        spec, target_dpi=kw.pop("target_dpi", 72), bounds=_VIEW_BOUNDS, **kw)


# ── 投影复算（dpi=72 → dpi_scale=1；默认画布 1200×800 / 边距 40）────────


def _project(lon: float, lat: float, dpi_scale: float = 1.0) -> Tuple[float, float]:
    """与编译器同一投影闭包（lon 线性 × merc-y 非线性，G34 缺席路径）。

    编译器 ``project()`` 返回 ``_fmt_num`` 字符串（2 位小数最简形）→ 图符
    中心以该**量化值**参与几何计算（消费端精度口径）；测试按同一量化复算，
    避免 .xx5 边界上的二进制表示差假阳性。注意先缩放后量化 ≠ 先量化后缩放
    —— DPI 用例必须按实际 dpi_scale 投影，不得缩放 72dpi 结果。
    """
    min_x, min_y, max_x, max_y = _VIEW_BOUNDS
    range_x = ((max_x - min_x) if max_x > min_x else 1.0)

    def _merc(v: float) -> float:
        return math.log(math.tan(math.pi / 4.0 + (v * math.pi) / 360.0))

    merc_min_y, merc_max_y = _merc(min_y), _merc(max_y)
    range_merc = (merc_max_y - merc_min_y) or 1.0
    w_span = (1200.0 - 80.0) * dpi_scale
    h_span = (800.0 - 80.0) * dpi_scale
    px = 40.0 * dpi_scale + ((lon - min_x) / range_x) * w_span
    py = 800.0 * dpi_scale - 40.0 * dpi_scale - \
        ((_merc(lat) - merc_min_y) / range_merc) * h_span
    return float(_fmt_num(px)), float(_fmt_num(py))


# ── oracle 链路独立复算（与渲染分支同源同序；golden 对锁的事实面）──────


def _oracle_chain(features, block):
    """资格 → 公共尺度 → 逐要素几何 → 布局计划（渲染分支同一管线）。"""
    kind = block["kind"]
    size_policy = normalize_size_policy(
        kind, dict(block.get("size_policy") or {}))
    qualified = qualify_features(
        features, kind=kind, categories=block["categories"], fields=block["fields"],
        missing_policy=block.get("missing_policy") or "omit",
        units=(None if block.get("unit") is None
               else {f: block["unit"] for f in block["fields"]}),
    )
    scale = compute_glyph_scale(kind, qualified["values"], size_policy)
    geometries = [
        build_glyph_geometry(kind, row, scale, size_policy,
                             layout=block.get("layout") or "stacked")
        for row in qualified["values"]
    ]
    radii = [glyph_bound_radius(g) for g in geometries]
    plan = solve_diagram_symbol_layout(
        features, qualified, radii, layout_policy={"max_glyphs": 1000})
    geo_by_fid = dict(zip(qualified["glyph_feature_ids"], geometries))
    return plan, geo_by_fid


# ── SVG 解析 ───────────────────────────────────────────────────────────


def _attrs_of(element_str: str) -> Dict[str, str]:
    return dict(re.findall(r'([\w:-]+)="([^"]*)"', element_str))


def _layer_group_attrs(svg: str) -> Dict[str, str]:
    m = re.search(r'<g\b[^>]*data-diagram-symbol-layer="1"[^>]*>', svg)
    return _attrs_of(m.group(0)) if m else {}


def _glyph_groups(svg: str) -> List[Tuple[Dict[str, str], str]]:
    """图符组（开标签属性, 组体）；顺序 = 布局计划输出序。"""
    out: List[Tuple[Dict[str, str], str]] = []
    for m in re.finditer(r'(<g\b[^>]*data-diagram-symbol="1"[^>]*>)(.*?)</g>', svg, re.S):
        out.append((_attrs_of(m.group(1)), m.group(2)))
    return out


def _elements(body: str, tag: str) -> List[Dict[str, str]]:
    return [_attrs_of(m.group(0)) for m in re.finditer(rf'<{tag}\b[^>]*/>', body)]


# ── 期望几何（合同公式独立实现；不 import 渲染端 helper，防同构盲区）──


def _pt(cx: float, cy: float, r: float, ang_deg: float) -> Tuple[float, float]:
    """0°=12 点钟顺时针 → SVG y-down 平面点（x=cx+r·sin, y=cy−r·cos）。"""
    rad = math.radians(ang_deg)
    return cx + r * math.sin(rad), cy - r * math.cos(rad)


def _expected_slice_d(cx, cy, r, start_deg, end_deg) -> str:
    span = end_deg - start_deg
    sx, sy = _pt(cx, cy, r, start_deg)
    ex, ey = _pt(cx, cy, r, end_deg)
    if span >= 359.999:
        mx, my = _pt(cx, cy, r, start_deg + 180.0)
        rr = _fmt_num(r)
        return (f"M {_fmt_num(cx)},{_fmt_num(cy)} L {_fmt_num(sx)},{_fmt_num(sy)} "
                f"A {rr} {rr} 0 1 1 {_fmt_num(mx)},{_fmt_num(my)} "
                f"A {rr} {rr} 0 1 1 {_fmt_num(sx)},{_fmt_num(sy)} Z")
    large = 1 if span > 180.0 else 0
    return (f"M {_fmt_num(cx)},{_fmt_num(cy)} L {_fmt_num(sx)},{_fmt_num(sy)} "
            f"A {_fmt_num(r)} {_fmt_num(r)} 0 {large} 1 {_fmt_num(ex)},{_fmt_num(ey)} Z")


def _expected_donut_d(cx, cy, r_out, r_in, start_deg, end_deg) -> str:
    span = end_deg - start_deg
    osx, osy = _pt(cx, cy, r_out, start_deg)
    oex, oey = _pt(cx, cy, r_out, end_deg)
    iex, iey = _pt(cx, cy, r_in, end_deg)
    isx, isy = _pt(cx, cy, r_in, start_deg)
    large = 1 if span > 180.0 else 0
    if span >= 359.999:
        omx, omy = _pt(cx, cy, r_out, start_deg + 180.0)
        imx, imy = _pt(cx, cy, r_in, start_deg + 180.0)
        ro, ri = _fmt_num(r_out), _fmt_num(r_in)
        return (f"M {_fmt_num(osx)},{_fmt_num(osy)} "
                f"A {ro} {ro} 0 1 1 {_fmt_num(omx)},{_fmt_num(omy)} "
                f"A {ro} {ro} 0 1 1 {_fmt_num(osx)},{_fmt_num(osy)} "
                f"L {_fmt_num(isx)},{_fmt_num(isy)} "
                f"A {ri} {ri} 0 1 0 {_fmt_num(imx)},{_fmt_num(imy)} "
                f"A {ri} {ri} 0 1 0 {_fmt_num(isx)},{_fmt_num(isy)} Z")
    return (f"M {_fmt_num(osx)},{_fmt_num(osy)} "
            f"A {_fmt_num(r_out)} {_fmt_num(r_out)} 0 {large} 1 {_fmt_num(oex)},{_fmt_num(oey)} "
            f"L {_fmt_num(iex)},{_fmt_num(iey)} "
            f"A {_fmt_num(r_in)} {_fmt_num(r_in)} 0 {large} 0 {_fmt_num(isx)},{_fmt_num(isy)} Z")


# ══ golden parity：SVG 数值 == oracle fixtures ════════════════════════


class TestGoldenParityAgainstFixtures:
    def test_composition_pie_slice_arcs_match_fixture(self):
        case = _FIXTURE_CASES["composition_pie_basic"]
        block = _diagram_block(
            "composition_pie", ["小学", "中学", "其他"], ["v0", "v1", "v2"],
            unit="所", size_policy={"r_max_px": 32.0},
        )
        features = [
            _point("g0", 104.0, 30.0, {"v0": 30.0, "v1": 20.0, "v2": 10.0}),
            _point("g1", 104.2, 30.2, {"v0": 5.0, "v1": 5.0, "v2": 5.0}),
        ]
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)

        # 黄金对锁：oracle 链路输出与 fixture 逐字段相等（dict 全等）
        assert geo_by_fid["g0"] == case["glyphs"][0]
        assert geo_by_fid["g1"] == case["glyphs"][1]
        assert [case["glyphs"][0]["radius_px"], case["glyphs"][1]["radius_px"]] == \
            [case["scale"]["r_max_px"]] * 2

        layer = _layer_group_attrs(res.svg)
        assert layer["data-kind"] == "composition_pie"
        assert layer["data-placed"] == "2"
        assert layer["data-omitted-collision"] == "0"
        assert layer["data-blocked"] == "0"

        groups = _glyph_groups(res.svg)
        assert [a["data-feature-id"] for a, _ in groups] == ["g0", "g1"]
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        for attrs, body in groups:
            fid = attrs["data-feature-id"]
            fixture_glyph = case["glyphs"][int(fid[1:])]
            cx, cy = _project(*plan_by_fid[fid]["position"])
            # 中心 = 布局 position 投影；组内首个弧命令的 M 点即圆心
            assert attrs["data-radius-px"] == _fmt_num(fixture_glyph["radius_px"])
            paths = _elements(body, "path")
            assert len(paths) == len(fixture_glyph["slices"])
            for path, sl in zip(paths, fixture_glyph["slices"]):
                assert path["d"] == _expected_slice_d(
                    cx, cy, fixture_glyph["radius_px"],
                    sl["start_angle_deg"], sl["end_angle_deg"])
                assert path["data-category"] == sl["category"]
                assert float(path["data-value"]) == sl["value"]
        # 颜色槽位 = 类别序（Set1 权威色板，slot 0..2）
        g0_paths = _elements(groups[0][1], "path")
        assert [p["fill"] for p in g0_paths] == _SET1_SLOTS

    def test_composition_donut_area_law_matches_fixture(self):
        case = _FIXTURE_CASES["composition_donut_area"]
        block = _diagram_block(
            "composition_donut", ["a", "b"], ["v0", "v1"],
            size_policy={"r_max_px": 40.0, "r_min_px": 4.0,
                         "inner_radius_ratio": 0.55, "total_area_encoding": True},
        )
        features = [
            _point("g0", 104.0, 30.0, {"v0": 70.0, "v1": 30.0}),
            _point("g1", 104.2, 30.2, {"v0": 15.0, "v1": 10.0}),
        ]
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)
        assert geo_by_fid["g0"] == case["glyphs"][0]
        assert geo_by_fid["g1"] == case["glyphs"][1]

        groups = _glyph_groups(res.svg)
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        for attrs, body in groups:
            fid = attrs["data-feature-id"]
            fixture_glyph = case["glyphs"][int(fid[1:])]
            # 总量面积编码：radius ∝ √total（g0: 40；g1: 40·√0.25=20，未触底）
            assert float(attrs["data-radius-px"]) == fixture_glyph["radius_px"]
            cx, cy = _project(*plan_by_fid[fid]["position"])
            paths = _elements(body, "path")
            assert len(paths) == len(fixture_glyph["slices"])
            for path, sl in zip(paths, fixture_glyph["slices"]):
                assert path["d"] == _expected_donut_d(
                    cx, cy, fixture_glyph["radius_px"],
                    fixture_glyph["inner_radius_px"],
                    sl["start_angle_deg"], sl["end_angle_deg"])
                assert path["data-category"] == sl["category"]

    def test_series_bars_stacked_rects_match_fixture(self):
        case = _FIXTURE_CASES["series_bars_stacked_neg"]
        block = _diagram_block(
            "series_bars", ["公立", "民办", "外资"], ["v0", "v1", "v2"],
            layout="stacked",
            size_policy={"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 2.0},
        )
        features = [
            _point("g0", 104.0, 30.0, {"v0": 10.0, "v1": -20.0, "v2": 5.0}),
            _point("g1", 104.2, 30.2, {"v0": 0.0, "v1": 12.0, "v2": -3.5}),
        ]
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)
        assert geo_by_fid["g0"] == case["glyphs"][0]
        assert geo_by_fid["g1"] == case["glyphs"][1]

        groups = _glyph_groups(res.svg)
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        assert len(groups) == 2
        for attrs, body in groups:
            fid = attrs["data-feature-id"]
            fixture_glyph = case["glyphs"][int(fid[1:])]
            assert attrs["data-zero-values"] == str(fixture_glyph["zero_values"])
            cx, cy = _project(*plan_by_fid[fid]["position"])
            rects = _elements(body, "rect")
            drawn = [s for s in fixture_glyph["segments"] if s["y1"] - s["y0"] > 0]
            assert len(rects) == len(drawn)
            for rect, seg in zip(rects, drawn):
                # glyph-local y 向上 → SVG y 向下：y = cy − y1（基线翻折）
                assert rect["x"] == _fmt_num(cx + seg["x0"])
                assert rect["y"] == _fmt_num(cy - seg["y1"])
                assert rect["width"] == _fmt_num(seg["x1"] - seg["x0"])
                assert rect["height"] == _fmt_num(seg["y1"] - seg["y0"])
                assert float(rect["data-value"]) == seg["value"]
                assert rect["data-category"] == seg["category"]
        # g1：公立 0.0 → 零长不绘制（会计=1），只画 民办/外资 两段
        g1_rects = _elements(groups[1][1], "rect")
        assert [r["data-category"] for r in g1_rects] == ["民办", "外资"]
        assert groups[1][0]["data-zero-values"] == "1"

    def test_series_bars_grouped_rects_match_fixture(self):
        case = _FIXTURE_CASES["series_bars_grouped"]
        block = _diagram_block(
            "series_bars", ["公立", "民办", "外资"], ["v0", "v1", "v2"],
            layout="grouped",
            size_policy={"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 2.0},
        )
        features = [
            _point("g0", 104.0, 30.0, {"v0": 10.0, "v1": -20.0, "v2": 5.0}),
            _point("g1", 104.2, 30.2, {"v0": 0.0, "v1": 12.0, "v2": -3.5}),
        ]
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)
        assert geo_by_fid["g0"] == case["glyphs"][0]
        assert geo_by_fid["g1"] == case["glyphs"][1]

        groups = _glyph_groups(res.svg)
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        for attrs, body in groups:
            fid = attrs["data-feature-id"]
            fixture_glyph = case["glyphs"][int(fid[1:])]
            cx, cy = _project(*plan_by_fid[fid]["position"])
            rects = _elements(body, "rect")
            drawn = [s for s in fixture_glyph["segments"] if s["y1"] - s["y0"] > 0]
            assert len(rects) == len(drawn)
            for rect, seg in zip(rects, drawn):
                # grouped：段沿类别轴并排（x0/x1 逐段不同），每段自基线起
                assert rect["x"] == _fmt_num(cx + seg["x0"])
                assert rect["y"] == _fmt_num(cy - seg["y1"])
                assert rect["width"] == _fmt_num(seg["x1"] - seg["x0"])
                assert rect["height"] == _fmt_num(seg["y1"] - seg["y0"])

    def test_segmented_proportional_matches_fixture(self):
        case = _FIXTURE_CASES["segmented_proportional"]
        block = _diagram_block(
            "segmented_proportional", ["a", "b"], ["v0", "v1"],
            size_policy={"r_max_px": 30.0},
        )
        features = [
            _point("g0", 104.0, 30.0, {"v0": 10.0, "v1": 40.0}),
            _point("g1", 104.2, 30.2, {"v0": 5.0, "v1": 25.0}),
        ]
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)

        # 资格层行序 = 类别序（fixture 第二要素行序 [b,a] 是 oracle 直调的
        # 重排自由度，qualification 输出恒为类别序）→ golden parity 按
        # (category → radius/area) 对账（半径/面积 ∝ 值，与槽位序无关）；
        # 槽位角绑定类别索引（g0 行序与 fixture 一致 → 角度逐项相等）
        for fid, g_index in (("g0", 0), ("g1", 1)):
            fixture_seg = {
                s["category"]: s for s in case["glyphs"][g_index]["segments"]}
            oracle_seg = {
                s["category"]: s for s in geo_by_fid[fid]["segments"]}
            assert set(oracle_seg) == set(fixture_seg)
            for cat, seg in oracle_seg.items():
                assert seg["radius_px"] == fixture_seg[cat]["radius_px"]
                assert seg["area_px2"] == fixture_seg[cat]["area_px2"]
                if g_index == 0:
                    assert seg["start_angle_deg"] == fixture_seg[cat]["start_angle_deg"]
                    assert seg["end_angle_deg"] == fixture_seg[cat]["end_angle_deg"]
        # 槽位宽 = 360/n（等角语义，与方向统计无关）
        assert geo_by_fid["g0"]["slot_deg"] == case["glyphs"][0]["slot_deg"]

        groups = _glyph_groups(res.svg)
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        for attrs, body in groups:
            fid = attrs["data-feature-id"]
            fixture_glyph = case["glyphs"][int(fid[1:])]
            cx, cy = _project(*plan_by_fid[fid]["position"])
            paths = _elements(body, "path")
            assert len(paths) == len(geo_by_fid[fid]["segments"])
            for path, seg in zip(paths, geo_by_fid[fid]["segments"]):
                # 扇形半径 = 段 radius_px（非外半径）；槽位角 = 类别序
                fixture_seg = next(
                    s for s in fixture_glyph["segments"]
                    if s["category"] == seg["category"])
                assert float(path["data-value"]) == fixture_seg["value"]
                assert path["d"] == _expected_slice_d(
                    cx, cy, seg["radius_px"],
                    seg["start_angle_deg"], seg["end_angle_deg"])
                assert path["data-category"] == seg["category"]


# ══ 布局消费 / 引线 / 会计 ════════════════════════════════════════════


class TestLayoutConsumption:
    def test_mixed_point_polygon_anchors_at_layout_positions(self):
        # 2 点 + 1 面混合源：面走 representative_point 锚（layout plan 事实）；
        # 图符画在布局 position 上（未位移 → position == anchor）
        features = [
            _point("p0", 104.0, 30.0, {"v0": 30.0, "v1": 20.0}),
            _point("p1", 104.3, 30.3, {"v0": 5.0, "v1": 5.0}),
            _square("poly", 105.0, 30.0, 105.2, 30.2, {"v0": 7.0, "v1": 9.0}),
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)

        layer = _layer_group_attrs(res.svg)
        assert layer["data-placed"] == "3"
        assert layer["data-blocked"] == "0"
        assert layer["data-omitted-collision"] == "0"

        groups = {a["data-feature-id"]: (a, b) for a, b in _glyph_groups(res.svg)}
        plan_by_fid = {g["feature_id"]: g for g in plan["glyphs"]}
        assert set(groups) == {"p0", "p1", "poly"}
        for fid, (attrs, body) in groups.items():
            planned = plan_by_fid[fid]
            # 未位移 → 无引线标记（属性缺席）
            assert "data-leader-line" not in attrs
            cx, cy = _project(*planned["position"])
            ax, ay = _project(*planned["anchor"])
            if planned["anchor_source"] == "polygon_representative_point":
                assert fid == "poly"
            assert (cx, cy) == (ax, ay)  # 未位移：画在锚上
            first = _elements(body, "path")[0]
            assert first["d"].startswith(
                f"M {_fmt_num(cx)},{_fmt_num(cy)} L ")

    def test_displaced_glyph_draws_leader_line_from_position_to_anchor(self):
        # 两要素几乎重合 → 确定性螺旋位移；位移超阈值 → 引线（layout
        # plan 的 leader_line 事实逐字消费 —— 读者必须知道符号被挪过）
        features = [
            _point("a", 104.0, 30.0, {"v0": 30.0, "v1": 20.0}),
            _point("b", 104.0001, 30.0, {"v0": 30.0, "v1": 20.0}),
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 32.0},
        )
        res = _compile(features, block)
        plan, _ = _oracle_chain(features, block)

        displaced = [g for g in plan["glyphs"] if g["leader_line"]]
        assert len(displaced) == 1
        fid = displaced[0]["feature_id"]

        groups = {a["data-feature-id"]: (a, b) for a, b in _glyph_groups(res.svg)}
        attrs, body = groups[fid]
        assert attrs["data-leader-line"] == "true"
        lines = _elements(body, "line")
        assert len(lines) == 1
        pos_x, pos_y = _project(*displaced[0]["position"])
        anc_x, anc_y = _project(*displaced[0]["anchor"])
        # 引线端点 == 布局 position ↔ anchor 的投影（逐位）
        assert lines[0]["x1"] == _fmt_num(pos_x)
        assert lines[0]["y1"] == _fmt_num(pos_y)
        assert lines[0]["x2"] == _fmt_num(anc_x)
        assert lines[0]["y2"] == _fmt_num(anc_y)

    def test_collision_omission_accounted_not_drawn(self):
        # 同锚点密集图符（130 个 @同点，r=120）：8 方位螺旋位移候选有界
        # （12 步 × 8 方位 + 中心 ≈ ≤97 席），超出席位者确定性隐藏并计数
        # —— 被避让图符不绘制、值绝不并入他符，会计进层组 data-* 属性
        features = [
            _point(f"f{i:03d}", 104.0, 30.0, {"v0": float(i + 1), "v1": 1.0})
            for i in range(130)
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 120.0},
        )
        res = _compile(features, block)
        plan, _ = _oracle_chain(features, block)
        evidence = plan["evidence"]
        assert evidence["candidates"] == 130
        assert evidence["omitted_collision"] > 0  # 席位有界 → 必有隐藏

        layer = _layer_group_attrs(res.svg)
        assert layer["data-placed"] == str(evidence["placed"])
        assert layer["data-omitted-collision"] == \
            str(evidence["omitted_collision"])
        assert layer["data-omitted-budget"] == str(evidence["omitted_budget"])
        assert int(layer["data-placed"]) + int(layer["data-omitted-collision"]) == 130
        drawn_ids = {a["data-feature-id"] for a, _ in _glyph_groups(res.svg)}
        assert drawn_ids == {g["feature_id"] for g in plan["glyphs"]}


class TestZeroValueAccounting:
    def test_zero_slice_skipped_with_accounting(self):
        # 零值 slice：零角不绘制（start==end），其余 slice 角度不受影响；
        # 会计 data-zero-slices 与 oracle zero_slices 一致
        features = [_point("z", 104.0, 30.0, {"v0": 0.0, "v1": 10.0, "v2": 5.0})]
        block = _diagram_block(
            "composition_pie", ["c0", "c1", "c2"], ["v0", "v1", "v2"],
            size_policy={"r_max_px": 32.0},
        )
        res = _compile(features, block)
        plan, geo_by_fid = _oracle_chain(features, block)
        assert geo_by_fid["z"]["zero_slices"] == 1

        groups = _glyph_groups(res.svg)
        assert len(groups) == 1
        attrs, body = groups[0]
        assert attrs["data-zero-slices"] == "1"
        paths = _elements(body, "path")
        assert len(paths) == 2
        assert [p["data-category"] for p in paths] == ["c1", "c2"]
        # 首个绘制 slice 从 0° 起（零值 slice 不占弧段）
        cx, cy = _project(*plan["glyphs"][0]["position"])
        assert paths[0]["d"] == _expected_slice_d(cx, cy, 32.0, 0.0, 240.0)

    def test_full_circle_slice_uses_two_arc_path(self):
        # 单类 100%（其余类零值）：span=360 弧指令退化 → 两个半圆弧，
        # 仍是 path 图元（与扇形同构，渲染器逐位确定）
        features = [_point("z", 104.0, 30.0, {"v0": 0.0, "v1": 10.0})]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        groups = _glyph_groups(res.svg)
        assert len(groups) == 1
        paths = _elements(groups[0][1], "path")
        assert len(paths) == 1
        plan, _ = _oracle_chain(features, block)
        cx, cy = _project(*plan["glyphs"][0]["position"])
        assert paths[0]["d"] == _expected_slice_d(cx, cy, 24.0, 0.0, 360.0)
        assert paths[0]["d"].count("A ") == 2


# ══ 诚实降级（typed 披露；无图符组；无异常逃逸）═══════════════════════


class TestHonestDegradation:
    def _diag_of(self, res, code):
        return [d for d in res.diagnostics if d["code"] == code]

    def test_missing_fields_disclosed_no_glyphs_no_raise(self):
        features = [
            _point("a", 104.0, 30.0, {"unrelated": 1.0}),
            _square("poly", 104.5, 30.0, 104.7, 30.2, {"unrelated": 2.0}),
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)  # 不抛
        assert _layer_group_attrs(res.svg) == {}
        assert _glyph_groups(res.svg) == []
        findings = self._diag_of(res, "chart_ref_unavailable")
        assert len(findings) == 1
        assert findings[0]["detail"].startswith("diagram_symbol:FIELD_NOT_FOUND:")
        assert findings[0]["layer_id"] == "diag"
        # 诚实降级 ≠ 整层消失：既有 paint 路径照常（点要素画基圆）
        assert "<circle" in res.svg

    def test_block_policy_disclosed_not_silently_omitted(self):
        features = [
            _point("a", 104.0, 30.0, {"v0": None, "v1": 20.0}),
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            missing_policy="block", size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        assert _glyph_groups(res.svg) == []
        findings = self._diag_of(res, "chart_ref_unavailable")
        assert len(findings) == 1
        assert findings[0]["detail"].startswith("diagram_symbol:MISSING_VALUE_BLOCKED:")

    def test_non_numeric_value_disclosed(self):
        features = [
            _point("a", 104.0, 30.0, {"v0": "abc", "v1": 20.0}),
        ]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        assert _glyph_groups(res.svg) == []
        findings = self._diag_of(res, "chart_ref_unavailable")
        assert findings[0]["detail"].startswith("diagram_symbol:FIELD_NOT_NUMERIC:")

    def test_unknown_kind_disclosed_and_layer_falls_through(self):
        # 词汇表外 kind：typed 披露（非静默）；层走既有路径
        features = [_point("a", 104.0, 30.0, {"v0": 1.0, "v1": 2.0})]
        block = _diagram_block("wind_rose", ["c0", "c1"], ["v0", "v1"])
        res = _compile(features, block)
        assert _glyph_groups(res.svg) == []
        assert 'data-diagram-symbol-layer' not in res.svg
        findings = self._diag_of(res, "chart_ref_unavailable")
        assert findings[0]["detail"].startswith("diagram_symbol:UNKNOWN_KIND:")

    def test_zero_total_features_disclosed(self):
        # 组成分母 Σ=0 → typed ZERO_TOTAL（组成语义不可妥协）
        features = [_point("a", 104.0, 30.0, {"v0": 0.0, "v1": 0.0})]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        assert _glyph_groups(res.svg) == []
        findings = self._diag_of(res, "chart_ref_unavailable")
        assert findings[0]["detail"].startswith("diagram_symbol:ZERO_TOTAL:")


# ══ 确定性 / DPI 纪律 / 转义 ══════════════════════════════════════════


class TestDeterminismAndSafety:
    def test_render_twice_byte_identical(self):
        features = [
            _point("p0", 104.0, 30.0, {"v0": 30.0, "v1": 20.0}),
            _point("p1", 104.2, 30.2, {"v0": 5.0, "v1": 5.0}),
            _square("poly", 105.0, 30.0, 105.2, 30.2, {"v0": 7.0, "v1": 9.0}),
        ]
        block = _diagram_block(
            "composition_donut", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0, "total_area_encoding": True,
                         "r_min_px": 4.0, "inner_radius_ratio": 0.55},
        )
        first = _compile(features, block).svg
        second = _compile(features, block).svg
        assert first == second

    def test_dpi_scale_multiplies_glyph_radius_like_circle_radius(self):
        # DPI 纪律：glyph-local px @1x × dpi_scale（144/72=2）→ 半径 32→64；
        # oracle 事实（data-radius-px）保真不缩放
        features = [_point("p0", 104.0, 30.0, {"v0": 30.0, "v1": 20.0})]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 32.0},
        )
        res = _compile(features, block, target_dpi=144)
        attrs, body = _glyph_groups(res.svg)[0]
        assert attrs["data-radius-px"] == "32"
        path = _elements(body, "path")[0]
        assert f"A {2 * 32} {2 * 32} 0" in path["d"]
        cx, cy = _project(104.0, 30.0, dpi_scale=2.0)
        assert path["d"].startswith(f"M {_fmt_num(cx)},{_fmt_num(cy)} L ")

    def test_category_text_escaped_and_svg_well_formed(self):
        # 类别文本（作者输入）进 data-category 前必须转义；整幅 SVG 可解析
        features = [_point("p0", 104.0, 30.0, {"v0": 1.0, "v1": 2.0})]
        block = _diagram_block(
            "composition_pie", ['x"y', "c&1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        res = _compile(features, block)
        root = ET.fromstring(res.svg)
        ns = "{http://www.w3.org/2000/svg}"
        groups = root.findall(f".//{ns}g[@data-diagram-symbol]")
        assert len(groups) == 1
        cats = [p.get("data-category") for p in groups[0].findall(f"{ns}path")]
        assert cats == ['x"y', "c&1"]

    def test_diagram_layer_accounting_survives_other_layers(self):
        # 多层 spec：无块层零开销、行为不变；有块层会计独立（互不污染）
        features = [_point("p0", 104.0, 30.0, {"v0": 1.0, "v1": 2.0})]
        block = _diagram_block(
            "composition_pie", ["c0", "c1"], ["v0", "v1"],
            size_policy={"r_max_px": 24.0},
        )
        spec = {
            "version": "2.2",
            "view": {"center": [104.0, 30.0], "zoom": 10},
            "sources": {"S": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": features}}},
            "layers": [
                {"id": "plain", "source": "S", "type": "circle",
                 "paint": {"circle-color": "#111111"}},
                {"id": "diag", "source": "S", "type": "circle",
                 "paint": {}, "diagram_symbol": block},
            ],
        }
        res = compile_mapspec_to_svg_detailed(
            spec, target_dpi=72, bounds=_VIEW_BOUNDS)
        assert res.diagnostics == []
        assert len(_glyph_groups(res.svg)) == 1
        # 图符组在 base paint 之后（置顶 —— live custom layer 同序）
        assert res.svg.rfind("<circle") < res.svg.rfind('data-diagram-symbol="1"')
