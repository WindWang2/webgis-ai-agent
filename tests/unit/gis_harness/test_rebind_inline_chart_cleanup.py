"""R2-P1（qc-loop round 5）回归锁：rebind 换绑通道必须清掉旧 inline chart。

历史缺陷：``rebind_component`` 的互斥纪律只清对侧绑定键
（chartRef↔layerId/tableRef），chart_panel 换绑后残留的旧
``options["chart"]`` 不会被移除 —— 渲染端 inline 优先，面板继续显示
旧 inline 数据而绑定声称新来源（``mutate_component`` 的同名清理
在 review P1-3 已修，此处为漏改的姊妹路径）。
"""
from app.services.gis_harness.components import (
    chart_panel_component,
    rebind_component,
)


def _panel():
    comp = chart_panel_component(component_id="panel-1")
    comp.options["chart"] = {"type": "bar", "data": [1, 2, 3]}
    comp.options["chartRef"] = "old-chart-ref"
    return [comp]


def test_rebind_to_layer_id_drops_stale_inline_chart_and_chart_ref():
    out, changes, err = rebind_component(
        _panel(), component_id="panel-1", bindings={"layerId": "lyr-9"})
    assert err is None
    opts = out[0].options
    assert opts.get("layerId") == "lyr-9"
    assert "chart" not in opts, "stale inline chart must be removed"
    assert "chartRef" not in opts


def test_rebind_to_chart_ref_drops_stale_inline_chart():
    out, changes, err = rebind_component(
        _panel(), component_id="panel-1", bindings={"chartRef": "new-ref"})
    assert err is None
    opts = out[0].options
    assert opts.get("chartRef") == "new-ref"
    assert "chart" not in opts
    assert "layerId" not in opts


def test_change_record_discloses_rebinding():
    _, changes, err = rebind_component(
        _panel(), component_id="panel-1", bindings={"chartRef": "new-ref"})
    assert err is None
    assert changes["rebound"]["chartRef"]["to"] == "new-ref"
