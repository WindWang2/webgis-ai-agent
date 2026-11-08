"""Regression: post-dispatch writes borrow the turn's verified lock, too.

Exercise the facade and pipeline, not only MapSpecLifecycleEngine: both journal
and cartographic hooks are best-effort and can silently lose writes on contention.
"""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.services import distributed_lock as dl
from app.services.harness_kernel.legacy_adapter import bind_engine_lock, unbind_engine_lock


@pytest.fixture(params=["inprocess", "redis"])
def registry(request, monkeypatch):
    registry = dl.SessionLockRegistry()
    client = None
    if request.param == "redis":
        import fakeredis.aioredis
        client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(registry, "_get_client", lambda: client)
    monkeypatch.setattr(registry, "_is_redis_configured", lambda: client is not None)
    lock = registry.lock
    registry.acquisitions = []
    registry.test_task = None

    def counted(sid, **kwargs):
        # Detached ref-staleness propagation is an independent request and may
        # legitimately contend; count acquisitions in the exercising task only.
        if asyncio.current_task() is registry.test_task:
            registry.acquisitions.append(sid)
        return lock(sid, **{**kwargs, "acquire_timeout_s": 0.05})

    monkeypatch.setattr(registry, "lock", counted)
    monkeypatch.setattr(dl, "session_lock_registry", registry)
    import app.services.harness_kernel.runtime as runtime
    import app.services.mapspec.lifecycle_engine as lifecycle
    monkeypatch.setattr(runtime, "session_lock_registry", registry)
    monkeypatch.setattr(lifecycle, "session_lock_registry", registry)
    return registry


@pytest.fixture
async def session(registry):
    from app.services.harness_kernel import get_runtime
    from app.services.session_data import session_data_manager
    from app.services import cartography_runtime

    sid = "postdispatch-" + uuid.uuid4().hex
    await get_runtime(sid).hydrate()
    registry.acquisitions.clear()
    yield sid
    cartography_runtime._discard_session_harness(sid)
    await session_data_manager.clear_session(sid)


@asynccontextmanager
async def turn_lock(registry, sid, bound=True):
    if not bound:
        yield None
        return
    async with registry.lock(sid) as outer:
        token = bind_engine_lock(sid, outer)
        try:
            yield outer
            assert outer.locked(), "post-dispatch must not release the turn lock"
        finally:
            unbind_engine_lock(token)


def pipeline_for(sid):
    from app.services.chat.tool_pipeline import ToolExecutionPipeline
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import SetViewIntent
    from app.tools.registry import ToolRegistry, tool

    tools = ToolRegistry()

    @tool(tools, name="postdispatch_set_view", description="Set the test map view")
    async def set_view(zoom: int) -> dict:
        result = await apply_gis_mutation(
            sid, SetViewIntent(zoom=zoom), actor="test",
            mutation_id=f"view-{zoom}", turn_id=f"turn-{zoom}",
        )
        assert not result.is_error, result.error_msg
        return result.to_dict()

    return tools, ToolExecutionPipeline(tools)


async def dispatch(pipeline, sid, zoom):
    result = await pipeline.execute_tool_call({
        "id": f"call-{zoom}",
        "function": {"name": "postdispatch_set_view", "arguments": {"zoom": zoom}},
    }, session_id=sid)
    assert not result.is_error, result.llm_payload
    assert result.raw_result["mapspec_fingerprint"]
    return result


async def assert_durable_evidence(sid, zoom):
    from app.services.session_data import session_data_manager
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(sid)
    rows = [row for row in plan.decisions if row.kind == "map_mutated"]
    matched = [row for row in rows if row.causal_id == f"view-{zoom}"]
    assert len(matched) == 1, "successful facade mutation lost its journal event"
    assert matched[0].turn_id == f"turn-{zoom}"
    state = await session_data_manager.get_map_state(sid)
    context = state.get("_cartographic_harness_context")
    assert context, "successful dispatch lost its durable cartographic context"
    assert context["tool_call"]["tool_call_id"] == f"call-{zoom}"
    assert context["mutation_revision"] == state["_cartographic_mutation_revision"]
    assert matched[0].detail["revision"] == context["mutation_revision"]
    assert state.get("_cartographic_review"), "post-dispatch evaluation was skipped"
    return state


@pytest.mark.parametrize("bound", [True, False])
async def test_real_pipeline_persists_facade_journal_and_cartography(registry, session, bound):
    _, pipeline = pipeline_for(session)
    registry.test_task = asyncio.current_task()
    async with turn_lock(registry, session, bound):
        await dispatch(pipeline, session, 11)
        await assert_durable_evidence(session, 11)
        if bound:
            assert registry.acquisitions == [session]
        else:
            assert len(registry.acquisitions) >= 4
    async with registry.lock(session):
        pass


async def test_two_chat_stream_turns_keep_real_postdispatch_writes(registry, session, monkeypatch):
    from app.services.chat_engine import ChatEngine

    tools, _ = pipeline_for(session)
    registry.test_task = asyncio.current_task()
    engine = ChatEngine(tools)
    # Skip planning and external conversation persistence/title generation;
    # fake provider completions only within the tool/answer loop. Real rounds,
    # tool waves, lock binding, and turn lifecycle hooks still run.
    monkeypatch.setattr(engine, "_get_or_create_session", AsyncMock(return_value=[]))
    for name in ("_generate_title", "_save_msg_async", "_flush_plan"):
        monkeypatch.setattr(engine, name, AsyncMock())

    async def no_planner(ctx):
        if False:
            yield None

    completions = iter([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": f"call-{zoom}", "type": "function", "function": {
                "name": "postdispatch_set_view",
                "arguments": json.dumps({"zoom": zoom}),
            },
        }]} if tool_round else {
            "role": "assistant", "content": "Map view updated.", "tool_calls": None,
        }
        for zoom in (11, 12) for tool_round in (True, False)
    ])

    async def llm_stream(*args, **kwargs):
        outer = dl.current_session_lock(session)
        assert outer is not None and outer.locked()
        yield "done", {"message": next(completions)}

    monkeypatch.setattr(engine, "_stream_planner_phase", no_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", llm_stream)
    for zoom in (11, 12):
        async with asyncio.timeout(15):
            events = [event async for event in engine.chat_stream("set map view", session_id=session)]
        types = [line.removeprefix("event: ") for event in events
                 for line in event.splitlines() if line.startswith("event: ")]
        assert types.count("task_complete") == 1, events
        assert types.count("done") == 1, events
        assert "task_error" not in types
        tasks = engine.tracker.list_by_session(session)
        assert tasks and all(task.status.value == "completed" for task in tasks)
        assert dl.current_session_lock(session) is None
        await assert_durable_evidence(session, zoom)
    assert registry.acquisitions == [session, session]


@pytest.mark.parametrize("operation", ["issued_action", "selfheal_backfill"])
async def test_repair_writes_borrow_turn_lock(registry, session, operation):
    from app.services import cartography_runtime as cartography
    from app.services.session_data import session_data_manager

    _, pipeline = pipeline_for(session)
    await dispatch(pipeline, session, 11)
    await session_data_manager.set_map_state(session, "_cartographic_repair_state", {
        "attempts": [{"action_id": "repair-1", "status": "issued"}],
    })
    registry.acquisitions.clear()
    registry.test_task = asyncio.current_task()
    async with turn_lock(registry, session):
        if operation == "issued_action":
            await cartography._persist_cartographic_issued_action(session, {
                "action_id": "repair-1", "command": "setPaintProperty",
                "requested": {"layer_id": "missing"},
            })
            state = await session_data_manager.get_map_state(session)
            assert state["_cartographic_harness_context"]["map_actions"][-1]["action_id"] == "repair-1"
            assert registry.acquisitions == [session]
            return
        # An invalid real layer commit must still mark its issued attempt failed.
        # No mock of the lifecycle/facade or repair-state backfill is involved.
        await cartography._execute_selfheal_commit(session, {
            "commit_call_id": "repair-1",
            "iterations": [{"layer_id": "invalid", "layer": {"id": "invalid", "type": "not-a-layer"}}],
        })
        state = await session_data_manager.get_map_state(session)
        attempt = state["_cartographic_repair_state"]["attempts"][0]
        assert attempt["status"] == "failed"
        assert attempt["commits"]
        assert registry.acquisitions == [session]
