"""行为化 dispatch 测试 —— 地图样式/浮动图表 2 工具。

apply_layer_style / control_floating_chart 是纯显示变换（无会话依赖）：
样式注入走 ApplyStyleArgs pydantic 契约；图表控制走 chart_kinds 状态机
词表的服务端预检。
"""
import pytest

from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_cartography_tools(reg)
    return reg


def _fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "a"}},
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.01, 39.01]},
             "properties": {}},
        ],
    }


@pytest.mark.asyncio
async def test_apply_layer_style_behavioral(registry):
    # validation：opacity 越界（>1）→ ApplyStyleArgs ge/le 校验错误
    bad = await registry.dispatch("apply_layer_style", {
        "geojson": _fc(), "color": "#ff0000", "opacity": 1.5})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"
    assert "opacity" in (bad.get("message") or "")

    # error path：非法 GeoJSON 字符串 → 工具自管 error 应答
    junk = await registry.dispatch("apply_layer_style", {
        "geojson": "definitely-not-geojson", "color": "#ff0000"})
    assert isinstance(junk, dict) and "error" in junk, junk

    # happy path：样式注入回写 properties + style_applied 摘要
    out = await registry.dispatch("apply_layer_style", {
        "geojson": _fc(), "color": "#3366cc", "opacity": 0.8,
        "stroke_width": 3.0, "group": "analysis"})
    style = out.get("style_applied") or {}
    assert style.get("color") == "#3366cc"
    assert style.get("opacity") == 0.8
    assert style.get("stroke_width") == 3.0
    assert out.get("group") == "analysis"
    features = (out.get("geojson") or {}).get("features") or []
    assert len(features) == 2
    for f in features:
        props = f.get("properties") or {}
        assert props.get("fill_color") == "#3366cc"
        assert props.get("opacity") == 0.8
        assert props.get("stroke_width") == 3.0


@pytest.mark.asyncio
async def test_control_floating_chart_behavioral(registry):
    # validation：缺 operation（必填） → 校验错误
    bad = await registry.dispatch(
        "control_floating_chart", {"component_id": "chart-1"})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：未知操作 → 服务端词表预检拒绝（可读错误含合法操作清单）
    unknown = await registry.dispatch("control_floating_chart", {
        "component_id": "chart-1", "operation": "teleport"})
    assert isinstance(unknown, dict) and "error" in unknown
    assert "未知操作" in unknown["error"]

    # error path：move 缺 x/y 与 anchor → 参数依赖错误
    move_bad = await registry.dispatch("control_floating_chart", {
        "component_id": "chart-1", "operation": "move"})
    assert isinstance(move_bad, dict) and "error" in move_bad
    assert "move" in move_bad["error"]

    # happy path：pin → chart_set_state 指令（state=floating）
    pin = await registry.dispatch("control_floating_chart", {
        "component_id": "chart-1", "operation": "pin"})
    assert pin.get("status") == "chart_command_created", pin
    assert pin.get("command") == "chart_set_state"
    assert pin.get("params") == {"componentId": "chart-1", "state": "floating"}

    # happy path：switch_chart_type 到 native 类型 → chart_switch_type 指令
    switch = await registry.dispatch("control_floating_chart", {
        "component_id": "chart-2", "operation": "switch_chart_type",
        "chart_type": "bar"})
    assert switch.get("status") == "chart_command_created", switch
    assert switch.get("command") == "chart_switch_type"
    assert switch.get("params", {}).get("chartType")

    # happy path：highlight → 类别列表被有界截断传递
    hl = await registry.dispatch("control_floating_chart", {
        "component_id": "chart-2", "operation": "highlight",
        "categories": ["北区", "南区"]})
    assert hl.get("command") == "chart_highlight"
    assert hl.get("params", {}).get("categories") == ["北区", "南区"]
