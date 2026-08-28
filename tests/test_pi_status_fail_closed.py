"""Pi native surface: cartography status fails closed on analysis args."""
import pytest

from app.agent_pi_bridge import PiToolRequest, dispatch_tool, set_tool_registry
from app.services.chat.pi_native_surface import STATUS_TOOL
from app.tools.registry import ToolRegistry

INTENT_TOOL = "webgis_map_intent"


@pytest.mark.asyncio
async def test_dispatch_rejects_chengdu_status_hallucination():
    """Native surface: status + analysis keys fail closed (no silent reroute)."""
    registry = ToolRegistry()
    status_hits = []
    intent_hits = []

    def status_tool(**_kwargs):
        status_hits.append(True)
        return {"success": True, "should_not_run": True}

    def intent_tool(query, scope_hint=None, subject_hint=None, **_kwargs):
        intent_hits.append(
            {"query": query, "scope_hint": scope_hint, "subject_hint": subject_hint}
        )
        return {
            "success": True,
            "summary": f"意图:distribution_overview 范围:{scope_hint} 主体:{subject_hint}",
            "guidance": ["poi_query → query_local_poi"],
        }

    registry.register(
        STATUS_TOOL,
        "status",
        status_tool,
        parameters={"type": "object", "properties": {}},
    )
    registry.register(
        INTENT_TOOL,
        "intent",
        intent_tool,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope_hint": {"type": "string"},
                "subject_hint": {"type": "string"},
            },
            "required": ["query"],
        },
    )
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-chengdu-schools",
            name=STATUS_TOOL,
            arguments={"city": "成都市", "topic": "小学分布", "scope": "全市"},
            sessionId="sess-chengdu-schools",
        )
    )
    assert resp.isError
    assert status_hits == []
    assert intent_hits == []
    text = resp.content[0]["text"]
    assert "city" in text or "不接受参数" in text


@pytest.mark.asyncio
async def test_dispatch_empty_status_does_not_reroute():
    registry = ToolRegistry()
    status_hits = []

    def status_tool(**_kwargs):
        status_hits.append(True)
        return {"success": True, "summary": "No cartography harness verdict yet"}

    def intent_tool(**_kwargs):
        raise AssertionError("empty status must not reroute to intent")

    registry.register(
        STATUS_TOOL, "status", status_tool,
        parameters={"type": "object", "properties": {}},
    )
    registry.register(
        INTENT_TOOL, "intent", intent_tool,
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-status-empty",
            name=STATUS_TOOL,
            arguments={},
            sessionId="sess-status-empty",
        )
    )
    assert not resp.isError
    assert status_hits == [True]


@pytest.mark.asyncio
async def test_dispatch_empty_status_succeeds_on_live_registry():
    """Live registry: the empty verdict pull dispatches to the real status
    tool and succeeds — no reroute to intent, no key-guard rejection."""
    from app.tools import init_tools

    registry = ToolRegistry()
    init_tools(registry)
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-status-empty-live",
            name=STATUS_TOOL,
            arguments={},
            sessionId="sess-status-empty-live",
        )
    )
    assert not resp.isError, resp.content
    text = resp.content[0]["text"]
    # The REAL status tool ran: its not-evaluated verdict summary comes back,
    # not an intent dispatch (which would answer with 意图/analysis guidance).
    assert "No cartography harness verdict yet" in text
    assert "意图" not in text


@pytest.mark.asyncio
async def test_dispatch_chengdu_status_fails_closed_on_live_registry():
    """Full registry: status with analysis keys never succeeds."""
    from app.tools import init_tools

    registry = ToolRegistry()
    init_tools(registry)
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-chengdu-live",
            name=STATUS_TOOL,
            arguments={"city": "成都市", "topic": "小学分布", "scope": "全市"},
            sessionId="sess-chengdu-live",
        )
    )
    assert resp.isError, resp.content
    text = resp.content[0]["text"]
    assert "city" in text or "不接受参数" in text


@pytest.mark.asyncio
async def test_dispatch_status_null_valued_keys_fail_closed():
    """Key-sensitive guard: `{"city": null}` is still a hallucinated
    analysis argument, not a valid empty status call."""
    from app.tools import init_tools

    registry = ToolRegistry()
    init_tools(registry)
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-status-null-city",
            name=STATUS_TOOL,
            arguments={"city": None, "topic": ""},
            sessionId="sess-status-null-city",
        )
    )
    assert resp.isError, resp.content
    assert "city" in resp.content[0]["text"]


@pytest.mark.asyncio
async def test_dispatch_unknown_bare_name_rejects_with_discovery_guidance():
    """HTTP dispatch boundary: unknown bare names reject with the
    list_available_tools → webgis_execute guidance, not a bare Tool-not-found."""
    from app.tools import init_tools

    registry = ToolRegistry()
    init_tools(registry)
    set_tool_registry(registry)

    resp = await dispatch_tool(
        PiToolRequest(
            toolCallId="tc-bare-name",
            name="heatmap_data",
            arguments={"render_type": "native"},
            sessionId="sess-bare-name",
        )
    )
    assert resp.isError, resp.content
    text = resp.content[0]["text"]
    assert "list_available_tools" in text
    assert "webgis_execute" in text
    assert resp.details.get("error") == "native_surface_reject"
