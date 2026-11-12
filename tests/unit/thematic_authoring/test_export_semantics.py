"""T-B16/T-E1：静态导出同表达核验（验收 21）。

SVG（compile_mapspec_to_svg_detailed）是出版链（mixed export / publication
PDF / raster snapshot）共用的矢量求值核心——本文件在该核心上做边界 fixture
的同值同色断言：断点左右邻值/恰在断点/越界/常数/nodata/类型化类别/大小与
线宽映射。live 端语义由 paint-bridge→compileStyleMethod（vitest 孪生）+
adapter 测试族锁定；Python/TS 双端 assign_class/size_forValue 由共享
fixture 双端断言（twins.fixture.json）。
"""
from __future__ import annotations

import re

import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed, _resolve_paint_value
from app.services.thematic_authoring import commit_expression
from app.services.thematic_authoring.contract import parse_plan
from app.services.thematic_authoring.stats import assign_class
from tests.unit.thematic_authoring.helpers import (
    current_revision, make_fc, read_layer, seed_thematic_layer)

# 边界 fixture：断点 [0,10,20,50] 上的代表性取值（恰等/左右邻/越界/nodata）
_BOUNDARY_VALUES = [-5, 0, 9.999, 10, 10.001, 19.999, 20, 34.9, 50, 50.001, 88, None]


def _polygon_fills(svg: str) -> list:
    """按文档序抽取要素 path 的 fill 色（编译器按要素顺序迭代输出 <path>）。"""
    return re.findall(r'<path[^>]*fill="([^"]+)"', svg)


@pytest.mark.asyncio
async def test_svg_boundary_values_same_class_same_color(session_id, store_root):
    rows = [{"v": v if v is not None else None} for v in _BOUNDARY_VALUES]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-exp", field="v")
    plan = parse_plan({
        "layer_id": "result-exp", "mode": "graduated_manual",
        "breaks": [0, 10, 20, 50], "palette": "Blues",
        "nodata": {"color": "#cccccc", "label": "无数据"},
    })
    r = await commit_expression(
        session_id, "result-exp", plan,
        expected_revision=await current_revision(session_id),
        origin="user", actor="test")
    layer = await read_layer(session_id, "result-exp")
    spec = layer["legend_spec"]
    colors = spec["palette_colors"]

    svg = compile_mapspec_to_svg_detailed(
        {"version": "1.0",
         "sources": {"result-exp-source": {"type": "geojson", "inlineData": fc}},
         "layers": [layer], "view": {"center": [116.05, 39.0], "zoom": 9}},
        target_dpi=96, width=900, height=600, padding=30, include_chrome=True).svg
    fills = _polygon_fills(svg)
    assert fills, "SVG 未输出任何 polygon（编译失败或层被丢弃）"

    # 逐值断言：导出色 = legend_spec 语义（统一求值器 + 调色板）；
    # null → nodata 色（三端一致契约）。
    for value, fill in zip(_BOUNDARY_VALUES, fills):
        if value is None:
            assert fill.lower() == "#cccccc", f"null 未走 nodata 色：{fill}"
            continue
        expected_color = colors[assign_class(float(value), spec["breaks"])]
        assert fill.lower() == expected_color.lower(), (
            f"v={value}: SVG {fill} ≠ 语义 {expected_color}")
    # 同类同色（验收 21 字面）：0/9.999 同类；10/10.001/19.999 同类
    assert fills[1].lower() == fills[2].lower()
    assert fills[3].lower() == fills[4].lower() == fills[5].lower()
    # 不同类不同色
    assert fills[1].lower() != fills[3].lower()


@pytest.mark.asyncio
async def test_svg_categorical_typed_keys_and_other(session_id, store_root):
    rows = [{"code": 1}, {"code": "1"}, {"code": "a"}, {"code": "zzz"}]
    fc = make_fc(rows)
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.lib.cartography.thematic_spec import build_categorical_spec, spec_to_paint
    from tests.unit.thematic_authoring.helpers import register_geojson_ref

    ref = await register_geojson_ref(session_id, fc)
    legend = build_categorical_spec("code", [
        {"key": "1", "color": "#111111", "key_type": "number"},
        {"key": "a", "color": "#aaaaaa"},
    ], default={"color": "#cccccc", "label": "其他"})
    paint, _ = spec_to_paint(legend)
    await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={  # type: ignore[arg-type]
            "id": "result-catexp", "type": "fill", "source": "result-catexp-source",
            "paint": {"color": paint}, "legend_spec": legend,
        }, source_data={"ref_id": ref}),
        origin="agent", actor="test:seed")

    svg = compile_mapspec_to_svg_detailed(
        {"version": "1.0",
         "sources": {"result-catexp-source": {"type": "geojson", "inlineData": fc}},
         "layers": [await read_layer(session_id, "result-catexp")],
         "view": {"center": [116.05, 39.0], "zoom": 9}},
        target_dpi=96, width=900, height=600, padding=30, include_chrome=True).svg
    fills = _polygon_fills(svg)
    # 类型语义保持：数值 1 → #111111（类型化 case）；文本 "1" → other；
    # "a" → #aaaaaa；未列出 "zzz" → other
    assert fills[0].lower() == "#111111"
    assert fills[1].lower() == "#cccccc", "文本 '1' 不得命中数值 case（类型敏感）"
    assert fills[2].lower() == "#aaaaaa"
    assert fills[3].lower() == "#cccccc"


@pytest.mark.asyncio
async def test_svg_size_and_width_channels(session_id, store_root):
    """比例符号/线宽：SVG 端 r=√(线性面积) 与 width 线性（与 live 同函数）。"""
    from app.services.thematic_authoring.builder import size_for_value

    pts = make_fc([{"pop": v} for v in (100, 400, 1600)], geom="Point")
    await seed_thematic_layer(
        session_id, pts, layer_id="result-size", field="pop", layer_type="circle")
    plan = parse_plan({
        "layer_id": "result-size", "mode": "graduated_auto",
        "method": "equal_interval", "k": 2,
        "size_channel": {"channel": "radius", "field": "pop",
                         "min_value": 100, "max_value": 1600,
                         "min_size": 4.0, "max_size": 10.0, "scaling": "area_sqrt"},
    })
    await commit_expression(
        session_id, "result-size", plan,
        expected_revision=await current_revision(session_id), origin="user", actor="test")
    layer = await read_layer(session_id, "result-size")
    sm = layer["paint"]["radius"]

    for v, expect_scale in ((100, 4.0), (400, 5.7271), (1600, 10.0)):
        got = _resolve_paint_value(sm, {"pop": v}, fallback=None)
        import math as _m
        assert abs(got - expect_scale) < 1e-3, f"r({v})={got} ≠ {expect_scale}"
        # 与同一尺寸函数（图例样本源）一致
        assert abs(got - size_for_value(v, plan.size_channel)) < 1e-9

    # 线宽线性
    lines = make_fc([{"flow": v} for v in (1, 40, 80)], geom="LineString")
    await seed_thematic_layer(
        session_id, lines, layer_id="result-width", field="flow", layer_type="line")
    plan_w = parse_plan({
        "layer_id": "result-width", "mode": "graduated_auto",
        "method": "equal_interval", "k": 2,
        "size_channel": {"channel": "width", "field": "flow",
                         "min_value": 1, "max_value": 80,
                         "min_size": 0.5, "max_size": 6.0, "scaling": "linear"},
    })
    await commit_expression(
        session_id, "result-width", plan_w,
        expected_revision=await current_revision(session_id), origin="user", actor="test")
    layer_w = await read_layer(session_id, "result-width")
    smw = layer_w["paint"]["width"]
    for v, expect in ((1, 0.5), (40.5, 3.25), (80, 6.0), (200, 6.0)):
        got = _resolve_paint_value(smw, {"flow": v}, fallback=None)
        assert abs(got - expect) < 1e-6


@pytest.mark.asyncio
async def test_export_capability_disclosure(session_id, store_root):
    """能力表：G33 表达的静态输出面 = SVG 语义（出版 PDF/混合导出共用同一
    求值核心）；矢量 PNG 走前端 canvas（浏览器路径）。本测试锁定该口径
    ——能力表不得宣称未验证的通道。"""
    rows = [{"v": v} for v in (1, 5, 9)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-cap", field="v")
    from app.services.thematic_authoring import inspect_layer

    receipt = await inspect_layer(session_id, "result-cap")
    # inspect 回执不宣称导出能力（导出能力归 export 面）；此断言防止
    # 未来把未验证的 PNG/PDF 承诺塞进作者回执。
    assert "export_channels" not in receipt
