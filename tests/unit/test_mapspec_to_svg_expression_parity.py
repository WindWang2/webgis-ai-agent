"""#1726 项 2：MapSpec-to-SVG 双孪生 MapLibre 表达式求值 parity。

背景（issue #1726 项 2）：``resolvePaintValue`` / ``_resolve_paint_value``
对 MapLibre 表达式数组的处置不对称 —— TS 孪生
``frontend/lib/mapspec-compiler/mapspec-to-svg.ts:220-235`` 自 #1385 F04 起
在导出 zoom 处求值（``evaluateMapLibreExpression``），Python 孪生却以
``if not isinstance(val, dict): return val`` 原样返回表达式数组。同一份
live 合成 spec（后端 ``app/lib/cartography/palettes.py:heatmap_paint`` 的
``heatmap-radius`` 就是 interpolate 表达式）两条导出路径因此产出不同几何
（实测：TS r="26.89" vs Python r="15" —— 后者被 ``_safe_float`` 回落缺省）。

修法：Python 侧补与 TS 孪生逐分支对齐的表达式求值器
（``_evaluate_map_libre_expression`` + ``_resolve_paint_value`` 的数组分支）。

本文件是**两侧 parity 回归闸**：断言值同时是 TS 孪生
``frontend/lib/mapspec-compiler/mapspec-to-svg.test.ts``（TEST-06 与
``mapspec-to-svg-representation.test.ts``）锁定的同一组数。改任一侧的
表达式语义，必须同步更新另一侧测试 —— 与 ``test_compiler_parity.py`` /
``mapspec-to-svg.parity.test.ts`` 的双更新纪律同款。

已知边界（不在本项范围）：TS 入口支持 ``options.zoom`` 覆盖
``view.zoom``，Python 入口无对应入参（``publication_export`` /
``report_service`` 均不传 zoom）。缺省解析口径一致：
``options.zoom ?? view.zoom ?? 8`` ≡ ``export_zoom_of_mapspec(view.zoom, 8)``。
"""
import math
import re

import pytest

from app.lib.cartography.palettes import heatmap_paint
from app.services.mapspec_expr import (
    ACTIVE_EXPORT_ZOOM as _ACTIVE_EXPORT_ZOOM,
    DEFAULT_EXPORT_ZOOM as _DEFAULT_EXPORT_ZOOM,
    evaluate_map_libre_expression as _evaluate_map_libre_expression,
)
from app.services.mapspec_to_svg import (
    _resolve_paint_value,
    compile_mapspec_to_svg,
    compile_mapspec_to_svg_detailed,
)

#: TS 孪生 TEST-06 的同一表达式（``["interpolate", ["linear"], ["zoom"],
#: 0, 10, 10, 50]``）。
ZOOM_INTERP = ["interpolate", ["linear"], ["zoom"], 0, 10, 10, 50]

#: 后端 heatmap_paint 实际落笔的半径表达式（classic / radius_px=30）。
HEATMAP_RADIUS_EXPR = ["interpolate", ["linear"], ["zoom"], 0, 2, 9, 30, 13, 51]


def _heatmap_spec(radius_expr, zoom=8):
    return {
        "version": "1.5",
        "view": {"center": [116.4, 39.9], "zoom": zoom},
        "sources": {
            "S": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "heat",
                "type": "heatmap",
                "source": "S",
                "paint": {"heatmap-radius": radius_expr},
            }
        ],
    }


def _heatmap_paint_spec(paint, zoom=8):
    """整块 heatmap paint（含多个表达式键）的 spec —— 覆盖 `or` 取值链。"""
    spec = _heatmap_spec(None, zoom=zoom)
    spec["layers"][0]["paint"] = paint
    return spec


def _radii(svg):
    return re.findall(r'<circle[^>]*\br="([^"]+)"', svg)


# ── 单元：求值器语义 ≡ TS evaluateMapLibreExpression ─────────────────────


def test_interpolate_zoom_input_evaluated_at_export_zoom():
    """TS: resolvePaintValue(expr, {}, 15) → 42（缺省 zoom 8）。"""
    assert _resolve_paint_value(ZOOM_INTERP, {}, 15) == 42.0
    assert _resolve_paint_value(ZOOM_INTERP, {}, 15, 8) == 42.0
    assert _resolve_paint_value(ZOOM_INTERP, {}, 15, 10) == 50
    assert _resolve_paint_value(ZOOM_INTERP, {}, 15, 0) == 10


def test_interpolate_stops_clamp_below_and_above():
    """首/末停靠外侧取端点值（TS 同款：inputVal <= stops[0][0] / >= last）。"""
    expr = ["interpolate", ["linear"], ["zoom"], 0, 2, 9, 30, 13, 51]
    assert _resolve_paint_value(expr, {}, 15, -5) == 2
    assert _resolve_paint_value(expr, {}, 15, 0) == 2
    assert _resolve_paint_value(expr, {}, 15, 13) == 51
    assert _resolve_paint_value(expr, {}, 15, 20) == 51


def test_interpolate_color_stops_interpolate_to_hex():
    """色带停靠线性插值成 hex（TS ``interpolateValue`` 的 parseColor 分支）。"""
    expr = ["interpolate", ["linear"], ["zoom"], 0, "#000000", 10, "#ffffff"]
    assert _resolve_paint_value(expr, {}, "#808080", 5) == "#808080"


def test_step_expression_uses_base_and_thresholds():
    """TS: ``let res = val[2]`` 基值 + 逐阈值覆盖。"""
    expr = ["step", ["get", "v"], 3, 10, 8]
    assert _resolve_paint_value(expr, {"v": 5}) == 3
    assert _resolve_paint_value(expr, {"v": 10}) == 8
    assert _resolve_paint_value(expr, {"v": 15}) == 8


def test_match_expression_matches_label_then_fallback():
    """TS: ``inputVal === label || String(inputVal) === String(label)``。"""
    expr = ["match", ["get", "c"], "a", 1, "b", 2, 3]
    assert _resolve_paint_value(expr, {"c": "b"}) == 2
    assert _resolve_paint_value(expr, {"c": "z"}) == 3  # 末位兜底
    assert _resolve_paint_value(expr, {}) == 3  # get 缺席 → None → 不命中


def test_literal_expression_returns_its_value():
    assert _resolve_paint_value(["literal", ["Open Sans", "Arial"]], {}) == [
        "Open Sans",
        "Arial",
    ]


def test_unknown_operator_passes_through_fail_open():
    """词表外算子不求值、原样返回 —— 与 TS ``return val`` 分支同口径；
    调用方 ``_safe_float`` 回落缺省（绝不产 NaN）。"""
    expr = ["coalesce", ["get", "a"], 1]
    assert _resolve_paint_value(expr, {}, "#000") == expr


def test_non_finite_input_fails_open_to_fallback():
    """``["get", missing]`` → 输入非有限 → fallback（TS ``Number(undefined)``
    = NaN 门同结果）。"""
    expr = ["interpolate", ["linear"], ["get", "nope"], 0, 10, 10, 50]
    assert _resolve_paint_value(expr, {}, 15, 8) == 15


def test_style_method_objects_unchanged():
    """回归保护：StyleMethod 对象路径（constant/field/match/step/interpolate）
    不受数组求值分支影响。"""
    assert _resolve_paint_value({"method": "constant", "value": "#ff0000"}) == "#ff0000"
    assert _resolve_paint_value({"method": "field", "field": "cat"}, {"cat": "B"}) == "B"
    step = {"method": "step", "field": "v", "stops": [[10, "#f00"], [20, "#00f"]], "default": "#fff"}
    assert _resolve_paint_value(step, {"v": 5}) == "#fff"
    assert _resolve_paint_value(step, {"v": 15}) == "#f00"
    interp = {"method": "interpolate", "field": "s", "stops": [[0, 10], [100, 50]]}
    assert _resolve_paint_value(interp, {"s": 50}) == 30.0


def test_primitives_and_none_unchanged():
    assert _resolve_paint_value(5, {}) == 5
    assert _resolve_paint_value("#abc", {}) == "#abc"
    assert _resolve_paint_value(None, {}, 15) == 15


# ── 端到端：同一份 spec 双路径同几何 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("zoom", "expected"),
    [(10, "50"), (0, "10"), (5, "30"), (None, "42")],
)
def test_zoom_interpolate_radius_matches_ts_twin(zoom, expected):
    """TS TEST-06 同参数同结果（zoom=10→50 / zoom=0→10 / view5→30 /
    缺省→42）。"""
    svg = compile_mapspec_to_svg(_heatmap_spec(ZOOM_INTERP, zoom=zoom), target_dpi=72)
    assert _radii(svg) == [expected]
    assert "NaN" not in svg


@pytest.mark.parametrize(
    ("zoom", "expected"),
    [(0, "2"), (5, "17.56"), (8, "26.89"), (9, "30"), (13, "51"), (20, "51")],
)
def test_backend_heatmap_paint_radius_matches_ts_twin(zoom, expected):
    """#1726 复现用例：后端 ``heatmap_paint`` 落笔的 radius 表达式在两个
    孪生上必须逐 zoom 同值（修前 Python 恒为缺省 15）。"""
    paint = heatmap_paint("classic", 30)
    assert paint["heatmap-radius"] == HEATMAP_RADIUS_EXPR
    svg = compile_mapspec_to_svg(_heatmap_spec(paint["heatmap-radius"], zoom=zoom), target_dpi=72)
    assert _radii(svg) == [expected]


def test_heatmap_layer_data_layer_is_byte_identical_across_twins():
    """数据层整段字节比对锚点（TS 孪生产出同串，见测试文件 docstring）。"""
    paint = heatmap_paint("classic", 30)
    svg = compile_mapspec_to_svg(_heatmap_paint_spec(paint, zoom=8), target_dpi=72)
    expected = (
        '<circle cx="40" cy="760" r="26.89" fill="#ef4444" fill-opacity="0.9" />'
    )
    assert expected in svg


def test_export_zoom_contextvar_is_reset_after_compile():
    """ContextVar set/reset 配对 —— 编译结束后还原，不泄漏给后续编译。"""
    assert _ACTIVE_EXPORT_ZOOM.get() == _DEFAULT_EXPORT_ZOOM
    compile_mapspec_to_svg_detailed(_heatmap_spec(ZOOM_INTERP, zoom=13), 72)
    assert _ACTIVE_EXPORT_ZOOM.get() == _DEFAULT_EXPORT_ZOOM


def test_sequential_compiles_do_not_leak_zoom():
    """连续两次不同 view.zoom 的编译互不污染（修前靠 _safe_float 缺省，
    两类 spec 都渲成同一个错半径）。"""
    low = compile_mapspec_to_svg(_heatmap_spec(ZOOM_INTERP, zoom=0), target_dpi=72)
    high = compile_mapspec_to_svg(_heatmap_spec(ZOOM_INTERP, zoom=10), target_dpi=72)
    assert _radii(low) == ["10"]
    assert _radii(high) == ["50"]


def test_contextvar_reset_on_internal_exception():
    """异常路径同样还原（finally 配对）—— 编译失败不留下陈旧 zoom。"""
    # 触发内部异常：sources 形状非法到 extract 阶段抛错，走 except 回退分支。
    broken = {"sources": {"S": {"type": "geojson", "inlineData": {"type": "FeatureCollection"}}},
              "layers": [{"id": "x", "type": "circle", "source": "S"}]}
    broken["layers"][0]["paint"] = {"circle-radius": object()}
    compile_mapspec_to_svg_detailed(broken, 72)
    assert _ACTIVE_EXPORT_ZOOM.get() == _DEFAULT_EXPORT_ZOOM


def test_malformed_expression_arrays_do_not_crash():
    """对抗性输入：截断/空/类型错乱的表达式数组必须 fail-open，不抛异常。"""
    for bad in (
        [],
        ["interpolate"],
        ["interpolate", ["linear"]],
        ["step"],
        ["match"],
        ["literal"],
        ["interpolate", ["linear"], ["zoom"], "x", 1, 2],
        ["step", ["zoom"], "not-a-number", 10, 8],
        [None, 1, 2],
        [42],
    ):
        # 不抛异常即通过；返回值允许是原样数组（调用方 fail-open 缺省）、
        # 回落缺省，或从有效停靠点求值出的标量 —— 但绝不产出 NaN/Inf。
        resolved = _resolve_paint_value(bad, {}, "FB")
        evaluated = _evaluate_map_libre_expression(bad, {}, 8.0, "FB")
        for v in (resolved, evaluated):
            if isinstance(v, float):
                assert math.isfinite(v)


def test_no_nan_in_output_for_unresolvable_expression():
    """``["get", missing]`` 输入的 radius → 缺省值，产物无 NaN。"""
    expr = ["interpolate", ["linear"], ["get", "absent"], 0, 10, 10, 50]
    svg = compile_mapspec_to_svg(_heatmap_spec(expr, zoom=8), target_dpi=72)
    assert "NaN" not in svg
    assert _radii(svg) == ["15"]  # heatmap-radius 缺省（TS 同口径）
