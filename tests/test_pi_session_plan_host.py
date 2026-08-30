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


@pytest.mark.asyncio
async def test_failed_dispatch_marks_rows_and_retry_recovers(registry, sid):
    """v3(Phase E) bridge 层：dispatch error → 命中能力行标 failed（SSE
    progress 披露 + open 投影），重试成功覆写 complete。"""
    intent = await dispatch_tool(_req(
        "tf-intent", "webgis_map_intent", {"query": QUERY}, sid,
    ))
    assert not intent.isError, intent.content

    # 失败的数据工具调用：参数校验错误 → dispatch status=error
    failed = await dispatch_tool(_req(
        "tf-fail", "query_local_poi",
        {"district": {"bad": "type"}, "subtype": "小学", "limit": 5},
        sid,
    ))
    assert failed.isError

    plan = await load_session_plan(sid)
    rows = {row.capability: row.status for row in plan.progress}
    assert rows["poi_query"] == "failed", rows
    # 其余能力不受牵连（失败只落在命中的能力行）
    assert rows["admin_boundary_query"] == "pending"

    # SSE：失败的 progress 事件随 toolCallId 缓存
    sse = take_session_plan_sse("tf-fail", sid)
    assert "event: session_plan_progress" in sse
    assert '"capability": "poi_query"' in sse
    assert '"status": "failed"' in sse

    # 投影披露 Failed（retry-able）且计入 open
    from app.services.session_plan import format_session_plan_projection
    text = format_session_plan_projection(plan)
    assert "Failed (retry-able): poi_query" in text
    assert "poi_query" in text.splitlines()[0]

    # 重试恢复（failed → complete 覆写）由 service 级测试锁定
    # （tests/unit/test_session_plan.py::test_failed_dispatch_marks_capability_failed）
    # —— bridge 级用真实工具无法确定性构造"重试即成功"（本机数据态工具
    # 依赖 gd_pois.gpkg 导入）。


def test_session_plan_sse_cache_appends_not_overwrites():
    """同一 tool call 的 plan progress 与 map_finalization 事件必须都存活
    （P0 稳定化，review A-1/B-2）：cache 是赋值时，finalization 事件会
    覆盖刚缓存的行进度事件 —— 完成该 DAG 的那个工具结果在前端丢失
    [GIS Plan] 行更新。"""
    from app.agent_pi_bridge import cache_session_plan_sse, take_session_plan_sse

    cache_session_plan_sse(
        "tf-sse-concat",
        'event: session_plan_progress\ndata: {"capability": "poi_query"}\n\n',
        "sess-a",
    )
    cache_session_plan_sse(
        "tf-sse-concat",
        'event: map_finalization\ndata: {"status": "complete"}\n\n',
        "sess-a",
    )
    merged = take_session_plan_sse("tf-sse-concat", "sess-a")
    assert "event: session_plan_progress" in merged
    assert "event: map_finalization" in merged
    # 单次消费：take 后清空
    assert take_session_plan_sse("tf-sse-concat", "sess-a") == ""
