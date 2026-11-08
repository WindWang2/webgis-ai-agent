"""Post-dispatch borrowers retain exclusion and fail closed on revoked leases."""
import asyncio
import contextvars
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import cartography_runtime as carto
from app.services import distributed_lock as dl
from app.services.harness_kernel import runtime
from app.services.session_plan import SessionPlan
from app.lib.harness.tool_call_event import ToolCallEvent
from tests.unit.test_nested_session_mutations import registry as nested_registry

registry = nested_registry


@pytest.fixture(params=["journal", "context", "issued", "evaluation", "backfill"])
def operation(request, monkeypatch, registry):
    sid = "postdispatch-safety"
    state = {
        "_cartographic_mutation_revision": 1,
        "_cartographic_harness_context": {
            "session_id": sid, "mutation_revision": 1, "map_actions": [],
        },
        "_cartographic_repair_state": {"attempts": [{"action_id": "repair"}]},
    }
    store = SimpleNamespace(
        invalidate_local_cache=Mock(),
        get_map_state=AsyncMock(side_effect=lambda *_: state),
        get_state_field=AsyncMock(side_effect=lambda _, key: state.get(key)),
        set_map_state=AsyncMock(return_value=True),
        get_map_action_events=AsyncMock(return_value=[]),
    )
    import app.services.session_data as session_data
    from app.services.mapspec_store import mapspec_store

    monkeypatch.setattr(session_data, "session_data_manager", store)
    monkeypatch.setattr(runtime, "session_lock_registry", registry)
    plan = SessionPlan(envelope_id="safety", session_id=sid)
    loader = AsyncMock(return_value=plan)
    saved = AsyncMock(return_value=True)
    monkeypatch.setattr(runtime, "load_session_plan", loader)
    monkeypatch.setattr(runtime, "_save_if_fresh", saved)
    harness = SimpleNamespace(
        evaluate_with_evidence=AsyncMock(return_value={"cartography": {}}),
        tool_results=[],
    )
    monkeypatch.setattr(carto, "_get_session_harness", lambda *_, **kw: harness)
    monkeypatch.setattr(carto, "_hydrate_cartographic_harness", AsyncMock(return_value=True))
    monkeypatch.setattr(carto, "_spawn_quality_fact_record", Mock())
    monkeypatch.setattr(mapspec_store, "layer_upsert", AsyncMock(return_value={"success": False}))
    # Isolate cache state so each operation reaches its awaited reads.
    monkeypatch.setattr(carto, "_cartography_eval_cache", {})
    event = ToolCallEvent(
        tool_call_id="safety", tool_name="webgis_layer_upsert", arguments={},
        result={"mutation_revision": 1}, session_id=sid,
    )

    async def call(session_id=sid):
        if request.param == "journal":
            return await runtime.GISSessionRuntime(session_id).record_map_mutation(mutation_id="mutation")
        if request.param == "context":
            return await carto._persist_cartographic_harness_context(session_id, event, [])
        if request.param == "issued":
            return await carto._persist_cartographic_issued_action(session_id, {"action_id": "repair"})
        if request.param == "evaluation":
            return await carto.evaluate_cartographic_session(session_id)
        return await carto._execute_selfheal_commit(session_id, {
            "commit_call_id": "repair", "iterations": [{"layer": {"id": "layer"}}],
        })

    return SimpleNamespace(
        sid=sid, call=call, store=store, load=loader, saved=saved,
        plan=plan, state=state, kind=request.param,
    )


async def test_postdispatch_rejects_lost_owner_before_writes(registry, operation):
    async with registry.lock(operation.sid, fail_on_lost=False) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)
        try:
            outer._lost = True
            with pytest.raises(dl.LockLostError):
                await operation.call()
            operation.store.set_map_state.assert_not_awaited()
            operation.saved.assert_not_awaited()
        finally:
            dl.unbind_session_lock(binding)


async def test_postdispatch_keeps_independent_requests_excluded(registry, operation):
    async with registry.lock(operation.sid) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)
        try:
            with pytest.raises(dl.LockContentionError):
                await asyncio.create_task(operation.call(), context=contextvars.Context())
            assert outer.locked()
            operation.store.set_map_state.assert_not_awaited()
            operation.saved.assert_not_awaited()
        finally:
            dl.unbind_session_lock(binding)


async def test_postdispatch_rejects_degraded_outer(operation):
    outer = dl._ResilientSessionLock(
        None, f"webgis:sessionlock:{operation.sid}", dl._InProcessLock(),
        redis_configured=True,
    )
    async with outer:
        binding = dl.bind_session_lock(operation.sid, outer)
        try:
            with pytest.raises(dl.LockDegradedError):
                await operation.call()
            operation.store.set_map_state.assert_not_awaited()
            operation.saved.assert_not_awaited()
        finally:
            dl.unbind_session_lock(binding)


@pytest.mark.parametrize("failure", ["lost", "revoked"])
async def test_postdispatch_aborts_if_ownership_changes_during_read(registry, operation, failure):
    async with registry.lock(operation.sid, fail_on_lost=False) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)

        def revoke():
            if failure == "lost":
                outer._lost = True
            elif binding.scope.active:
                dl.unbind_session_lock(binding)

        async def read_state(*args):
            revoke()
            return operation.state

        async def read_field(_, key):
            revoke()
            return operation.state.get(key)

        async def read_plan(*args, **kwargs):
            revoke()
            return operation.plan

        operation.store.get_map_state.side_effect = read_state
        operation.store.get_state_field.side_effect = read_field
        operation.load.side_effect = read_plan
        try:
            with pytest.raises(dl.LockLostError):
                await operation.call()
            operation.store.set_map_state.assert_not_awaited()
            operation.saved.assert_not_awaited()
            assert operation.state["_cartographic_repair_state"]["attempts"] == [{"action_id": "repair"}]
        finally:
            if binding.scope.active:
                dl.unbind_session_lock(binding)


async def test_postdispatch_siblings_serialize_and_cancel_cleanly(registry, operation):
    entered = asyncio.Event()
    resume = asyncio.Event()
    starts = []

    async def pause(value):
        starts.append(asyncio.current_task())
        entered.set()
        await resume.wait()
        return value

    async def state_read(*_):
        return await pause(operation.state)

    async def field_read(_, key):
        return await pause(operation.state.get(key))

    async def plan_read(*_, **kw):
        return await pause(operation.plan)

    operation.store.get_map_state.side_effect = state_read
    operation.store.get_state_field.side_effect = field_read
    operation.load.side_effect = plan_read
    async with registry.lock(operation.sid) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)
        one = two = None
        try:
            one = asyncio.create_task(operation.call())
            await asyncio.wait_for(entered.wait(), 1)
            two = asyncio.create_task(operation.call())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert starts == [one], "sibling bypassed the borrowed lock's mutex"
            two.cancel()
            with pytest.raises(asyncio.CancelledError):
                await two
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert outer.locked()
            resume.set()
            await operation.call()
            assert outer.locked()
            assert operation.store.set_map_state.await_count + operation.saved.await_count == 1
        finally:
            for task in (one, two):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            dl.unbind_session_lock(binding)
    async with registry.lock(operation.sid):
        pass


async def test_postdispatch_rechecks_stolen_redis_token(registry, operation):
    if registry._get_client() is None:
        pytest.skip("token theft applies to the Redis backend")
    async with registry.lock(operation.sid, fail_on_lost=False) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)
        try:
            await outer._client.set(outer._key, "successor-token")
            with pytest.raises(dl.LockLostError):
                await operation.call()
            operation.store.set_map_state.assert_not_awaited()
            operation.saved.assert_not_awaited()
        finally:
            dl.unbind_session_lock(binding)
    assert await outer._client.get(outer._key) == b"successor-token"


async def test_inherited_revoked_context_must_acquire_again(registry, operation):
    start = asyncio.Event()
    async with registry.lock(operation.sid) as outer:
        binding = dl.bind_session_lock(operation.sid, outer)

        async def detached():
            await start.wait()
            with pytest.raises(dl.LockContentionError):
                await operation.call()

        child = asyncio.create_task(detached())
        dl.unbind_session_lock(binding)
        start.set()
        await child
        operation.store.set_map_state.assert_not_awaited()
        operation.saved.assert_not_awaited()


async def test_repair_state_aborts_after_revocation_during_mapspec_read(registry, monkeypatch):
    from app.services.mapspec.store import mapspec_store_instance
    from app.services.session_data import session_data_manager

    write = AsyncMock()
    monkeypatch.setattr(session_data_manager, "set_map_state", write)
    sid = "repair-state-revocation"
    async with registry.lock(sid) as outer:
        binding = dl.bind_session_lock(sid, outer)

        async def get_mapspec(_):
            dl.unbind_session_lock(binding)
            return {"layers": []}

        monkeypatch.setattr(mapspec_store_instance, "get_mapspec", get_mapspec)
        with pytest.raises(dl.LockLostError):
            async with dl.mutation_session_lock(sid) as borrowed:
                await carto._advance_runtime_cartographic_repair(
                    session_id=sid, harness=SimpleNamespace(),
                    result={"cartography": {"mapspec_fingerprint": "fp", "status": "passed"}},
                    map_state={"_cartographic_observation": {}}, actions=[],
                    _session_lock=borrowed,
                )
        write.assert_not_awaited()
