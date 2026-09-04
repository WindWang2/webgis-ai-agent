"""V5-B-3: production pool routing — the turn-acquisition seam under a pool.

These tests exercise the PRODUCTION acquisition path (``chat._pi_turn_bridge``
/ ``get_pi_bridge(session_id=...)`` resolution + the routed
``prompt``/``stream_prompt``/``abort`` lifecycle), not just the pool data
structure. Invariants (ADR-0093 V5-B + this task's P1–P10):

  C1  same-session serialization — turn B waits for turn A on the same worker.
  C2  cross-session parallelism — PROVEN by overlapping execution intervals,
      not by comparing worker ids.
  C3  deterministic affinity across N acquisitions through the prod seam.
  C4  abort routes to the owner worker only; other sessions' turns untouched.
  C5  disconnect storm (>=100 random connect/start/disconnect/cancel cycles)
      leaves the active-turn table empty, every lease released, and an
      unrelated session serviceable.
  C6  cancellation during register/unregister Redis I/O leaks no lease/table
      entry.
  C7  worker crash mid-turn fails promptly, releases the lease, and the next
      turn on that worker works.
  P8  pool size 1 routes every session to the singleton reference.
"""
import asyncio
import random
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import (
    PiBridge,
    PiBridgePool,
    get_active_turn_entry,
    get_pi_bridge,
)

import app.api.routes.chat as chat_mod


# ─── fixtures ───────────────────────────────────────────────────────────────


class FakeRpc:
    """Minimal PiRpcClient stand-in with the surface the bridge touches.

    ``script`` is an async fn ``(cmd, data) -> None`` invoked per RPC; it can
    push events onto ``events`` to advance a scripted turn.
    """

    def __init__(self) -> None:
        self.events = asyncio.Queue()
        self.process_died = False
        self.process_died_event = asyncio.Event()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self.script = None
        self.abort_rpc_count = 0

    def is_alive(self) -> bool:
        return not self.process_died

    async def start(self) -> None:
        self.process_died = False
        self.process_died_event.clear()

    async def stop(self) -> None:
        pass

    async def request(self, cmd: str, data=None):
        if cmd == "abort":
            self.abort_rpc_count += 1
        if self.process_died:
            raise bridge_mod.PiRpcError("Pi process not started")
        rid = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            if self.script is not None:
                await self.script(cmd, data)
            if cmd == "abort":
                return {}
            if not fut.done():
                fut.set_result({})
            return await fut
        finally:
            self._pending.pop(rid, None)

    def pending_request_ids(self) -> set:
        return set(self._pending.keys())

    def fail_pending_ids(self, request_ids, reason: str) -> int:
        failed = 0
        for rid in request_ids:
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_exception(bridge_mod.PiRpcError(reason))
                failed += 1
        return failed

    def die(self) -> None:
        """Simulate subprocess crash: flip flags + fail pending futures."""
        self.process_died = True
        self.process_died_event.set()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(bridge_mod.PiRpcError("process died"))
        self._pending.clear()


def _make_bridge(name: str) -> PiBridge:
    rpc = FakeRpc()
    bridge = PiBridge(rpc=rpc)
    bridge.name = name
    return bridge


def _settle_event() -> dict:
    return {"type": "agent_settled"}


def _text_event(text: str) -> dict:
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": []},
        "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text},
    }


@pytest.fixture
def clean_turn_state(monkeypatch):
    """Isolate module turn/pool state + pin fast timeouts + no-op Redis I/O."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 3.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 10.0)
    # Redis-backed registration is best-effort; pin it to local-only so the
    # table is the observable truth in these tests. register/unregister import
    # the registry object lazily, so patching its methods is sufficient.
    from app.services.chat import pi_turn_context as _ptc

    monkeypatch.setattr(_ptc.pi_turn_registry, "register_turn", AsyncMock())
    monkeypatch.setattr(_ptc.pi_turn_registry, "unregister_turn", AsyncMock())
    monkeypatch.setattr(
        _ptc.pi_turn_registry, "is_active", AsyncMock(return_value=False)
    )
    yield
    bridge_mod._active_turns.clear()
    bridge_mod._active_turn_token = None
    bridge_mod._active_turn_turn_id = None
    bridge_mod._active_turn_run_id = None
    bridge_mod._active_turn_session_id = None
    bridge_mod._active_turn_context = None
    bridge_mod._bridge_pool = None
    bridge_mod._pi_bridge = None


@pytest.fixture
def pool2(clean_turn_state, monkeypatch):
    """A 2-worker pool wired through the production seam (chat.pi_bridge)."""
    bridges = [_make_bridge("w0"), _make_bridge("w1")]
    pool = PiBridgePool(bridges)
    monkeypatch.setattr(bridge_mod, "_bridge_pool", pool)
    monkeypatch.setattr(bridge_mod, "_pi_bridge", bridges[0])
    saved = chat_mod.pi_bridge
    chat_mod.pi_bridge = bridges[0]
    yield pool
    chat_mod.pi_bridge = saved


async def _drive_stream(bridge: PiBridge, session_id: str, on_event=None):
    """Consume a stream_prompt turn to completion, returning its SSE chunks."""

    async def run():
        chunks = []
        try:
            async for ev in bridge.stream_prompt(message="m", session_id=session_id):
                chunks.append(ev)
                if on_event is not None:
                    on_event(ev)
        except asyncio.CancelledError:
            raise
        return chunks

    return await run()


# ─── C3 + P8: production routing seam ──────────────────────────────────────


async def test_c3_production_seam_deterministic_affinity(pool2):
    """N acquisitions of one session through the PRODUCTION seam (chat helper
    AND get_pi_bridge) always resolve to the same worker."""
    sid = "sess-prod"
    picks = set()
    for _ in range(10):
        picks.add(id(chat_mod._pi_turn_bridge(sid)))
        picks.add(id(await get_pi_bridge(session_id=sid)))
    assert len(picks) == 1, "affinity must be stable across acquisitions"
    # and it is one of the pool's workers
    assert picks.pop() in {id(b) for b in pool2.bridges}


def test_p8_pool_size_one_routes_to_singleton(clean_turn_state, monkeypatch):
    """P8: pool size 1 keeps the byte-identical singleton reference."""
    b = _make_bridge("solo")
    pool = PiBridgePool([b])
    monkeypatch.setattr(bridge_mod, "_bridge_pool", pool)
    monkeypatch.setattr(bridge_mod, "_pi_bridge", b)
    saved = chat_mod.pi_bridge
    chat_mod.pi_bridge = b
    try:
        for sid in ("a", "b", "成都小学", ""):
            assert chat_mod._pi_turn_bridge(sid) is b
    finally:
        chat_mod.pi_bridge = saved


def test_no_pool_falls_back_to_injected_reference(clean_turn_state):
    """Test seam compat: with no pool, the injected chat.pi_bridge is used."""
    mock = MagicMock()
    saved = chat_mod.pi_bridge
    chat_mod.pi_bridge = mock
    try:
        assert chat_mod._pi_turn_bridge("any-session") is mock
    finally:
        chat_mod.pi_bridge = saved


# ─── C1: same-session serialization ────────────────────────────────────────


async def test_c1_same_session_turns_serialize(pool2):
    """Turn B for the same session must not start until turn A completes."""
    sid = "sess-serial"
    bridge = chat_mod._pi_turn_bridge(sid)
    assert isinstance(bridge._rpc, FakeRpc)
    turn_bounds: dict[str, tuple[float, float]] = {}

    async def script(cmd, data=None):
        if cmd != "prompt":
            return
        which = "A" if "A" not in turn_bounds or "B" in turn_bounds else "A"
        # Tag by order: first prompt seen = A, second = B.
        if len(turn_bounds) == 0:
            which = "A"
        elif "B" not in turn_bounds:
            which = "B"
        else:
            which = "C"
        t0 = time.monotonic()
        turn_bounds[which] = (t0, t0)  # provisional end; updated below
        await bridge._rpc.events.put(_text_event(which))
        await asyncio.sleep(0.15)  # A runs long enough to overlap a rogue B
        t1 = time.monotonic()
        turn_bounds[which] = (t0, t1)
        await bridge._rpc.events.put(_settle_event())

    bridge._rpc.script = script

    task_a = asyncio.create_task(_drive_stream(bridge, sid))
    await asyncio.sleep(0.03)  # A is now mid-turn
    task_b = asyncio.create_task(_drive_stream(bridge, sid))
    await asyncio.wait_for(task_b, timeout=5.0)
    await asyncio.wait_for(task_a, timeout=5.0)

    assert "A" in turn_bounds and "B" in turn_bounds
    a0, a1 = turn_bounds["A"]
    b0, b1 = turn_bounds["B"]
    assert b0 >= a1 - 0.05, (
        f"same-session turn B (start {b0:.3f}) must wait for A (end {a1:.3f})"
    )


# ─── C2: cross-session parallelism, PROVEN by interval overlap ─────────────


async def test_c2_cross_session_parallelism_proven_by_overlap(pool2):
    """Two sessions' scripted turns must have OVERLAPPING execution intervals
    through the production seam — worker-id inequality alone proves nothing."""
    sid_a, sid_b = "sess-par-a", "sess-par-b"
    bridge_a = chat_mod._pi_turn_bridge(sid_a)
    bridge_b = chat_mod._pi_turn_bridge(sid_b)
    assert bridge_a is not bridge_b, "fixture pool must spread these sessions"
    assert isinstance(bridge_a._rpc, FakeRpc) and isinstance(bridge_b._rpc, FakeRpc)

    intervals: dict[str, tuple[float, float]] = {}
    both_inside = asyncio.Event()

    async def make_script(tag, rpc, other_tag):
        async def script(cmd, data=None):
            if cmd != "prompt":
                return
            t0 = time.monotonic()
            intervals.setdefault(tag, (t0, t0))
            # Park mid-turn until the OTHER session's turn is also mid-turn:
            # only true parallelism lets both parking windows coexist.
            for _ in range(500):
                if other_tag in intervals:
                    both_inside.set()
                    break
                await asyncio.sleep(0.002)
            await rpc.events.put(_text_event(tag))
            await rpc.events.put(_settle_event())
            t1 = time.monotonic()
            intervals[tag] = (t0, t1)

        return script

    bridge_a._rpc.script = await make_script("A", bridge_a._rpc, "B")
    bridge_b._rpc.script = await make_script("B", bridge_b._rpc, "A")

    task_a = asyncio.create_task(_drive_stream(bridge_a, sid_a))
    task_b = asyncio.create_task(_drive_stream(bridge_b, sid_b))

    # Both turns were simultaneously mid-flight (the barrier resolved).
    await asyncio.wait_for(both_inside.wait(), timeout=5.0)
    await asyncio.wait_for(task_a, timeout=5.0)
    await asyncio.wait_for(task_b, timeout=5.0)

    a0, a1 = intervals["A"]
    b0, b1 = intervals["B"]
    overlap = min(a1, b1) - max(a0, b0)
    assert overlap > 0, (
        f"execution intervals must overlap for real parallelism, got "
        f"A=[{a0:.3f},{a1:.3f}] B=[{b0:.3f},{b1:.3f}]"
    )


# ─── C4: abort routes to the owner worker only ─────────────────────────────


async def test_c4_abort_routed_to_owner_worker_other_session_unharmed(pool2):
    sid_victim, sid_bystander = "sess-abort-victim", "sess-abort-bystander"
    b_victim = chat_mod._pi_turn_bridge(sid_victim)
    b_bystander = chat_mod._pi_turn_bridge(sid_bystander)
    assert b_victim is not b_bystander
    assert isinstance(b_victim._rpc, FakeRpc) and isinstance(b_bystander._rpc, FakeRpc)

    victim_prompt_started = asyncio.Event()

    async def victim_script(cmd, data=None):
        if cmd == "prompt":
            victim_prompt_started.set()
            await asyncio.sleep(10)  # parks until aborted/cancelled

    b_victim._rpc.script = victim_script

    async def bystander_script(cmd, data=None):
        if cmd == "prompt":
            await b_bystander._rpc.events.put(_text_event("ok"))
            await b_bystander._rpc.events.put(_settle_event())

    b_bystander._rpc.script = bystander_script

    task_victim = asyncio.create_task(_drive_stream(b_victim, sid_victim))
    await asyncio.wait_for(victim_prompt_started.wait(), timeout=5.0)

    # Bystander turn runs to completion WHILE the victim is parked.
    task_bystander = asyncio.create_task(_drive_stream(b_bystander, sid_bystander))
    await asyncio.wait_for(task_bystander, timeout=5.0)

    # Abort from the NON-owning worker routes via the active-turn table.
    result = await asyncio.wait_for(b_bystander.abort(sid_victim), timeout=5.0)
    assert result is not None

    # The abort RPC hit the victim's worker, never the bystander's.
    assert b_victim._rpc.abort_rpc_count >= 1, (
        "abort RPC must be delivered to the owning (victim) worker"
    )
    assert b_bystander._rpc.abort_rpc_count == 0, (
        "abort must never reach the bystander session's worker"
    )

    task_victim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task_victim, timeout=5.0)

    # No turn state leaked for either session.
    await asyncio.sleep(0.05)
    assert get_active_turn_entry(sid_victim) is None
    assert get_active_turn_entry(sid_bystander) is None
    assert not b_victim._lock.locked()
    assert not b_bystander._lock.locked()


# ─── C5: disconnect storm ──────────────────────────────────────────────────


async def test_c5_disconnect_storm_100_cycles(pool2):
    """>=100 randomized connect/start/disconnect/cancel/reconnect cycles."""
    rng = random.Random(20260903)
    sessions = [f"storm-s{i}" for i in range(6)]
    outcomes = {"completed": 0, "cancelled": 0, "failed": 0}

    # Each worker's script: sometimes settle quickly, sometimes park long.
    for w in pool2.bridges:
        assert isinstance(w._rpc, FakeRpc)
        parked: dict[int, asyncio.Event] = {}

        async def script(cmd, data=None, _rpc=w._rpc, _parked=parked):
            if cmd != "prompt":
                return
            mode = rng.random()
            if mode < 0.45:
                # quick scripted turn
                await _rpc.events.put(_text_event("x"))
                await _rpc.events.put(_settle_event())
            else:
                # long turn: parks until aborted/cancelled or timeout
                await asyncio.sleep(2.0)
                await _rpc.events.put(_settle_event())

        w._rpc.script = script

    async def one_cycle(i: int):
        sid = rng.choice(sessions)
        bridge = chat_mod._pi_turn_bridge(sid)
        task = asyncio.create_task(_drive_stream(bridge, sid))
        await asyncio.sleep(rng.uniform(0.002, 0.08))
        action = rng.random()
        if action < 0.55:
            # disconnect: cancel the consuming task (GeneratorExit path)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
                outcomes["cancelled"] += 1
            except asyncio.CancelledError:
                outcomes["cancelled"] += 1
            except Exception:
                outcomes["failed"] += 1
        else:
            # let it run (quick script settles; long one is aborted below)
            if action < 0.8:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                    outcomes["completed"] += 1
                except asyncio.CancelledError:
                    outcomes["cancelled"] += 1
                except Exception:
                    outcomes["failed"] += 1
            else:
                # explicit abort mid-turn (routes via the session table)
                try:
                    await asyncio.wait_for(bridge.abort(sid), timeout=5.0)
                except Exception:
                    pass
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                    outcomes["cancelled"] += 1
                except asyncio.CancelledError:
                    outcomes["cancelled"] += 1
                except Exception:
                    outcomes["failed"] += 1

    for cycle in range(100):
        await one_cycle(cycle)
        # Random reconnect pressure: a fresh turn on a random session mid-storm.
        if rng.random() < 0.15:
            await one_cycle(cycle + 1000)

    # Let any pending abort-driven unwinds settle.
    await asyncio.sleep(0.15)

    # Active-turn table is EMPTY; every lease is FREE.
    assert not bridge_mod._active_turns, (
        f"active turn ghosts after storm: {sorted(bridge_mod._active_turns)}"
    )
    for w in pool2.bridges:
        assert not w._lock.locked(), f"lease leaked on worker {getattr(w, 'name', '?')}"
        # Residual queue events are acceptable ONLY as the turn-terminator
        # the next turn's stale-drain will drop; anything else is pollution.
        leftover = list(w._rpc.events._queue)
        assert all(
            isinstance(e, dict) and e.get("type") == "agent_settled"
            for e in leftover
        ), f"non-terminator events left on worker queue: {leftover!r}"

    # An unrelated session can still run a full turn afterwards.
    sid_fresh = "storm-after-fresh"
    bridge = chat_mod._pi_turn_bridge(sid_fresh)
    assert isinstance(bridge._rpc, FakeRpc)

    async def fresh_script(cmd, data=None):
        if cmd == "prompt":
            await bridge._rpc.events.put(_text_event("fresh"))
            await bridge._rpc.events.put(_settle_event())

    bridge._rpc.script = fresh_script
    chunks = await asyncio.wait_for(_drive_stream(bridge, sid_fresh), timeout=5.0)
    assert any("done" in c for c in chunks)
    assert outcomes["completed"] + outcomes["cancelled"] + outcomes["failed"] >= 100


# ─── C6: cancellation during register/unregister Redis I/O ─────────────────


async def test_c6_cancel_during_register_redis_io(pool2, monkeypatch):
    """A cancellation delivered while register_active_pi_turn is awaiting its
    Redis write must not leak the lease or a table entry (INV-P3).

    The hang is placed on the REDIS registry method — the real suspension
    point. ``register_active_pi_turn`` writes the local table entry BEFORE
    that await, so a wrapper-level hang (pre-registered) would prove the
    wrong window: the ghost entry exists exactly when the cancel lands.
    """
    sid = "sess-reg-cancel"
    bridge = chat_mod._pi_turn_bridge(sid)
    assert isinstance(bridge._rpc, FakeRpc)
    register_hang = asyncio.Event()
    release_hang = asyncio.Event()

    from app.services.chat import pi_turn_context as _ptc

    async def hanging_redis_register(session_id, turn_id):
        register_hang.set()
        await release_hang.wait()  # simulates stuck Redis I/O

    monkeypatch.setattr(
        _ptc.pi_turn_registry, "register_turn", hanging_redis_register
    )

    async def script(cmd, data=None):
        pass

    bridge._rpc.script = script

    task = asyncio.create_task(_drive_stream(bridge, sid))
    await asyncio.wait_for(register_hang.wait(), timeout=5.0)
    # Cancel while the turn parks inside register's Redis await — the local
    # table entry is already written at this point.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
    release_hang.set()
    await asyncio.sleep(0.05)

    assert not bridge._lock.locked(), "register-cancel leaked the turn lease"
    assert get_active_turn_entry(sid) is None, (
        "register-cancel leaked a ghost active-turn entry (INV-P3)"
    )


async def test_c6_cancel_during_unregister_redis_io(pool2, monkeypatch):
    """A cancellation re-delivered while unregister awaits Redis I/O must not
    leak the lease (INV-P4: sync release runs before the shielded await)."""
    sid = "sess-unreg-cancel"
    bridge = chat_mod._pi_turn_bridge(sid)
    assert isinstance(bridge._rpc, FakeRpc)
    unreg_hang = asyncio.Event()

    async def hanging_unregister(session_id, turn_id):
        unreg_hang.set()
        await asyncio.sleep(3600)  # stuck Redis eval; shielded+budgeted inside

    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", hanging_unregister)

    async def script(cmd, data=None):
        if cmd == "prompt":
            await bridge._rpc.events.put(_settle_event())

    bridge._rpc.script = script

    task = asyncio.create_task(_drive_stream(bridge, sid))
    # Turn completed normally (agent_settled); teardown parks in unregister.
    await asyncio.wait_for(unreg_hang.wait(), timeout=5.0)
    task.cancel()
    # Either outcome is correct INV-P4 behavior: the cancellation may
    # propagate, or be swallowed by the shielded-budget unregister so the
    # REST of teardown still runs (then the task completes normally).
    try:
        await asyncio.wait_for(task, timeout=8.0)
    except asyncio.CancelledError:
        pass

    assert not bridge._lock.locked(), "unregister-cancel leaked the turn lease"
    # Table entry: the local pop happens inside unregister (which is hung);
    # the lease backstop guarantees the LOCK is free so the next turn runs.
    # Prove serviceability with a follow-up turn on the same worker session.
    monkeypatch.setattr(
        bridge_mod, "unregister_active_pi_turn", AsyncMock()
    )
    bridge._rpc.script = script
    chunks = await asyncio.wait_for(
        _drive_stream(bridge, sid + "-next"), timeout=5.0
    )
    assert any("done" in c for c in chunks)


# ─── C7: worker crash mid-turn ─────────────────────────────────────────────


async def test_c7_worker_crash_mid_turn_recovers(pool2):
    """Killing the worker's subprocess mid-turn fails the turn promptly,
    releases the lease, and the next turn on that worker succeeds."""
    sid = "sess-crash"
    bridge = chat_mod._pi_turn_bridge(sid)
    assert isinstance(bridge._rpc, FakeRpc)
    mid_turn = asyncio.Event()

    async def script(cmd, data=None):
        if cmd == "prompt":
            # Answer the prompt RPC immediately; the turn is now streaming
            # (parked on the event queue) when mid_turn fires.
            mid_turn.set()

    bridge._rpc.script = script

    t0 = time.monotonic()
    task = asyncio.create_task(_drive_stream(bridge, sid))
    await asyncio.wait_for(mid_turn.wait(), timeout=5.0)
    # Let the pump park on {events.get(), death.wait()} before the crash.
    await asyncio.sleep(0.05)

    bridge._rpc.die()  # subprocess crash: fails pending + sets death event

    chunks = await asyncio.wait_for(task, timeout=5.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 4.0, "crashed turn must exit promptly, not park on stall budget"
    assert any("error" in c for c in chunks), "crash must surface an error event"
    assert any("done" in c for c in chunks), "stream still terminates with done"

    await asyncio.sleep(0.05)
    assert get_active_turn_entry(sid) is None, "table entry leaked after crash"
    assert not bridge._lock.locked(), "lease leaked after crash"

    # Next turn on the SAME worker works (lazy respawn semantics: the fake
    # rpc's start() clears the death flags — production respawn_if_dead).
    bridge._rpc.process_died = False
    bridge._rpc.process_died_event.clear()

    async def next_script(cmd, data=None):
        if cmd == "prompt":
            await bridge._rpc.events.put(_text_event("after-crash"))
            await bridge._rpc.events.put(_settle_event())

    bridge._rpc.script = next_script
    chunks2 = await asyncio.wait_for(
        _drive_stream(bridge, sid + "-post"), timeout=5.0
    )
    assert any("done" in c for c in chunks2)
    assert not bridge._lock.locked()


# ─── PR 1133 Remediations: Regression Tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_tool_callbacks_without_redis(pool2, monkeypatch):
    """[CRITICAL 1] Concurrent turns across pool workers without Redis must both
    be recognized as active by is_active_pi_turn, preventing 409 Conflict.
    """
    from app.agent_pi_bridge import (
        is_active_pi_turn,
        register_active_pi_turn,
        unregister_active_pi_turn,
    )
    from app.services.chat.pi_turn_context import pi_turn_registry

    # Ensure unpatched / clean local registry
    pi_turn_registry._local_context = {}

    w0, w1 = pool2.bridges[0], pool2.bridges[1]
    sid_a, tid_a = "session-concurrent-a", "turn-a-1"
    sid_b, tid_b = "session-concurrent-b", "turn-b-2"

    await register_active_pi_turn(sid_a, tid_a, bridge=w0)
    await register_active_pi_turn(sid_b, tid_b, bridge=w1)

    # Both concurrent sessions MUST be recognized as active simultaneously
    assert await is_active_pi_turn(sid_a, tid_a) is True
    assert await is_active_pi_turn(sid_b, tid_b) is True

    # Cross-turn / mismatched validations must reject
    assert await is_active_pi_turn(sid_a, tid_b) is False
    assert await is_active_pi_turn(sid_b, tid_a) is False
    assert await is_active_pi_turn("unknown-session", tid_a) is False

    # Unregistering session A does not invalidate session B
    await unregister_active_pi_turn(sid_a, tid_a)
    assert await is_active_pi_turn(sid_a, tid_a) is False
    assert await is_active_pi_turn(sid_b, tid_b) is True

    # Unregistering session B clears session B
    await unregister_active_pi_turn(sid_b, tid_b)
    assert await is_active_pi_turn(sid_b, tid_b) is False

    # Verify fallback to real PiTurnRegistry when session is not in local _active_turns
    monkeypatch.setattr(
        pi_turn_registry,
        "is_active",
        pi_turn_registry.__class__.is_active.__get__(pi_turn_registry),
    )
    pi_turn_registry._local_context = {"remote-session": "remote-turn"}
    assert await is_active_pi_turn("remote-session", "remote-turn") is True
    assert await is_active_pi_turn("remote-session", "wrong-turn") is False
    pi_turn_registry._local_context.clear()


@pytest.mark.asyncio
async def test_stream_prompt_respawn_after_registration_cancellation(monkeypatch):
    """[CRITICAL 2] stream_prompt cancellation during registration must clear
    _active_turn_sid and clean up TURN_EVIDENCE so dead worker can respawn.
    """
    from app.lib.runtime.evidence import TURN_EVIDENCE

    bridge = _make_bridge("w0")

    async def _failing_register(*args, **kwargs):
        # Trigger cancellation while in registration
        raise asyncio.CancelledError("registration cancelled")

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", _failing_register)

    sid = "sess-stream-reg-cancel"
    with pytest.raises(asyncio.CancelledError):
        await _drive_stream(bridge, sid)

    # Invariants: no leak of active turn sid, lock released, table entry cleared, evidence cleared
    assert bridge._active_turn_sid is None, "_active_turn_sid leaked on cancellation!"
    assert not bridge._lock.locked(), "turn lease locked on cancellation!"
    assert get_active_turn_entry(sid) is None, "active turn entry leaked!"
    assert not any(getattr(ev, "session_id", None) == sid for ev in TURN_EVIDENCE._entries.values()), (
        "TURN_EVIDENCE leaked for cancelled stream_prompt turn!"
    )

    # Subprocess dies subsequently
    bridge._rpc.die()
    assert bridge.is_alive() is False

    # Worker MUST be able to respawn (not blocked by _active_turn_sid)
    respawned = await bridge.respawn_if_dead()
    assert respawned is True, "respawn_if_dead failed after registration cancellation!"
    assert bridge.is_alive() is True


@pytest.mark.asyncio
async def test_prompt_outer_finally_cleanup_on_cancellation(monkeypatch):
    """[MAJOR 3] prompt() outer finally backstop clears _active_turn_sid,
    releases lease, and unregisters active turn on cancellation.
    """
    from app.lib.runtime.evidence import TURN_EVIDENCE

    bridge = _make_bridge("w0")

    async def _failing_register(*args, **kwargs):
        raise asyncio.CancelledError("prompt registration cancelled")

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", _failing_register)

    sid = "sess-prompt-reg-cancel"
    with pytest.raises(asyncio.CancelledError):
        await bridge.prompt("test prompt message", session_id=sid)

    assert bridge._active_turn_sid is None, "_active_turn_sid leaked on prompt cancellation!"
    assert not bridge._lock.locked(), "turn lease locked on prompt cancellation!"
    assert get_active_turn_entry(sid) is None, "active turn entry leaked!"
    assert not any(getattr(ev, "session_id", None) == sid for ev in TURN_EVIDENCE._entries.values()), (
        "TURN_EVIDENCE leaked for cancelled prompt turn!"
    )

    # Process death and respawn
    bridge._rpc.die()
    assert bridge.is_alive() is False
    respawned = await bridge.respawn_if_dead()
    assert respawned is True, "respawn_if_dead failed after prompt cancellation!"
    assert bridge.is_alive() is True


@pytest.mark.asyncio
async def test_chat_completions_affinity_minting_for_none_session(pool2, monkeypatch):
    """[MAJOR 4] chat_completions mints an affinity session ID for session_id=None
    requests so they distribute across the pool instead of starving worker 0.
    """
    from unittest.mock import AsyncMock
    from app.api.routes.chat import chat_completions, ChatRequest

    monkeypatch.setattr(chat_mod, "USE_NEW_AGENT", True)
    monkeypatch.setattr(
        chat_mod, "_record_frontend_cartographic_observation", AsyncMock()
    )
    monkeypatch.setattr(
        chat_mod, "_build_cartography_turn_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        chat_mod, "_build_environment_turn_context", MagicMock(return_value="")
    )
    monkeypatch.setattr(chat_mod, "harvest_project_memory", AsyncMock())

    # Mock DB context manager to avoid real DB I/O
    class FakeAsyncDbSession:
        async def __aenter__(self):
            db = MagicMock()
            db.get = AsyncMock(return_value=None)
            return db

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr(chat_mod, "async_db_session", FakeAsyncDbSession)

    w0, w1 = pool2.bridges[0], pool2.bridges[1]
    prompt_calls = {w0.name: [], w1.name: []}

    async def _mock_prompt_w0(msg, session_id=None, **kwargs):
        prompt_calls[w0.name].append(session_id)
        return {"sessionId": session_id, "content": "reply from w0"}

    async def _mock_prompt_w1(msg, session_id=None, **kwargs):
        prompt_calls[w1.name].append(session_id)
        return {"sessionId": session_id, "content": "reply from w1"}

    w0.prompt = _mock_prompt_w0
    w1.prompt = _mock_prompt_w1

    responses = []
    # Dispatch 20 anonymous turns; with UUID affinity they must hit both workers
    for i in range(20):
        req = ChatRequest(message=f"msg {i}", session_id=None)
        resp = await chat_completions(
            req, request=MagicMock(), _user={}, owner_token=None, db=None
        )
        responses.append(resp)

    # Every response got a non-empty minted session ID
    for resp in responses:
        assert resp.session_id, "chat_completions returned empty session_id!"

    # Traffic was distributed to BOTH workers, not 100% on worker 0
    assert len(prompt_calls[w0.name]) > 0, "worker 0 received no turns"
    assert len(prompt_calls[w1.name]) > 0, "worker 1 received no turns (pool starved!)"
    assert len(prompt_calls[w0.name]) + len(prompt_calls[w1.name]) == 20


@pytest.mark.asyncio
async def test_chat_completions_message_persistence_saves_user_and_assistant(pool2, monkeypatch):
    """Regression test: chat_completions message persistence must call
    AsyncHistoryService.get_or_create_conversation with valid kwargs (only session_id
    and user_id, without unexpected 'title' or 'project_id' kwargs), execute without
    TypeError, and persist both user and assistant messages.
    """
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.api.routes.chat import chat_completions, ChatRequest
    from app.models.db_model import Base, Conversation, Message
    from app.services.history_service_async import AsyncHistoryService

    # Set up in-memory SQLite database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_async_db_session():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(chat_mod, "USE_NEW_AGENT", True)
    monkeypatch.setattr(
        chat_mod, "_record_frontend_cartographic_observation", AsyncMock()
    )
    monkeypatch.setattr(
        chat_mod, "_build_cartography_turn_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        chat_mod, "_build_environment_turn_context", MagicMock(return_value="")
    )
    monkeypatch.setattr(chat_mod, "harvest_project_memory", AsyncMock())
    monkeypatch.setattr(chat_mod, "async_db_session", fake_async_db_session)

    w0 = pool2.bridges[0]
    w0.prompt = AsyncMock(return_value={"sessionId": "persisted-sid-1", "content": "Assistant answer on GIS"})

    # Spy on AsyncHistoryService.get_or_create_conversation to verify exact keyword arguments
    spy_calls = []
    real_get_or_create = AsyncHistoryService.get_or_create_conversation

    async def spy_get_or_create(self, session_id, user_id=None, **kwargs):
        spy_calls.append({"session_id": session_id, "user_id": user_id, "kwargs": kwargs})
        # If unexpected keyword arguments were passed, real_get_or_create will raise TypeError
        return await real_get_or_create(self, session_id, user_id=user_id, **kwargs)

    monkeypatch.setattr(AsyncHistoryService, "get_or_create_conversation", spy_get_or_create)

    # Capture log warnings to ensure no persistence failure occurred
    logged_warnings = []
    original_warning = chat_mod.logger.warning

    def capture_warning(msg, *args, **kwargs):
        formatted = msg % args if args else msg
        logged_warnings.append(formatted)
        original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(chat_mod.logger, "warning", capture_warning)

    test_sid = "persisted-sid-1"
    user_msg = "Please render buffer zone for Shenzhen"
    req = ChatRequest(message=user_msg, session_id=test_sid)

    async with session_factory() as request_db:
        resp = await chat_completions(
            req,
            request=MagicMock(),
            _user={"user_id": "test-user-persist"},
            owner_token=None,
            db=request_db,
        )

    assert resp.session_id == test_sid
    assert resp.content == "Assistant answer on GIS"

    # Verify get_or_create_conversation was called without invalid kwargs
    assert len(spy_calls) == 1
    assert spy_calls[0]["session_id"] == test_sid
    assert spy_calls[0]["user_id"] == "test-user-persist"
    assert "title" not in spy_calls[0]["kwargs"]
    assert "project_id" not in spy_calls[0]["kwargs"]
    assert spy_calls[0]["kwargs"] == {}

    # Verify no persistence failure was logged
    persistence_errors = [w for w in logged_warnings if "message persistence failed" in w]
    assert not persistence_errors, f"Persistence error was logged: {persistence_errors}"

    # Verify database contents directly: conversation and both messages must be saved
    async with session_factory() as db:
        conv = await db.get(Conversation, test_sid)
        assert conv is not None, "Conversation row was not created in DB!"

        stmt = select(Message).where(Message.conversation_id == test_sid).order_by(Message.id)
        result = await db.execute(stmt)
        messages = result.scalars().all()

        assert len(messages) == 2, f"Expected 2 messages saved, found {len(messages)}"
        assert messages[0].role == "user"
        assert messages[0].content == user_msg
        assert messages[1].role == "assistant"
        assert messages[1].content == "Assistant answer on GIS"


