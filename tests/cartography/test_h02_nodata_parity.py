"""H02 Wave 5b — nodata guard 双端语义一致（任务书 §B：no-data 强制核查）。

历史分歧（thematic-paint.ts:21-26 自述）：legend_spec.nodata 时 live 把
null/missing 转无数据色，headless paint（spec_to_paint）不编码 → 导出把
null 压进最低类。修复后：StyleMethod 携带 ``nodata`` 色，全部四个下降点
（TS compileStyleMethod / TS SVG 孪生 / Python SVG 孪生 / MapLibre live）
同语义 —— null/missing → 无数据色；数值 NaN 行为保持不变。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.thematic_spec import spec_to_paint


def _graduated_legend(nodata=None) -> dict:
    legend = {
        "type": "graduated",
        "field": "density",
        "breaks": [0, 10, 20],
        "palette_colors": ["#fcae91", "#fb6a4a", "#cb181d"],
    }
    if nodata is not None:
        legend["nodata"] = nodata
    return legend


def test_spec_to_paint_encodes_nodata_guard():
    style, warnings = spec_to_paint(
        _graduated_legend(nodata={"color": "#eeeeee", "label": "No data"}))
    assert style is not None and not warnings
    assert style.get("nodata") == "#eeeeee", (
        "StyleMethod 必须携带 nodata 色（导出侧据此分派无数据色）"
    )


def test_spec_to_paint_byte_compat_without_nodata():
    style, _ = spec_to_paint(_graduated_legend())
    assert style is not None
    assert "nodata" not in style, "无 nodata 规则时输出保持逐字节兼容"


def test_interpolate_encodes_nodata_guard():
    legend = {
        "type": "continuous",
        "field": "value",
        "min": 0,
        "max": 100,
        "palette_colors": ["#eff3ff", "#08519c"],
        "nodata": {"color": "#cccccc", "label": "无数据"},
    }
    style, warnings = spec_to_paint(legend)
    assert style is not None and not warnings
    assert style.get("nodata") == "#cccccc"


def test_python_svg_twin_nodata_semantics():
    """Python SVG 孪生：null/missing → 无数据色（不再是最低类）。"""
    from app.services.mapspec_to_svg import _resolve_paint_value

    style = {
        "method": "step",
        "field": "density",
        "default": "#fcae91",
        "stops": [[10, "#fb6a4a"], [20, "#cb181d"]],
        "nodata": "#eeeeee",
    }
    assert _resolve_paint_value(style, {"density": None}) == "#eeeeee"
    assert _resolve_paint_value(style, {}) == "#eeeeee"
    # 有值时行为不变
    assert _resolve_paint_value(style, {"density": 15}) == "#fb6a4a"
    # 无 nodata 声明时旧行为保持（missing → default/最低类）
    legacy = {k: v for k, v in style.items() if k != "nodata"}
    assert _resolve_paint_value(legacy, {"density": None}) == "#fcae91"
