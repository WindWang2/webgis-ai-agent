"""SessionPlan store + apply (no live LLM, no Pi RPC)."""
import pytest

from app.services.session_data import session_data_manager
from app.services.session_plan import (
    CANONICAL_PLAN_EVENT_NAMES,
    CURRENT_ALIAS,
    SESSION_PLAN_PROGRESS,
    SESSION_PLAN_SUPERSEDED,
    SESSION_PLAN_UPDATED,
    apply_tool_result,
    ensure_session_plan_slot,
    events_to_sse,
    format_session_plan_projection,
    goal_key,
    load_session_plan,
    public_data_refs,
)


@pytest.fixture
async def sid():
    session_id = "sess-plan-unit"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


def _gis(query: str, scope: str, subject: str = "小学", task: str = "distribution_overview"):
    return {
        "plan_id": f"plan-{scope}",
        "query": query,
        "recipe_id": "poi_distribution_overview",
        "intent": {
            "query": query,
            "task": task,
            "scope": {"name": scope, "level": "city"},
            "subject": {"type": "poi", "category": subject},
        },
        "data_requirements": [
            {"capability": "poi_query", "status": "pending", "resolved_tool": "query_local_poi"},
            {"capability": "admin_boundary", "status": "pending", "resolved_tool": "get_local_admin_boundary"},
        ],
        "analysis_steps": [
            {"capability": "heatmap", "status": "pending", "resolved_tool": "heatmap_data"},
        ],
        "status": "draft",
    }


@pytest.mark.asyncio
async def test_host_opens_empty_slot(sid):
    plan = await ensure_session_plan_slot(sid)
    again = await ensure_session_plan_slot(sid)
    assert plan.envelope_id == again.envelope_id
    assert plan.gis_chapter is None
    alias = await session_data_manager.resolve_alias(sid, CURRENT_ALIAS)
    assert alias != CURRENT_ALIAS
    assert CURRENT_ALIAS != "plan-current"


@pytest.mark.asyncio
async def test_intent_writes_gis_chapter_and_updated_sse(sid):
    await ensure_session_plan_slot(sid)
    gis = _gis("成都市小学分布情况", "成都市")
    events = await apply_tool_result(
        sid, "webgis_map_intent",
        {"success": True, "plan": gis, "intent": gis["intent"]},
        success=True,
    )
    plan = await load_session_plan(sid)
    assert plan is not None
    assert plan.gis_chapter["recipe_id"] == "poi_distribution_overview"
    assert plan.gis_chapter["query"] == "成都市小学分布情况"
    assert {row.capability for row in plan.progress} >= {"poi_query", "admin_boundary", "heatmap"}
    assert all(row.status == "pending" for row in plan.progress)
    assert [e.event for e in events] == [SESSION_PLAN_UPDATED]
    sse = events_to_sse(events, sid)
    assert "event: session_plan_updated" in sse
    for name in CANONICAL_PLAN_EVENT_NAMES:
        assert f"event: {name}" not in sse


@pytest.mark.asyncio
async def test_same_goal_replaces_and_voids_progress(sid):
    gis = _gis("成都市小学分布情况", "成都市")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": gis}, success=True)
    await apply_tool_result(
        sid, "query_local_poi", {"success": True},
        success=True, geojson_ref="ref:geojson-poi",
    )
    plan = await load_session_plan(sid)
    assert any(row.status == "complete" and row.capability == "poi_query" for row in plan.progress)

    events = await apply_tool_result(sid, "webgis_map_intent", {"plan": gis}, success=True)
    names = [e.event for e in events]
    assert SESSION_PLAN_PROGRESS in names
    assert SESSION_PLAN_UPDATED in names
    assert SESSION_PLAN_SUPERSEDED not in names
    plan = await load_session_plan(sid)
    assert plan.replaced is True
    assert all(row.status == "pending" for row in plan.progress)


@pytest.mark.asyncio
async def test_new_goal_supersedes_envelope(sid):
    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": _gis("成都市小学分布情况", "成都市")},
        success=True,
    )
    old = await load_session_plan(sid)
    events = await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": _gis("分析北京学校", "北京市", subject="学校")},
        success=True,
    )
    names = [e.event for e in events]
    assert names[0] == SESSION_PLAN_SUPERSEDED
    assert SESSION_PLAN_UPDATED in names
    new = await load_session_plan(sid)
    assert new.envelope_id != old.envelope_id
    assert new.gis_chapter["intent"]["scope"]["name"] == "北京市"
    assert new.previous_goal == old.user_goal


@pytest.mark.asyncio
async def test_tool_progress_is_capability_shaped(sid):
    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": _gis("成都市小学分布情况", "成都市")},
        success=True,
    )
    events = await apply_tool_result(
        sid, "heatmap_data", {"success": True},
        success=True, geojson_ref="ref:geojson-heat",
    )
    assert events
    assert all(e.event == SESSION_PLAN_PROGRESS for e in events)
    caps = {e.data["capability"] for e in events}
    assert "heatmap" in caps
    assert all(e.data["status"] == "complete" for e in events)
    assert all("tool" not in e.data for e in events)


def test_projection_is_bounded_not_verdict():
    text = format_session_plan_projection(None)
    assert text.startswith("[SessionPlan]")
    assert "CARTOGRAPHY" not in text
    assert "verdict" not in text.lower()


def test_public_data_refs_hide_session_plan_envelope():
    refs = {
        "ref:geojson-abc": "schools",
        "ref:sessionplan-xyz": "session-plan",
        "ref:sessionplan-old": "session-plan-id:sp-1",
    }
    assert public_data_refs(refs) == {"ref:geojson-abc": "schools"}


def test_session_plan_only_is_not_data_refs():
    from app.services.chat.execution_engine import _has_non_plan_refs
    assert _has_non_plan_refs({"ref:sessionplan-xyz": "session-plan"}) is False
    assert _has_non_plan_refs({"ref:geojson-abc": "schools"}) is True


@pytest.mark.asyncio
async def test_unresolved_ref_error_omits_session_plan(sid):
    from app.tools.registry import ToolRegistry, tool

    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": _gis("成都市小学分布情况", "成都市")},
        success=True,
    )
    await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []}, prefix="geojson",
    )
    registry = ToolRegistry()

    @tool(registry, name="echo_geojson", description="echo")
    def echo_geojson(geojson):
        return {"success": True}

    result = await registry.dispatch(
        "echo_geojson", {"geojson": "ref:missing-dead"}, session_id=sid,
    )
    message = str(result.get("message") or result.get("summary") or "")
    assert "无法找到引用数据或别名" in message
    assert "session-plan" not in message
    assert "sessionplan" not in message.lower()
    assert "ref:geojson-" in message


def test_goal_key_distinguishes_cities():
    a = goal_key(_gis("成都市小学分布情况", "成都市"))
    b = goal_key(_gis("分析北京学校", "北京市", subject="学校"))
    assert a != b
    assert goal_key(_gis("成都市小学分布情况", "成都市")) == a


@pytest.mark.asyncio
async def test_component_update_keeps_envelope(sid):
    """「换个颜色」 is a product step, not a new user goal."""
    gis = _gis("成都市小学分布情况", "成都市")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": gis}, success=True)
    before = await load_session_plan(sid)
    events = await apply_tool_result(
        sid, "webgis_component_update",
        {"success": True, "component_id": "title"},
        success=True,
    )
    after = await load_session_plan(sid)
    assert after.envelope_id == before.envelope_id
    assert after.gis_chapter["query"] == "成都市小学分布情况"
    assert SESSION_PLAN_SUPERSEDED not in [e.event for e in events]
