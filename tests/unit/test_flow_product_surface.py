from app.services.chat.pi_native_surface import compute_turn_active_tools
from app.services.chat.tool_surface_v3 import SurfaceSelection
from app.tools.flow_tools import register_flow_tools
from app.tools.registry import ToolRegistry


def test_od_tool_stays_available_when_retrieval_abstains(monkeypatch):
    registry = ToolRegistry()
    register_flow_tools(registry)
    monkeypatch.setattr("app.agent_pi_bridge.get_tool_registry", lambda: registry)
    monkeypatch.setattr(
        "app.services.chat.tool_surface_v3.DynamicToolSurface.select",
        lambda _self, _ctx: SurfaceSelection(
            abstained=True, confidence=0.34, abstain_reason="low_confidence",
        ),
    )
    for message in (
        "把这份成都区县通勤表画成流图；两向分别显示",
        "点选武侯区查看流入和流出",
        "改看净流并导出",
        "恢复两向显示",
        "Draw OD trips as directed flows",
    ):
        assert "od_flow_edges" in compute_turn_active_tools(message)


def test_explicit_od_marker_does_not_wait_for_slow_retrieval(monkeypatch):
    import asyncio
    import json

    registry = ToolRegistry()
    register_flow_tools(registry)
    monkeypatch.setattr("app.agent_pi_bridge.get_tool_registry", lambda: registry)
    monkeypatch.setattr(
        "app.services.chat.tool_surface_v3.DynamicToolSurface.select",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not await semantic retrieval")),
    )
    from app.services.chat.pi_turn_context import _active_tools_block_for

    marker = asyncio.run(_active_tools_block_for("改看净流并导出", None, None))
    names = json.loads(marker.split("WEBGIS_ACTIVE_TOOLS:", 1)[1].split("]", 1)[0] + "]")
    assert "od_flow_edges" in names


def test_unrelated_message_does_not_force_od_tool(monkeypatch):
    assert "od_flow_edges" not in compute_turn_active_tools("普通问答")
