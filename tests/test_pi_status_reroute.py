"""Pi first-turn: cartography_status + analysis args → webgis_map_intent."""
import pytest

from app.agent_pi_bridge import PiToolRequest, dispatch_tool, set_tool_registry
from app.services.chat.pi_tool_reroute import (
    INTENT_TOOL,
    STATUS_TOOL,
    reroute_cartography_status_misuse,
)
from app.tools.registry import ToolRegistry


def test_chengdu_schools_status_call_rewrites_to_intent():
    name, args = reroute_cartography_status_misuse(
        STATUS_TOOL,
        {"city": "成都市", "topic": "小学分布", "scope": "全市"},
    )
    assert name == INTENT_TOOL
    assert args["query"] == "成都市 小学分布"
    assert args["scope_hint"] == "成都市"
    assert args["subject_hint"] == "小学分布"


def test_empty_status_call_is_unchanged():
    name, args = reroute_cartography_status_misuse(STATUS_TOOL, {})
    assert name == STATUS_TOOL
    assert args == {}


def test_status_with_only_session_id_is_unchanged():
    name, args = reroute_cartography_status_misuse(
        STATUS_TOOL, {"session_id": "sess-1"}
    )
    assert name == STATUS_TOOL
    assert args == {"session_id": "sess-1"}


def test_other_tools_are_not_rewritten():
    original = {"district": "成都市", "subtype": "小学"}
    name, args = reroute_cartography_status_misuse("query_local_poi", original)
    assert name == "query_local_poi"
    assert args == original


def test_unknown_junk_on_status_is_not_rewritten():
    name, args = reroute_cartography_status_misuse(
        STATUS_TOOL, {"foo": "bar"}
    )
    assert name == STATUS_TOOL
    assert args == {"foo": "bar"}


@pytest.mark.asyncio
async def test_dispatch_rejects_chengdu_status_hallucination():
    """Native surface: status + analysis keys fail closed (do not reroute)."""
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
