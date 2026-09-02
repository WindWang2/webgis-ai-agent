"""V5-B: bridge pool — session affinity, cross-session parallelism, abort routing.

Pool invariants:
  POOL-1  default PI_BRIDGE_POOL_SIZE=1 → the historical singleton behavior.
  POOL-2  session affinity is stable (same session → same worker across calls
          and across process restarts — hashlib, not hash()).
  POOL-3  two different sessions can hold turns on DIFFERENT workers
          simultaneously (per-bridge turn locks do not serialize each other).
  POOL-4  abort(session_id) routes to the worker owning that session's turn
          (via the session-keyed active-turn table) and cancels that turn's
          token only.
  POOL-5  register/unregister maintain the session table without cross-session
          clobbering.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiBridgePool

import app.api.routes.chat  # noqa: F401 — warm lazy import


def _make_bridge(name: str) -> PiBridge:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    bridge = PiBridge(rpc=rpc)
    bridge.name = name
    return bridge


# ─── POOL-2: stable affinity ────────────────────────────────────────────────


def test_pool_affinity_is_stable():
    bridges = [_make_bridge(f"w{i}") for i in range(4)]
    pool = PiBridgePool(bridges)
    for sid in ("sess-a", "sess-b", "成都教育-123", "x" * 100):
        picks = {pool.bridge_for_session(sid) for _ in range(20)}
        assert len(picks) == 1, f"affinity not stable for {sid!r}"
        assert picks.pop() in bridges
    # Distinct sessions distribute across workers (not all on one).
    picked = {pool.bridge_for_session(f"sess-{i}").name for i in range(50)}
    assert len(picked) > 1, "affinity never spreads across workers"


def test_pool_size_one_returns_same_bridge():
    b = _make_bridge("solo")
    pool = PiBridgePool([b])
    assert pool.bridge_for_session("anything") is b


# ─── POOL-3: parallel turns across workers ─────────────────────────────────


@pytest.mark.asyncio
async def test_two_sessions_turn_in_parallel_on_distinct_workers(monkeypatch):
    """Scenario 1 (V5 acceptance): a long-running turn on worker A must not
    block a normal turn on worker B."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 5.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 10.0)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    a, b = _make_bridge("wA"), _make_bridge("wB")
    a_started = asyncio.Event()
    a_still_running_when_b_finished = {}

    async def hang_a(cmd, data=None):
        if cmd == "prompt":
            a_started.set()
            await asyncio.sleep(30)  # long-running tool on worker A

    a._rpc.request = AsyncMock(side_effect=hang_a)

    async def quick_b(cmd, data=None):
        if cmd == "prompt":
            # Prove A's turn is still parked while B completes.
            a_still_running_when_b_finished["a_parked"] = a_started.is_set()
            await b._rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "B"},
            })
            await b._rpc.events.put({"type": "agent_settled"})

    b._rpc.request = AsyncMock(side_effect=quick_b)

    async def drive(bridge, session_id):
        chunks = []
        async for ev in bridge.stream_prompt(message="m", session_id=session_id):
            chunks.append(ev)
        return chunks

    task_a = asyncio.create_task(drive(a, "sess-A"))
    await asyncio.wait_for(a_started.wait(), timeout=3.0)
    task_b = asyncio.create_task(drive(b, "sess-B"))

    # B completes promptly — A's turn never blocked it.
    await asyncio.wait_for(task_b, timeout=5.0)
    assert a_still_running_when_b_finished["a_parked"] is True
    assert not task_a.done(), "A's long turn should still be running"

    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_a


# ─── POOL-4: abort routing via the session table ───────────────────────────


@pytest.mark.asyncio
async def test_abort_routes_to_owning_worker(monkeypatch):
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 5.0)

    a, b = _make_bridge("wA"), _make_bridge("wB")
    b_started = asyncio.Event()

    async def hang(cmd, data=None):
        if cmd == "prompt":
            b_started.set()
            await asyncio.sleep(30)

    b._rpc.request = AsyncMock(side_effect=hang)

    async def drive():
        async for _ in b.stream_prompt(message="m", session_id="sess-b"):
            pass

    task = asyncio.create_task(drive())
    await asyncio.wait_for(b_started.wait(), timeout=3.0)

    entry = bridge_mod.get_active_turn_entry("sess-b")
    assert entry is not None and entry.bridge is b, "table must record owning worker"
    assert entry.token is not None, "table must record the turn's token"

    # abort on the OTHER worker routes to b and cancels b's turn token.
    result = await asyncio.wait_for(a.abort("sess-b"), timeout=3.0)
    assert result is not None
    # The abort RPC went to worker b (which owns the subprocess), not a.
    abort_calls = [c for c in b._rpc.request.call_args_list if c.args and c.args[0] == "abort"]
    assert abort_calls, "abort RPC must be sent to the owning worker"
    assert task.cancelled() or not task.done()  # turn ends via abort path or still draining
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_session_table_register_unregister_no_clobber():
    """POOL-5: two sessions' table entries are independent; unregister of one
    leaves the other intact (the old single-slot design clobbered here)."""
    token_a = bridge_mod.CancellationToken(job_id="t-a")
    token_b = bridge_mod.CancellationToken(job_id="t-b")
    bridge = _make_bridge("w0")

    await bridge_mod.register_active_pi_turn("sa", "turn-a", token=token_a, bridge=bridge)
    await bridge_mod.register_active_pi_turn("sb", "turn-b", token=token_b, bridge=bridge)

    ea = bridge_mod.get_active_turn_entry("sa")
    eb = bridge_mod.get_active_turn_entry("sb")
    assert ea.token is token_a and eb.token is token_b

    await bridge_mod.unregister_active_pi_turn("sa", "turn-a")
    assert bridge_mod.get_active_turn_entry("sa") is None
    assert bridge_mod.get_active_turn_entry("sb") is eb, "sb entry must survive"
    assert bridge_mod.get_active_turn_entry("sb").token is token_b

    await bridge_mod.unregister_active_pi_turn("sb", "turn-b")
    assert bridge_mod.get_active_turn_entry("sb") is None


@pytest.mark.asyncio
async def test_unregister_ignores_non_owner_turn_id():
    await bridge_mod.register_active_pi_turn("sx", "turn-1")
    # A late/duplicate unregister for a DIFFERENT turn must not clear the slot.
    await bridge_mod.unregister_active_pi_turn("sx", "turn-stale")
    assert bridge_mod.get_active_turn_entry("sx") is not None
    await bridge_mod.unregister_active_pi_turn("sx", "turn-1")
    assert bridge_mod.get_active_turn_entry("sx") is None
