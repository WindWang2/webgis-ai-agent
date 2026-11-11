"""Review-R2 #1950: primary writer admission, lifecycle and attribution."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge
from app.services.chat import pi_bridge_tasks, pi_writer_gate
from app.services.distributed_lock import SessionLockRegistry


@pytest.fixture(autouse=True)
def local_gate(monkeypatch):
    import app.services.distributed_lock as locks
    registry = SessionLockRegistry()
    monkeypatch.setattr(registry, "_get_client", lambda: None)
    monkeypatch.setattr(registry, "_is_redis_configured", lambda: False)
    monkeypatch.setattr(locks, "session_lock_registry", registry)
    monkeypatch.setenv("GIS_SWARM_ORCHESTRATOR", "1")
    return registry


def _swarm(run):
    return bridge.SwarmBridge(
        "low-session", orchestrator_factory=lambda sid: SimpleNamespace(run_swarm=run),
        projection_builder=lambda *args: None,
    )


def _status():
    return SimpleNamespace(manifest=None, run_id="swarm-low", state="succeeded", counts={}, manifest_ref=None)


@pytest.mark.asyncio
async def test_policy_source_survives_forward_to_owning_worker(monkeypatch):
    target = SimpleNamespace(abort=AsyncMock(return_value={"aborted": True}))
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: SimpleNamespace(bridge=target))
    result = await bridge.PiBridge(rpc=MagicMock()).abort("low-session", source="policy")
    assert result == {"aborted": True}
    target.abort.assert_awaited_once_with("low-session", source="policy")


@pytest.mark.asyncio
async def test_empty_tool_session_rejects_before_any_token_borrow():
    request = bridge.PiToolRequest(toolCallId="empty", name="webgis_execute", arguments={})
    with pytest.raises(bridge.PiRpcError, match="no verified turn session"):
        await bridge._dispatch_tool_bound(request, object(), request.name, {}, "")


@pytest.mark.asyncio
async def test_policy_abort_task_retained_and_failure_observed(monkeypatch, caplog):
    ready, finish = asyncio.Event(), asyncio.Event()
    async def fail(**kwargs):
        ready.set()
        await finish.wait()
        raise RuntimeError("abort RPC unavailable")
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: SimpleNamespace(token=None, bridge=SimpleNamespace(abort=fail)))
    bridge._hard_stop_pi_turn_for_no_progress("low-session")
    await ready.wait()
    assert len(pi_bridge_tasks._pending_aborts) == 1
    finish.set()
    await asyncio.gather(*pi_bridge_tasks._pending_aborts, return_exceptions=True)
    await asyncio.sleep(0)
    assert not pi_bridge_tasks._pending_aborts
    assert "Pi policy abort failed" in caplog.text


def test_cleanup_preserves_other_sessions_and_anonymous_bucket():
    cache = {("a", "tool"): 1, ("b", "tool"): 2, ("", "tool"): 3, "": 4, (): 5}
    bridge._pop_session_entries(cache, "a")
    assert cache == {("b", "tool"): 2, ("", "tool"): 3, "": 4, (): 5}
    bridge._pop_session_entries(cache, "")
    assert cache == {("b", "tool"): 2, (): 5}


def test_explicit_anonymous_cache_clear_does_not_become_global_clear(monkeypatch):
    results = {("", "tool"): 1, ("other", "tool"): 2}
    plans = dict(results)
    monkeypatch.setattr(bridge, "_dispatch_result_cache", results)
    monkeypatch.setattr(bridge, "_session_plan_sse_cache", plans)
    bridge._clear_dispatch_cache("")
    assert results == plans == {("other", "tool"): 2}


@pytest.mark.asyncio
async def test_task_tracker_failure_is_observable_without_losing_tool_result(monkeypatch, caplog):
    from app.tools.registry import ToolRegistry
    import app.services.chat.engine_instance as engines
    import app.services.chat.pi_post_dispatch as disclosure
    registry = ToolRegistry()
    registry.register("review_pure", "review", lambda: {"summary": "ok"}, parameters={"type": "object", "properties": {}})
    monkeypatch.setattr(bridge, "get_tool_registry", lambda: registry)
    tracker = SimpleNamespace(list_by_session=MagicMock(side_effect=RuntimeError("tracker unavailable")))
    monkeypatch.setattr(engines, "try_get_chat_engine", lambda: SimpleNamespace(tracker=tracker))
    monkeypatch.setattr(disclosure, "apply_post_dispatch_disclosure", AsyncMock(return_value=disclosure.DisclosureOutcome()))
    request = bridge.PiToolRequest(
        toolCallId="low-accounting", name="webgis_execute", sessionId="low-accounting",
        arguments={"toolName": "review_pure", "arguments": {}},
    )
    result = await bridge.dispatch_tool(request)
    assert not result.isError
    assert "Pi task step accounting unavailable" in caplog.text


@pytest.mark.parametrize("raw,expected", [("bad", 1), ("", 1), ("-4", 1), ("999999999", 64), ("2", 2)])
def test_pool_setting_is_tolerant_and_bounded(monkeypatch, raw, expected):
    monkeypatch.setenv("PI_BRIDGE_POOL_SIZE", raw)
    assert pi_bridge_tasks.bridge_pool_size() == expected


@pytest.mark.asyncio
async def test_swarm_cannot_enter_pi_writer_registration_window(monkeypatch):
    # The table is deliberately still empty: the shared atomic gate, rather
    # than a check of that eventually published table, must block Swarm.
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: None)
    pi = bridge.PiBridge(rpc=MagicMock())
    lease = await pi._acquire_turn_lease("turn-low", "low-session")
    run = AsyncMock(return_value=_status())
    try:
        assert await _swarm(run).delegate_compound_task("goal") == {
            "delegated": False, "reason": "session_writer_busy",
        }
        run.assert_not_called()
    finally:
        pi._release_turn_lease(lease)


@pytest.mark.asyncio
async def test_pi_waits_for_swarm_writer_and_resumes_after_release(monkeypatch):
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: None)
    entered, finish = asyncio.Event(), asyncio.Event()
    async def run(*args, **kwargs):
        entered.set()
        await finish.wait()
        return _status()
    swarm_task = asyncio.create_task(_swarm(run).delegate_compound_task("goal"))
    await entered.wait()
    pi = bridge.PiBridge(rpc=MagicMock())
    acquisition = asyncio.create_task(pi._acquire_turn_lease("turn-low", "low-session"))
    await asyncio.sleep(0.01)
    assert not acquisition.done()
    finish.set()
    assert (await swarm_task)["delegated"]
    lease = await asyncio.wait_for(acquisition, 1)
    pi._release_turn_lease(lease)


@pytest.mark.asyncio
async def test_cancelled_swarm_releases_writer_admission(monkeypatch):
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: None)
    entered = asyncio.Event()
    async def run(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(_swarm(run).delegate_compound_task("goal"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    lease = await pi_writer_gate.acquire_writer("low-session", timeout_s=0.1)
    lease.release()


@pytest.mark.asyncio
async def test_distributed_gate_is_shared_across_registry_instances(monkeypatch):
    import fakeredis.aioredis
    import app.services.distributed_lock as locks
    server = fakeredis.FakeServer()
    clients = [fakeredis.aioredis.FakeRedis(server=server) for _ in range(2)]
    registries = [SessionLockRegistry() for _ in clients]
    for registry, client in zip(registries, clients):
        monkeypatch.setattr(registry, "_get_client", lambda client=client: client)
        monkeypatch.setattr(registry, "_is_redis_configured", lambda: True)
    monkeypatch.setattr(locks, "session_lock_registry", registries[0])
    lease = await pi_writer_gate.acquire_writer("low-session", timeout_s=0.1)
    monkeypatch.setattr(locks, "session_lock_registry", registries[1])
    try:
        with pytest.raises(TimeoutError):
            await pi_writer_gate.acquire_writer("low-session", timeout_s=0.01)
    finally:
        lease.release()
        await asyncio.sleep(0.01)
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_lost_writer_cancels_actual_owner(monkeypatch):
    lock = SimpleNamespace(lost=False, acquire=AsyncMock(), release=MagicMock())
    import app.services.distributed_lock as locks
    monkeypatch.setattr(locks.session_lock_registry, "lock", lambda *a, **kw: lock)
    started = asyncio.Event()
    async def own():
        lease = await pi_writer_gate.acquire_writer("low-session", timeout_s=0.1)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            lease.release()
    task = asyncio.create_task(own())
    await started.wait()
    lock.lost = True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_configured_redis_outage_cannot_fall_back_to_another_writer(monkeypatch, local_gate):
    from app.services.distributed_lock import LockDegradedError
    client = SimpleNamespace(set=AsyncMock(side_effect=ConnectionError("offline")))
    monkeypatch.setattr(local_gate, "_get_client", lambda: client)
    monkeypatch.setattr(local_gate, "_is_redis_configured", lambda: True)
    with pytest.raises(LockDegradedError):
        await pi_writer_gate.acquire_writer("low-session", timeout_s=0.01)


@pytest.mark.asyncio
async def test_stream_writer_loss_targets_consumer_and_aborts_rpc(turn_host, monkeypatch):
    import app.services.distributed_lock as locks
    lock = SimpleNamespace(lost=False, acquire=AsyncMock(), release=MagicMock())
    monkeypatch.setattr(locks.session_lock_registry, "lock", lambda *a, **kw: lock)
    monkeypatch.setattr(bridge, "PI_EVENT_STREAM_TIMEOUT", 10)
    monkeypatch.setattr(bridge, "PI_TURN_TOTAL_TIMEOUT", 10)
    started = asyncio.Event()
    rpc = _rpc()
    async def request(command, data=None):
        if command == "prompt":
            started.set()
        return {}
    rpc.request = AsyncMock(side_effect=request)

    # G22-P1：控制面（clear_queue/abort）经写相位 issue 拆分。
    async def _issue(command, data=None):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({})
        return SimpleNamespace(id=f"i-{command}", command=command, future=fut)

    rpc.issue = AsyncMock(side_effect=_issue)
    rpc.collect = AsyncMock(return_value={})
    async def consume():
        return [event async for event in bridge.PiBridge(rpc=rpc).stream_prompt("goal", session_id="low-session")]
    task = asyncio.create_task(consume())
    await started.wait()
    lock.lost = True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert any(call.args[0] == "abort" for call in rpc.issue.call_args_list)
    turn_id = turn_host.await_args.args[1]
    assert bridge.pop_turn_abort_source(turn_id) == "policy"
    lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_swarm_error_is_generic_and_missing_org_never_creates_mirror(monkeypatch):
    monkeypatch.setattr(bridge, "get_active_turn_entry", lambda sid: None)
    async def fail(*args, **kwargs):
        raise RuntimeError("postgres://internal:secret@host/db")
    assert await _swarm(fail).delegate_compound_task("goal") == {"delegated": False, "reason": "swarm_error"}
    run = AsyncMock(return_value=_status())
    result = await _swarm(run).delegate_compound_task("goal", mission_id="mission-low")
    assert result == {"delegated": False, "reason": "org_required"}
    run.assert_not_called()


@pytest.fixture
def turn_host(monkeypatch):
    import app.services.chat.pi_turn_context as context
    import app.services.harness_kernel.runtime as runtime
    import app.services.harness_kernel.settle as settle
    import app.services.chat.pi_turn_clarification as clarification
    monkeypatch.setattr(context, "bind_turn_prompt", AsyncMock(return_value="bound prompt"))
    monkeypatch.setattr(bridge, "register_active_pi_turn", AsyncMock())
    unregister = AsyncMock()
    monkeypatch.setattr(bridge, "unregister_active_pi_turn", unregister)
    monkeypatch.setattr(bridge.PiBridge, "_settle_turn_outcome", AsyncMock(return_value="completed"))
    monkeypatch.setattr(runtime, "get_runtime", lambda sid: SimpleNamespace(begin_turn=AsyncMock()))
    monkeypatch.setattr(settle, "run_settle_effects", AsyncMock(return_value=([], None)))
    monkeypatch.setattr(clarification, "collect_settlement_clarification", AsyncMock(return_value=None))
    monkeypatch.setattr(bridge, "PI_HEARTBEAT_INTERVAL", 0.005)
    monkeypatch.setattr(bridge, "PI_EVENT_STREAM_TIMEOUT", 0.02)
    monkeypatch.setattr(bridge, "PI_TURN_TOTAL_TIMEOUT", 0.03)
    return unregister


def _rpc(events=()):
    rpc = MagicMock(events=asyncio.Queue(), process_died_event=asyncio.Event())
    async def request(command, data=None):
        if command == "prompt":
            for event in events:
                await rpc.events.put(event)
        return {}
    rpc.request = AsyncMock(side_effect=request)
    return rpc


@pytest.mark.parametrize("failure", ["stall", "total", "death"])
@pytest.mark.asyncio
async def test_stream_failure_carries_turn_and_run_correlation(turn_host, monkeypatch, failure):
    rpc = _rpc()
    if failure == "death":
        rpc.process_died_event.set()
    elif failure == "total":
        monkeypatch.setattr(bridge, "PI_TURN_TOTAL_TIMEOUT", 0.01)
        monkeypatch.setattr(bridge, "PI_EVENT_STREAM_TIMEOUT", 1)
    frames = [frame async for frame in bridge.PiBridge(rpc=rpc).stream_prompt("goal", session_id="low-session")]
    errors = [json.loads(frame.split("data: ", 1)[1]) for frame in frames if frame.startswith("event: error")]
    assert errors and all(e["turn_id"].startswith("turn-") and e["run_id"] for e in errors)
    turn_host.assert_awaited_once()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_normal_turn_unregisters_once_preserving_empty_final_text_boundary(turn_host, streaming):
    rpc = _rpc([
        {"type": "tool_execution_end", "message": {"content": "private tool text"}},
        {"type": "agent_end", "messages": []}, {"type": "agent_settled"},
    ])
    pi = bridge.PiBridge(rpc=rpc)
    if streaming:
        frames = [frame async for frame in pi.stream_prompt("goal", session_id="low-session")]
        assert "private tool text" not in "".join(frames)
    else:
        assert (await pi.prompt("goal", session_id="low-session"))["content"] == ""
    turn_host.assert_awaited_once()


@pytest.mark.asyncio
async def test_unregister_backstop_retries_failed_inner_attempt(monkeypatch):
    unregister = AsyncMock(side_effect=[RuntimeError("first attempt failed"), None])
    monkeypatch.setattr(bridge, "unregister_active_pi_turn", unregister)
    pi = bridge.PiBridge(rpc=MagicMock())
    lease = bridge._TurnLease("turn-low", "low-session")
    await pi._safe_unregister_active_pi_turn("low-session", "turn-low", lease=lease)
    assert not lease.unregistered
    await pi._safe_unregister_active_pi_turn("low-session", "turn-low", lease=lease)
    assert lease.unregistered and unregister.await_count == 2


@pytest.mark.asyncio
async def test_unregister_backstop_retries_unconfirmed_redis_result(monkeypatch):
    unregister = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(bridge, "unregister_active_pi_turn", unregister)
    pi = bridge.PiBridge(rpc=MagicMock())
    lease = bridge._TurnLease("turn-low", "low-session")
    await pi._safe_unregister_active_pi_turn("low-session", "turn-low", lease=lease)
    assert not lease.unregistered
    await pi._safe_unregister_active_pi_turn("low-session", "turn-low", lease=lease)
    assert lease.unregistered and unregister.await_count == 2


@pytest.mark.asyncio
async def test_old_outer_cleanup_preserves_successor_abort_routing(turn_host, monkeypatch):
    old_unregister, finish_old, new_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def unregister(sid, turn_id):
        if sid == "old-session":
            old_unregister.set()
            await finish_old.wait()
        return True
    monkeypatch.setattr(bridge, "unregister_active_pi_turn", unregister)
    rpc = _rpc()
    pi = bridge.PiBridge(rpc=rpc)
    async def request(command, data=None):
        if command == "prompt" and data["sessionId"] == "new-session":
            pi._current_turn = SimpleNamespace(turn_id=pi._lock_lease.turn_id, session_id="new-session", token=None)
            new_started.set()
            await asyncio.Event().wait()
        elif command == "prompt":
            await rpc.events.put({"type": "agent_settled"})
        return {}
    rpc.request = AsyncMock(side_effect=request)
    old = asyncio.create_task(pi.prompt("old", session_id="old-session"))
    await old_unregister.wait()
    new = asyncio.create_task(pi.prompt("new", session_id="new-session"))
    try:
        await new_started.wait()
        finish_old.set()
        await old
        assert pi._active_turn_sid == "new-session"
        assert pi._current_turn.session_id == "new-session"
    finally:
        new.cancel()
        await asyncio.gather(new, return_exceptions=True)
