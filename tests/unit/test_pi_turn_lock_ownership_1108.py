"""#1108: Pi bridge turn-lock must never leak under cancellation re-delivery.

The singleton PiBridge serializes ALL sessions' turns through one
``asyncio.Lock``. Before the fix, a client disconnect during stream teardown
delivered a second ``CancelledError`` at the unprotected ``await
unregister_active_pi_turn(...)`` / ``await register_active_pi_turn(...)`` / the
stream's drain awaits — the finally's remaining statements (including
``self._lock.release()``) were skipped, and every session on the process hung
until restart.

Fix contract (invariants under test):
  INV-P1 every successful acquire is released exactly once (lease idempotence).
  INV-P2 a turn can never release another turn's acquisition (owner check).
  INV-P3 a cancelled/failing register cannot leak the lock.
  INV-P4 a cancelled/failing unregister cannot leak the lock.
  INV-P5 after a disconnect storm, an unrelated session acquires immediately.
  INV-P6 cleanup failure does not break lock ownership.
"""
import asyncio
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, _TurnLease

# stream_prompt lazily imports app.api.routes.chat inside the turn path — warm
# it at collection time (same note as tests/test_pi_bridge_lock.py).
import app.api.routes.chat  # noqa: F401


def _make_rpc() -> MagicMock:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


def _settled_feeder(rpc):
    """rpc.request('prompt') fake: emit one delta then agent_settled."""

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "X"},
            })
            await rpc.events.put({"type": "agent_settled"})

    return AsyncMock(side_effect=fake_request)


@pytest.fixture(autouse=True)
def _fast_heartbeats(monkeypatch):
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 5.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 10.0)


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    saved = (bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry)
    bridge_mod._dispatch_service = None
    bridge_mod._dispatch_service_registry = None
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry = saved


async def _drive(bridge, **kwargs):
    """Consume a full stream_prompt turn; returns the collected chunks."""
    chunks = []
    async for ev in bridge.stream_prompt(**kwargs):
        chunks.append(ev)
    return chunks


async def _assert_lock_free(bridge, timeout: float = 2.0) -> None:
    """Wait for the lock to become acquirable (the real user-visible symptom)."""
    async def _probe():
        await bridge._lock.acquire()
        bridge._lock.release()

    await asyncio.wait_for(_probe(), timeout=timeout)


# ─── INV-P4: cancellation re-delivered at the unregister await ─────────────


@pytest.mark.asyncio
async def test_stream_cancel_redisdelivered_during_unregister_releases_lock(monkeypatch):
    """The exact #1108 reproduction: a second cancellation lands while the
    finally awaits unregister (Redis eval only catches Exception)."""
    rpc = _make_rpc()
    rpc.request = _settled_feeder(rpc)
    bridge = PiBridge(rpc=rpc)

    stream_task: asyncio.Task | None = None
    unregister_hits = asyncio.Event()

    async def evil_unregister(session_id, turn_id):
        unregister_hits.set()
        # Re-deliver cancellation mid-unregister (BaseException — pi_turn_context
        # only catches Exception).
        assert stream_task is not None
        stream_task.cancel()
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", evil_unregister)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())

    stream_task = asyncio.create_task(_drive(bridge, message="m", session_id="s1"))
    await asyncio.wait_for(unregister_hits.wait(), timeout=3.0)
    # The turn reached teardown; the re-delivered cancellation either
    # propagates (task ends cancelled) or is absorbed by the shielded
    # unregister (task ends cleanly) — BOTH are acceptable; the turn already
    # completed and the lock must end up free either way.
    try:
        await stream_task
    except asyncio.CancelledError:
        pass

    await _assert_lock_free(bridge)
    assert bridge._lock_lease is None, "lease must be fully retired"


# ─── INV-P3: cancellation at the register await ────────────────────────────


@pytest.mark.asyncio
async def test_stream_cancel_during_register_releases_lock(monkeypatch):
    """#1108 twin exposure: stream register sat between acquire and the main
    try — a cancellation there leaked the lock before the fix."""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    stream_task: asyncio.Task | None = None

    async def slow_register(session_id, turn_id):
        assert stream_task is not None
        stream_task.cancel()
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", slow_register)
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    stream_task = asyncio.create_task(_drive(bridge, message="m", session_id="s2"))
    with pytest.raises(asyncio.CancelledError):
        await stream_task

    await _assert_lock_free(bridge)


# ─── register/unregister raising plain exceptions must not leak either ─────


@pytest.mark.asyncio
async def test_stream_register_crash_releases_lock(monkeypatch):
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    monkeypatch.setattr(
        bridge_mod, "register_active_pi_turn",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    with pytest.raises(RuntimeError, match="redis down"):
        await _drive(bridge, message="m", session_id="s3")
    await _assert_lock_free(bridge)


@pytest.mark.asyncio
async def test_stream_unregister_crash_releases_lock(monkeypatch):
    rpc = _make_rpc()
    rpc.request = _settled_feeder(rpc)
    bridge = PiBridge(rpc=rpc)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(
        bridge_mod, "unregister_active_pi_turn",
        AsyncMock(side_effect=RuntimeError("redis eval boom")),
    )
    # unregister failure is shielded+logged: the turn must still complete.
    chunks = await _drive(bridge, message="m", session_id="s4")
    assert any("done" in c for c in chunks)
    await _assert_lock_free(bridge)


# ─── INV-P5: unrelated session acquires right after a cancelled turn ───────


@pytest.mark.asyncio
async def test_next_session_acquires_after_cancel_storm():
    """Acceptance: after disconnects, an unrelated session's turn completes
    without waiting for the dead turn (bounded by heartbeat cadence)."""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)

    # Session A parks mid-drain (no settled event yet — rpc.request swallowed).
    async def hang_prompt(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "A"},
            })
            await asyncio.sleep(30)  # parks the drain loop

    rpc.request = AsyncMock(side_effect=hang_prompt)
    task_a = asyncio.create_task(_drive(bridge, message="A", session_id="sess-A"))
    await asyncio.sleep(0.1)
    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_a

    # Session B on the SAME singleton bridge must complete promptly.
    rpc2_events = asyncio.Queue()
    await rpc2_events.put({
        "type": "message_update",
        "message": {"role": "assistant", "content": []},
        "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "B"},
    })
    await rpc2_events.put({"type": "agent_settled"})

    async def b_prompt(cmd, data=None):
        if cmd == "prompt":
            while not rpc2_events.empty():
                await rpc.events.put(await rpc2_events.get())

    rpc.request = AsyncMock(side_effect=b_prompt)
    chunks = await asyncio.wait_for(
        _drive(bridge, message="B", session_id="sess-B"), timeout=5.0
    )
    assert any("done" in c for c in chunks)


# ─── non-streaming prompt() path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_cancel_during_unregister_releases_lock(monkeypatch):
    """prompt() had the same unprotected unregister-before-release ordering."""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    task: asyncio.Task | None = None
    unregister_hits = asyncio.Event()

    async def hang_prompt(cmd, data=None):
        if cmd == "prompt":
            await asyncio.sleep(30)

    rpc.request = AsyncMock(side_effect=hang_prompt)

    async def evil_unregister(session_id, turn_id):
        unregister_hits.set()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", evil_unregister)

    task = asyncio.create_task(bridge.prompt("m", session_id="s5"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _assert_lock_free(bridge)


@pytest.mark.asyncio
async def test_prompt_register_crash_releases_lock(monkeypatch):
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    monkeypatch.setattr(
        bridge_mod, "register_active_pi_turn",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())
    with pytest.raises(RuntimeError, match="redis down"):
        await bridge.prompt("m", session_id="s6")
    await _assert_lock_free(bridge)


# ─── INV-P1 / INV-P2: lease unit semantics ────────────────────────────────


@pytest.mark.asyncio
async def test_release_turn_lease_idempotent():
    bridge = PiBridge(rpc=_make_rpc())
    lease = await bridge._acquire_turn_lease("t1", "s")
    assert bridge._lock_lease is lease
    bridge._release_turn_lease(lease)
    assert lease.released and bridge._lock_lease is None and not bridge._lock.locked()
    # Double release must not raise nor corrupt the lock.
    bridge._release_turn_lease(lease)
    assert not bridge._lock.locked()


@pytest.mark.asyncio
async def test_stale_lease_cannot_release_new_turns_lock():
    """INV-P2: a stale lease handle from a finished turn must be refused —
    releasing it would free the CURRENT turn's acquisition."""
    bridge = PiBridge(rpc=_make_rpc())

    stale = await bridge._acquire_turn_lease("turn-old", "sess")
    bridge._release_turn_lease(stale)  # old turn finished properly

    current = await bridge._acquire_turn_lease("turn-new", "sess")
    assert bridge._lock.locked()

    # The finished turn's teardown arrives late (double-delivered finally) and
    # tries to release again — must be refused.
    bridge._release_turn_lease(stale)
    assert bridge._lock.locked(), "stale lease released the CURRENT turn's lock!"
    assert bridge._lock_lease is current

    bridge._release_turn_lease(current)
    assert not bridge._lock.locked()


def test_turn_lease_dataclass_shape():
    lease = _TurnLease("t", "s")
    assert lease.released is False
    assert lease.turn_id == "t" and lease.session_id == "s"


# ─── Acceptance: randomized cancellation storm (≥100 timings) ──────────────


@pytest.mark.asyncio
async def test_randomized_cancel_storm_never_leaks_lock(monkeypatch):
    """Issue acceptance: ≥100 random-timing disconnects; after each, the lock
    must return to free and an unrelated session must be able to run."""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    rng = random.Random(1108)
    storms = 120
    for i in range(storms):
        # Fresh turn parks mid-drain (prompt swallow + delta, no settled).
        async def hang_prompt(cmd, data=None, _i=i):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {
                        "type": "text_delta", "contentIndex": 0, "delta": f"i{_i}",
                    },
                })
                await asyncio.sleep(30)

        rpc.request = AsyncMock(side_effect=hang_prompt)
        task = asyncio.create_task(
            _drive(bridge, message=f"m{i}", session_id=f"sess-{i % 3}")
        )
        # Cancel at a random point across the whole turn lifecycle:
        # keepalive wait, register, send, event pump, teardown.
        await asyncio.sleep(rng.uniform(0.0, 0.08))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _assert_lock_free(bridge, timeout=2.0)

    # After the storm, a fresh session completes a full turn.
    rpc.request = _settled_feeder(rpc)
    chunks = await asyncio.wait_for(
        _drive(bridge, message="final", session_id="sess-final"), timeout=5.0
    )
    assert any("done" in c for c in chunks)
    assert not bridge._lock.locked()
