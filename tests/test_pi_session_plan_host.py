"""Primary seam: Pi dispatch → SessionPlan in SessionStore + session_plan_* SSE.

Scripted Chengdu-schools tool sequence. No live LLM, no Pi RPC.
"""
import uuid

import pytest

from app.agent_pi_bridge import (
    PiToolRequest,
    dispatch_tool,
    set_tool_registry,
    take_session_plan_sse,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    CANONICAL_PLAN_EVENT_NAMES,
    load_session_plan,
)
from app.tools import init_tools
from app.tools.registry import ToolRegistry


QUERY = "成都市小学分布情况"


@pytest.fixture
def registry():
    r = ToolRegistry()
    init_tools(r)
    set_tool_registry(r)
    return r


@pytest.fixture
async def sid():
    session_id = f"sess-chengdu-schools-host-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


def _req(call_id: str, name: str, arguments: dict, sid: str) -> PiToolRequest:
    return PiToolRequest(
        toolCallId=call_id, name=name, arguments=arguments, sessionId=sid,
    )


@pytest.mark.asyncio
async def test_chengdu_schools_scripted_chain(registry, sid):
    intent = await dispatch_tool(_req(
        "tc-intent", "webgis_map_intent", {"query": QUERY}, sid,
    ))
    assert not intent.isError, intent.content
    plan = await load_session_plan(sid)
    assert plan is not None
    assert plan.gis_chapter is not None
    assert plan.gis_chapter["query"] == QUERY
    assert plan.gis_chapter["recipe_id"]
    assert any(row.capability == "poi_query" for row in plan.progress)

    intent_sse = take_session_plan_sse("tc-intent", sid)
    assert "event: session_plan_updated" in intent_sse
    for name in CANONICAL_PLAN_EVENT_NAMES:
        assert f"event: {name}" not in intent_sse

    boundary = await dispatch_tool(_req(
        "tc-boundary",
        "get_local_admin_boundary",
        {"name": "成都市", "level": "city"},
        sid,
    ))
    poi = await dispatch_tool(_req(
        "tc-poi",
        "query_local_poi",
        {"district": "成都市", "subtype": "小学", "limit": 20},
        sid,
    ))
    if not boundary.isError:
        bound_sse = take_session_plan_sse("tc-boundary", sid)
        assert "event: session_plan_progress" in bound_sse or bound_sse == ""
    if not poi.isError:
        poi_sse = take_session_plan_sse("tc-poi", sid)
        assert "event: session_plan_progress" in poi_sse or poi_sse == ""

    points = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.06 + i * 0.01, 30.66]},
                "properties": {"name": f"school-{i}"},
            }
            for i in range(12)
        ],
    }
    heat = await dispatch_tool(_req(
        "tc-heat",
        "webgis_execute",
        {"toolName": "heatmap_data", "arguments": {
            "geojson": points, "render_type": "native",
        }},
        sid,
    ))
    assert not heat.isError, heat.content
    heat_sse = take_session_plan_sse("tc-heat", sid)
    assert "event: session_plan_progress" in heat_sse
    assert "event: plan_step_done" not in heat_sse

    primary_ref = await session_data_manager.store(sid, points, prefix="geojson")
    product = await dispatch_tool(_req(
        "tc-product",
        "webgis_map_product",
        {"query": QUERY, "primary_ref": primary_ref, "title": "成都市小学分布"},
        sid,
    ))
    assert not product.isError, product.content
    plan = await load_session_plan(sid)
    assert plan.gis_chapter["recipe_id"]
    product_sse = take_session_plan_sse("tc-product", sid)
    assert "event: session_plan_updated" in product_sse

    status_ok = await dispatch_tool(_req(
        "tc-status", "webgis_cartography_status", {}, sid,
    ))
    assert not status_ok.isError, status_ok.content

    status_bad = await dispatch_tool(_req(
        "tc-status-bad",
        "webgis_cartography_status",
        {"city": "成都市", "topic": "小学分布", "scope": "全市"},
        sid,
    ))
    assert status_bad.isError
    text = status_bad.content[0]["text"]
    assert "city" in text or "不接受参数" in text

    wrap = await dispatch_tool(_req(
        "tc-wrap",
        "webgis_execute",
        {"toolName": "webgis_map_intent", "arguments": {"query": QUERY}},
        sid,
    ))
    assert wrap.isError
    assert "webgis_map_intent" in wrap.content[0]["text"]


@pytest.mark.asyncio
async def test_beijing_intent_supersedes_chengdu(registry, sid):
    first = await dispatch_tool(_req(
        "tc-cd", "webgis_map_intent", {"query": QUERY}, sid,
    ))
    assert not first.isError
    take_session_plan_sse("tc-cd", sid)
    old = await load_session_plan(sid)

    second = await dispatch_tool(_req(
        "tc-bj", "webgis_map_intent", {"query": "分析北京学校"}, sid,
    ))
    assert not second.isError
    sse = take_session_plan_sse("tc-bj", sid)
    assert "event: session_plan_superseded" in sse
    assert "event: plan_ready" not in sse
    new = await load_session_plan(sid)
    assert new.envelope_id != old.envelope_id


@pytest.mark.asyncio
async def test_chatengine_intent_does_not_write_session_plan(registry, sid):
    """Fallback may lag SessionPlan: registry dispatch is not the Pi host."""
    res = await registry.dispatch(
        "webgis_map_intent", {"query": QUERY}, session_id=sid,
    )
    assert res.get("success") is True
    assert await load_session_plan(sid) is None
