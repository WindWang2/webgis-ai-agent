"""J7: turn-owned artifact/MapSpec writes must not acquire the same lock twice."""
import asyncio
import contextvars
from unittest.mock import AsyncMock

import pytest

from app.services import distributed_lock as dl
from app.services.harness_kernel.legacy_adapter import bind_engine_lock, unbind_engine_lock


@pytest.fixture(params=["inprocess", "redis"])
def registry(request, monkeypatch):
    registry = dl.SessionLockRegistry()
    if request.param == "redis":
        import fakeredis.aioredis

        client = fakeredis.aioredis.FakeRedis()
    else:
        client = None
    monkeypatch.setattr(registry, "_get_client", lambda: client)
    monkeypatch.setattr(registry, "_is_redis_configured", lambda: client is not None)
    original_lock = registry.lock
    monkeypatch.setattr(registry, "lock", lambda sid, **kw: original_lock(
        sid, **{**kw, "acquire_timeout_s": 0.05},
    ))
    monkeypatch.setattr(dl, "session_lock_registry", registry)
    return registry


@pytest.mark.parametrize("operation", ["artifact", "mutation", "batch"])
async def test_turn_mutations_reuse_one_real_acquisition(registry, monkeypatch, operation):
    import app.services.artifact_registry as artifacts
    import app.services.mapspec.lifecycle_engine as le
    from tests.fixtures.mapspec_cow_fixtures import FakeSDM, FakeStore, fake_checkpoint, get_large_ref_spec

    sid = "j7-nested-mutation"
    acquisitions = []
    original_lock = registry.lock

    def counted_lock(*args, **kwargs):
        acquisitions.append(args[0])
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(registry, "lock", counted_lock)
    monkeypatch.setattr(le, "session_lock_registry", registry)
    monkeypatch.setattr(le, "session_data_manager", FakeSDM())
    monkeypatch.setattr(le, "create_checkpoint", fake_checkpoint)
    monkeypatch.setattr(artifacts, "_load_records", AsyncMock(return_value={}))
    save_records = AsyncMock()
    monkeypatch.setattr(artifacts, "_save_records", save_records)
    engine = le.MapSpecLifecycleEngine()
    engine.store = FakeStore(get_large_ref_spec())
    async with registry.lock(sid) as outer:
        token = bind_engine_lock(sid, outer)
        try:
            if operation == "artifact":
                result = await artifacts.register_tool_artifact(sid, "ref:j7", tool="buffer")
                assert result is not None
                save_records.assert_awaited_once()
            elif operation == "mutation":
                result = await engine.apply_mutation(sid, le.SetViewIntent(zoom=11))
                assert not result.is_error, result.error_msg
                assert engine.store.saved
            else:
                result = await engine.apply_presentation_batch(
                    sid, [le.PatchLayerPresentationIntent(layer_id="l1", visible=False)],
                )
                assert not result.is_error, result.error_msg
                assert engine.store.saved
            assert outer.locked(), "inner completion must never release the turn lock"
            assert acquisitions == [sid]
        finally:
            unbind_engine_lock(token)
    async with registry.lock(sid):
        pass


async def test_independent_request_still_contends(registry):
    async with registry.lock("same") as outer:
        token = bind_engine_lock("same", outer)
        try:
            async def independent():
                with pytest.raises(dl.LockContentionError):
                    async with dl.mutation_session_lock("same", registry=registry):
                        pytest.fail("independent request bypassed turn lock")

            await asyncio.create_task(independent(), context=contextvars.Context())
        finally:
            unbind_engine_lock(token)


async def test_wrong_session_and_unacquired_objects_do_not_authorize_reuse(registry):
    async with registry.lock("one") as outer:
        for sid, candidate in [("two", outer), ("one", registry.lock("one")), ("one", object())]:
            token = bind_engine_lock(sid, candidate)
            try:
                assert dl.current_session_lock(sid) is None
                with pytest.raises(dl.LockContentionError):
                    async with dl.mutation_session_lock("one", registry=registry):
                        pytest.fail("unowned lock authorized reuse")
            finally:
                unbind_engine_lock(token)
        token = bind_engine_lock("one", outer)
        try:
            async with dl.mutation_session_lock("two", registry=registry) as second:
                assert second is not outer
        finally:
            unbind_engine_lock(token)


async def test_released_object_is_not_owned_when_successor_holds_fallback(registry):
    old = registry.lock("same")
    async with old:
        pass
    async with registry.lock("same"):
        assert not old.locked()
        token = bind_engine_lock("same", old)
        try:
            assert dl.current_session_lock("same") is None
        finally:
            unbind_engine_lock(token)


async def test_inherited_context_is_revoked_after_turn(registry):
    start = asyncio.Event()
    async with registry.lock("same") as outer:
        token = bind_engine_lock("same", outer)

        async def detached():
            await start.wait()
            assert dl.current_session_lock("same") is None
            async with dl.mutation_session_lock("same", registry=registry) as fresh:
                assert fresh is not outer

        child = asyncio.create_task(detached())
        unbind_engine_lock(token)
    start.set()
    await child


async def test_sibling_mutations_serialize_and_cancel_without_releasing_outer(registry):
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()
    async with registry.lock("same") as outer:
        token = bind_engine_lock("same", outer)
        try:
            async def first():
                async with dl.mutation_session_lock("same"):
                    # Same-task nesting is safe, but sibling tasks must wait.
                    async with dl.mutation_session_lock("same"):
                        entered.set()
                        await release.wait()

            async def second():
                async with dl.mutation_session_lock("same"):
                    second_entered.set()

            one = asyncio.create_task(first())
            await entered.wait()
            two = asyncio.create_task(second())
            await asyncio.sleep(0)
            assert not second_entered.is_set()
            two.cancel()
            with pytest.raises(asyncio.CancelledError):
                await two
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert outer.locked()
            await second()
            assert second_entered.is_set()
        finally:
            unbind_engine_lock(token)


async def test_lost_turn_lock_fails_closed_without_reacquiring(registry):
    async with registry.lock("same", fail_on_lost=False) as outer:
        token = bind_engine_lock("same", outer)
        try:
            outer._lost = True
            with pytest.raises(dl.LockLostError):
                async with dl.mutation_session_lock("same"):
                    pytest.fail("lost ownership allowed a mutation")
        finally:
            unbind_engine_lock(token)


async def test_borrowed_lock_exposes_revocation_before_commit(registry):
    async with registry.lock("same") as outer:
        token = bind_engine_lock("same", outer)
        with pytest.raises(dl.LockLostError):
            async with dl.mutation_session_lock("same") as borrowed:
                unbind_engine_lock(token)
                assert borrowed.lost
        assert outer.locked()


async def test_degraded_outer_does_not_weaken_strict_mutations(monkeypatch):
    outer = dl._ResilientSessionLock(
        None, "webgis:sessionlock:same", dl._InProcessLock(), redis_configured=True,
    )
    async with outer:
        token = bind_engine_lock("same", outer)
        try:
            with pytest.raises(dl.LockDegradedError):
                async with dl.mutation_session_lock("same"):
                    pytest.fail("strict mutation borrowed degraded lock")
            async with dl.mutation_session_lock("same", fail_on_degraded=False):
                pass
        finally:
            unbind_engine_lock(token)


async def test_redis_token_is_rechecked_before_reuse(monkeypatch):
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis()
    outer = dl._ResilientSessionLock(client, "webgis:sessionlock:same", dl._InProcessLock())
    async with outer:
        token = bind_engine_lock("same", outer)
        try:
            await client.set(outer._key, "successor-token")
            assert not outer.lost  # renewer has not observed the stolen token yet
            with pytest.raises(dl.LockLostError):
                async with dl.mutation_session_lock("same"):
                    pytest.fail("stale Redis token allowed a mutation")
            assert outer.lost
        finally:
            unbind_engine_lock(token)
    assert await client.get(outer._key) == b"successor-token"


async def test_artifact_aborts_if_owner_is_lost_during_load(registry, monkeypatch):
    import app.services.artifact_registry as artifacts

    async with registry.lock("same", fail_on_lost=False) as outer:
        token = bind_engine_lock("same", outer)
        try:
            async def load(*args):
                outer._lost = True
                return {}

            monkeypatch.setattr(artifacts, "_load_records", load)
            save = AsyncMock()
            monkeypatch.setattr(artifacts, "_save_records", save)
            assert await artifacts.register_artifact("same", artifact_id="ref:test") is None
            save.assert_not_awaited()
        finally:
            unbind_engine_lock(token)


async def test_cross_context_close_revokes_exact_binding(registry):
    async with registry.lock("same") as outer:
        binding = bind_engine_lock("same", outer)

        async def close_elsewhere():
            async with registry.lock("unrelated") as unrelated:
                other_binding = bind_engine_lock("unrelated", unrelated)
                try:
                    unbind_engine_lock(binding)
                    assert other_binding.scope.active
                    assert dl.current_session_lock("unrelated") is unrelated
                finally:
                    unbind_engine_lock(other_binding)

        await asyncio.create_task(close_elsewhere(), context=contextvars.Context())
        try:
            assert dl.current_session_lock("same") is None
            assert not binding.scope.active
            with pytest.raises(dl.LockContentionError):
                async with dl.mutation_session_lock("same"):
                    pytest.fail("cross-context close left stale authority")
        finally:
            unbind_engine_lock(binding)


@pytest.mark.parametrize("streaming", [False, True])
async def test_real_chat_engine_binds_lock_acquired_by_stream_helper(registry, monkeypatch, streaming):
    from app.services.chat_engine import ChatEngine
    from app.services.harness_kernel import legacy_adapter
    from app.tools.registry import ToolRegistry
    import app.services.artifact_registry as artifacts
    import app.services.mapspec.lifecycle_engine as le
    from tests.fixtures.mapspec_cow_fixtures import FakeSDM, FakeStore, fake_checkpoint, get_large_ref_spec

    engine = ChatEngine(ToolRegistry())
    sid = "j7-real-chat-stream" if streaming else "j7-real-chat"
    monkeypatch.setattr(engine, "_get_or_create_session", AsyncMock(return_value=[]))
    for name in ("_generate_title", "_save_msg_async", "_flush_plan"):
        monkeypatch.setattr(engine, name, AsyncMock())
    for name in ("begin_turn", "safe_end_turn", "settle_after_lock"):
        monkeypatch.setattr(legacy_adapter, name, AsyncMock())
    monkeypatch.setattr(artifacts, "_load_records", AsyncMock(return_value={}))
    monkeypatch.setattr(artifacts, "_save_records", AsyncMock())
    monkeypatch.setattr(le, "session_lock_registry", registry)
    monkeypatch.setattr(le, "session_data_manager", FakeSDM())
    monkeypatch.setattr(le, "create_checkpoint", fake_checkpoint)
    maps = le.MapSpecLifecycleEngine()
    maps.store = FakeStore(get_large_ref_spec())
    visited = []

    async def writes():
        outer = dl.current_session_lock(sid)
        assert outer is not None and outer.locked()
        record = await artifacts.register_tool_artifact(sid, "ref:engine", tool="buffer")
        assert record is not None
        result = await maps.apply_mutation(sid, le.SetViewIntent(zoom=12))
        assert not result.is_error, result.error_msg
        assert outer.locked()
        visited.append(sid)

    async def chat_body(*args, **kwargs):
        await writes()
        return {"response": "done"}

    async def empty_phase(ctx):
        for item in ():
            yield item

    async def stream_rounds(ctx):
        await writes()
        yield "done"

    monkeypatch.setattr(engine, "_chat_locked", chat_body)
    monkeypatch.setattr(engine, "_stream_planner_phase", empty_phase)
    monkeypatch.setattr(engine, "_stream_rounds", stream_rounds)
    if streaming:
        assert [event async for event in engine.chat_stream("show buffer", session_id=sid)]
    else:
        assert await engine.chat("show buffer", session_id=sid) == {"response": "done"}
    assert visited == [sid]
    assert dl.current_session_lock(sid) is None
    async with registry.lock(sid):
        pass
