"""SessionPlan store + apply (no live LLM, no Pi RPC)."""
import asyncio

import pytest

from app.services.distributed_lock import session_lock_registry
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
    open_capabilities,
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
    voided = [e for e in events if e.event == SESSION_PLAN_PROGRESS]
    assert all(e.data["status"] == "voided" for e in voided)
    plan = await load_session_plan(sid)
    assert plan.replaced is True
    # Store is plan truth: voided SSE must match voided rows, not a silent
    # re-init back to pending.
    assert all(row.status == "voided" for row in plan.progress)
    # Voided completions are open again — the chapter requirement is pending.
    assert set(open_capabilities(plan)) >= {"poi_query", "admin_boundary", "heatmap"}


@pytest.mark.asyncio
async def test_product_completeness_does_not_complete_unrun_caps(sid):
    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": _gis("成都市小学分布情况", "成都市")},
        success=True,
    )
    events = await apply_tool_result(
        sid, "webgis_map_product",
        {
            "success": True,
            "recipe_id": "poi_distribution_overview",
            "status": "finalized",
            "completeness": {"missing": [], "complete": True},
            "map_product_evidence": {
                "capability_resolution": [
                    {"capability": "poi_query", "status": "available",
                     "resolved_tool": "query_local_poi"},
                    {"capability": "admin_boundary", "status": "pending",
                     "resolved_tool": "get_local_admin_boundary"},
                ],
            },
        },
        success=True, geojson_ref="ref:geojson-poi",
    )
    plan = await load_session_plan(sid)
    statuses = {row.capability: row.status for row in plan.progress}
    assert statuses["poi_query"] == "complete"
    # completeness.missing == [] covers product outputs, not capabilities —
    # never-run admin_boundary/heatmap must stay open, not flip complete.
    assert statuses["admin_boundary"] == "pending"
    assert statuses["heatmap"] == "pending"
    progressed = {
        e.data["capability"]: e.data["status"]
        for e in events
        if e.event == SESSION_PLAN_PROGRESS
    }
    assert progressed == {"poi_query": "complete"}


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
    # The current envelope is the replacement, not the superseded one —
    # its projection must not keep flagging superseded on later turns.
    assert "superseded=false" in format_session_plan_projection(new)
    from app.services.session_plan import HISTORY_ALIAS_PREFIX, SessionPlan
    ref = await session_data_manager.resolve_alias(
        sid, f"{HISTORY_ALIAS_PREFIX}{old.envelope_id}"
    )
    archived = SessionPlan.model_validate(
        await session_data_manager.get(sid, ref)
    )
    assert archived.superseded is True
    assert "superseded=true" in format_session_plan_projection(archived)


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


@pytest.mark.asyncio
async def test_apply_tool_result_serializes_on_session_lock(sid):
    """Parallel tool callbacks in one Pi turn must not interleave
    load→mutate→save on the envelope — apply waits for the per-session lock."""
    gis = _gis("成都市小学分布情况", "成都市")
    await ensure_session_plan_slot(sid)

    async with session_lock_registry.lock(sid):
        task = asyncio.create_task(
            apply_tool_result(
                sid, "webgis_map_intent",
                {"success": True, "plan": gis, "intent": gis["intent"]},
                success=True,
            )
        )
        await asyncio.sleep(0.05)
        assert not task.done(), "apply must block while the session lock is held"
        assert (await load_session_plan(sid)).gis_chapter is None

    events = await asyncio.wait_for(task, timeout=5.0)
    assert any(e.event == SESSION_PLAN_UPDATED for e in events)
    assert (await load_session_plan(sid)).gis_chapter is not None


@pytest.mark.asyncio
async def test_slot_open_serializes_on_session_lock(sid):
    """Slot-open is equally serialized (ADR-0051): two first-turn tools racing
    to open the envelope must block on the per-session lock and observe ONE
    envelope — never two competing first-turn envelopes."""
    async with session_lock_registry.lock(sid):
        t1 = asyncio.create_task(ensure_session_plan_slot(sid))
        t2 = asyncio.create_task(ensure_session_plan_slot(sid))
        await asyncio.sleep(0.05)
        assert not t1.done(), "slot-open must block while the session lock is held"
        assert not t2.done(), "slot-open must block while the session lock is held"
        assert await load_session_plan(sid) is None, (
            "no envelope may exist while the lock is still held — creation "
            "must wait for the slot-open critical section"
        )

    p1 = await asyncio.wait_for(t1, timeout=5.0)
    p2 = await asyncio.wait_for(t2, timeout=5.0)
    stored = await load_session_plan(sid)
    assert p1.envelope_id == p2.envelope_id == stored.envelope_id, (
        "two first-turn tools must not create competing envelopes"
    )
    assert stored.gis_chapter is None


@pytest.mark.asyncio
async def test_supersede_survives_concurrent_capability_write(sid):
    """A supersede racing a capability write must never be lost to
    last-write-wins (user stories #1029-6/#1029-21): after both applies the
    current envelope is the new goal's, and the old one is archived superseded."""
    gis = _gis("成都市小学分布情况", "成都市")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": gis}, success=True)
    old = await load_session_plan(sid)
    new_gis = _gis("分析北京学校", "北京市", subject="学校")

    async with session_lock_registry.lock(sid):
        cap_task = asyncio.create_task(
            apply_tool_result(
                sid, "heatmap_data", {"success": True},
                success=True, geojson_ref="ref:geojson-heat",
            )
        )
        intent_task = asyncio.create_task(
            apply_tool_result(sid, "webgis_map_intent", {"plan": new_gis}, success=True)
        )
        await asyncio.sleep(0.05)
        assert not cap_task.done() and not intent_task.done(), (
            "both applies must block while the session lock is held — "
            "serialization is observed, not assumed"
        )

    await asyncio.wait_for(asyncio.gather(cap_task, intent_task), timeout=5.0)
    final = await load_session_plan(sid)
    # The user goal change is never lost: the new goal's envelope is current.
    assert final.gis_chapter["intent"]["scope"]["name"] == "北京市"
    assert final.envelope_id != old.envelope_id
    assert final.superseded is False
    # The old envelope is archived as superseded — a stale capability write
    # must not resurrect it as the current envelope.
    from app.services.session_plan import HISTORY_ALIAS_PREFIX, SessionPlan
    ref = await session_data_manager.resolve_alias(
        sid, f"{HISTORY_ALIAS_PREFIX}{old.envelope_id}"
    )
    archived = SessionPlan.model_validate(await session_data_manager.get(sid, ref))
    assert archived.superseded is True
    assert archived.envelope_id == old.envelope_id


# ── v3(Phase E 收尾)：failed 标记 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_dispatch_marks_capability_failed(sid):
    """数据工具失败 → 命中的能力行标 failed（progress + 两类扁平行），
    SSE progress 事件发出，open_capabilities 把 failed 视为 open（可重试）。"""
    await apply_tool_result(sid, "webgis_map_intent", {"plan": _gis("成都小学分布", "成都")}, success=True)
    events = await apply_tool_result(
        sid, "query_local_poi", {"success": False, "message": "boom"}, success=False,
    )
    assert events and all(e.event == SESSION_PLAN_PROGRESS for e in events)
    assert {e.data["capability"] for e in events} == {"poi_query"}
    assert all(e.data["status"] == "failed" for e in events)

    plan = await load_session_plan(sid)
    row = next(r for r in plan.progress if r.capability == "poi_query")
    assert row.status == "failed"
    req = next(
        r for r in plan.gis_chapter["data_requirements"] if r["capability"] == "poi_query"
    )
    assert req["status"] == "failed"
    # fixture 的 analysis_steps 只有 heatmap 行（无 poi_query）——失败标记
    # 的命中范围仅限真实存在的行，不凭空造行。
    assert not any(
        r["capability"] == "poi_query" for r in plan.gis_chapter["analysis_steps"]
    )
    assert "poi_query" in open_capabilities(plan)

    # 投影披露 [GIS Plan] Failed（retry-able）行
    text = format_session_plan_projection(plan)
    assert "Failed (retry-able): poi_query" in text

    # 重试成功覆写为 complete（failed 不是终态）
    await apply_tool_result(
        sid, "query_local_poi", {"success": True}, success=True,
        geojson_ref="ref:geojson:retry-1",
    )
    plan2 = await load_session_plan(sid)
    row2 = next(r for r in plan2.progress if r.capability == "poi_query")
    assert row2.status == "complete"
    assert row2.bound_ref == "ref:geojson:retry-1"


@pytest.mark.asyncio
async def test_failed_planner_tool_is_noop(sid):
    """webgis_* 规划入口失败不映射到能力行（无确定受害能力，章节保持原状）。"""
    await apply_tool_result(sid, "webgis_map_intent", {"plan": _gis("成都小学分布", "成都")}, success=True)
    events = await apply_tool_result(
        sid, "webgis_map_product", {"success": False}, success=False,
    )
    assert events == []
    plan = await load_session_plan(sid)
    assert all(r.status != "failed" for r in plan.progress)


@pytest.mark.asyncio
async def test_failed_dispatch_without_chapter_is_noop(sid):
    """空 envelope（无章节）时失败标记无落点——不建章节、不发事件。"""
    events = await apply_tool_result(
        sid, "query_local_poi", {"success": False}, success=False,
    )
    assert events == []
    plan = await load_session_plan(sid)
    assert plan.gis_chapter is None
