"""Q037（qc-loop round 5）回归锁：连续色带取色必须与前端镜像同舍入口径。

历史缺陷：``_pick_continuous_color`` 用 Python ``round``（银行家舍入），
声明的前端镜像（legend-model.ts:54）是 ``Math.round``（半进位）——
``t*(n-1)`` 恰为 .5 时（如双色带的中点色、6 色带 t=0.5）后端派生图例
与前端渲染取到不同颜色。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.render_scene import _pick_continuous_color


def test_two_color_midpoint_picks_upper_to_match_math_round():
    # round(0.5)=0（银行家）vs Math.round(0.5)=1 —— 修复前取 "#a"
    assert _pick_continuous_color(["#a", "#b"], 0.5) == "#b"


def test_six_color_midpoint_picks_upper_to_match_math_round():
    # 0.5*5=2.5 -> Math.round=3（修复前银行家舍入取 2）
    colors = ["#0", "#1", "#2", "#3", "#4", "#5"]
    assert _pick_continuous_color(colors, 0.5) == "#3"


def test_non_half_values_unchanged():
    colors = ["#a", "#b"]
    assert _pick_continuous_color(colors, 0.0) == "#a"
    assert _pick_continuous_color(colors, 0.25) == "#a"
    assert _pick_continuous_color(colors, 0.75) == "#b"
    assert _pick_continuous_color(colors, 1.0) == "#b"
