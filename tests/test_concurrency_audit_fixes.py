"""Regression tests for the concurrency findings of the master full review.

- CONC-F1: a late abort-on-disconnect must not kill the SAME session's
  successor turn (token/pending-future identity scoping).
- CONC-F2: session-lock registry eviction requires a grace period (the
  release→waiter-resume handoff window made instantaneous idle checks evict
  a lock a waiter was about to re-acquire).
- CONC-F3: update_plan_status read-check-write is serialized against
  concurrent writers (lost update between executor terminal write and user
  cancel).
- CONC-F4: the failed-wave sibling drain is bounded (stragglers cancelled,
  plan never wedged on a stuck sibling).
- CONC-F5: stop() survives the reader clearing _process concurrently.
"""
import asyncio
import time

import pytest


# ── CONC-F1: abort scoped to the snapshot token + pending futures ───────────

class _RpcStub:
    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}
        self.abort_delay = 0.0
        self.abort_calls = 0

    def pending_request_ids(self):
        return set(self._pending.keys())

    def fail_pending_ids(self, ids, reason):
        n = 0
        for rid in ids:
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_exception(RuntimeError(reason))
                n += 1
        return n

    def fail_all_pending(self, reason):
        self.fail_pending_ids(set(self._pending.keys()), reason)

    async def request(self, cmd, *a, **kw):
        self.abort_calls += 1
        await asyncio.sleep(self.abort_delay)
        return {"ok": True}

    def register(self, rid):
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        return fut


@pytest.mark.asyncio
async def test_F1_late_abort_spares_same_session_successor_turn(monkeypatch):
    import app.agent_pi_bridge as bridge_mod
    from app.services.jobs.cancellation import CancellationToken

    rpc = _RpcStub()
    rpc.abort_delay = 0.05

    b = bridge_mod.PiBridge.__new__(bridge_mod.PiBridge)
    b._rpc = rpc

    token_t1 = CancellationToken()
    monkeypatch.setattr(bridge_mod, "_active_turn_sid", "s-A", raising=False)
    monkeypatch.setattr(bridge_mod, "_active_turn_token", token_t1, raising=False)

    fut_t1 = rpc.register("prompt-t1")

    abort_task = asyncio.create_task(b.abort(session_id="s-A"))
    await asyncio.sleep(0.01)  # abort RPC now in flight

    # Same-session successor turn replaces the token + registers its own
    # prompt future while the abort RPC is still in flight.
    token_t2 = CancellationToken()
    monkeypatch.setattr(bridge_mod, "_active_turn_token", token_t2, raising=False)
    fut_t2 = rpc.register("prompt-t2")

    await abort_task

    # T1's prompt future failed (abort-relevant), T2's survives.
    assert fut_t1.done() and fut_t1.exception() is not None
    assert not fut_t2.done()
    # T2's token was NOT ignited.
    assert not token_t2.cancelled
    # T1's token was (it was still active at snapshot time).
    assert token_t1.cancelled


@pytest.mark.asyncio
async def test_F1_abort_still_cancels_the_live_token(monkeypatch):
    import app.agent_pi_bridge as bridge_mod
    from app.services.jobs.cancellation import CancellationToken

    rpc = _RpcStub()
    b = bridge_mod.PiBridge.__new__(bridge_mod.PiBridge)
    b._rpc = rpc

    token = CancellationToken()
    monkeypatch.setattr(bridge_mod, "_active_turn_sid", "s-A", raising=False)
    monkeypatch.setattr(bridge_mod, "_active_turn_token", token, raising=False)
    fut = rpc.register("prompt-live")

    await b.abort(session_id="s-A")

    assert token.cancelled
    assert fut.done() and fut.exception() is not None


# ── CONC-F2: lock eviction grace period ─────────────────────────────────────

def test_F2_recently_used_lock_is_not_evicted():
    from app.services.chat.execution_engine import ChatExecutionEngine

    eng = ChatExecutionEngine.__new__(ChatExecutionEngine)
    eng._session_locks = {}
    eng._session_lock_last_used = {}
    eng._deferred_lock_drops = {}
    eng._MAX_LOCKS = 4

    # Fill the registry past capacity with freshly-touched locks.
    for i in range(8):
        eng._get_session_lock(f"s-{i}")
    assert len(eng._session_locks) == 8
    # A just-touched lock is idle-but-fresh → none may be evicted.
    eng._evict_idle_locks()
    assert len(eng._session_locks) == 8

    # Age every lock past the grace window → eviction proceeds.
    grace = ChatExecutionEngine._LOCK_EVICTION_GRACE_S
    for sid in eng._session_lock_last_used:
        eng._session_lock_last_used[sid] = time.monotonic() - (grace + 1)
    eng._evict_idle_locks()
    assert len(eng._session_locks) < 8


# ── CONC-F3: update_plan_status serialization ───────────────────────────────

@pytest.mark.asyncio
async def test_F3_concurrent_terminal_and_cancel_no_lost_update(monkeypatch):
    from app.services import plan_mode as pm

    # Storage seam with a controllable load.
    state: dict[str, dict] = {}

    async def fake_load(session_id, plan_id):
        return dict(state[plan_id]) if plan_id in state else None

    async def fake_overwrite(session_id, plan_id, data):
        state[plan_id] = dict(data)
        return True

    monkeypatch.setattr(pm, "load_plan", fake_load)
    monkeypatch.setattr(pm.session_data_manager, "overwrite", fake_overwrite)

    # Interleave: executor's completed write and a user cancel racing on a
    # running plan. With the status write lock both read-check-writes
    # serialize; the terminal (first) write wins and the cancel either lands
    # BEFORE it (cancelled terminal) or is refused after (completed stays).
    state["p1"] = {"__status__": "running", "title": "t", "steps": []}

    order = {"n": 0}

    async def slow_writer(status):
        # Enter the critical section, yield mid-read-check (the exact
        # interleaving that lost updates pre-lock), then write.
        nonlocal order
        order["n"] += 1
        await asyncio.sleep(0)
        await pm.update_plan_status("s", "p1", __status__=status)

    # Sequential racing attempts: completed first, cancel second.
    await slow_writer("completed")
    await slow_writer("cancelled")
    assert state["p1"]["__status__"] == "completed"

    # Reverse order: cancel first (valid on running), completed second must
    # be REFUSED (cancelled is immutable).
    state["p2"] = {"__status__": "running", "title": "t", "steps": []}
    await pm.update_plan_status("s", "p2", __status__="cancelled")
    await pm.update_plan_status("s", "p2", __status__="completed")
    assert state["p2"]["__status__"] == "cancelled"


# ── CONC-F4: bounded sibling drain ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_F4_failed_wave_drain_is_bounded(monkeypatch):
    from app.services import plan_mode as pm
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.tool(name="audit_fail_fast", description="x")
    def fail_fast(x: str = "1"):
        return {"success": False, "message": "boom"}

    started = asyncio.Event()
    release = asyncio.Event()

    @reg.tool(name="audit_stuck_sync", description="x")
    async def stuck_sync(x: str = "1"):
        started.set()
        await release.wait()  # simulate IO with no deadline
        return {"success": True, "bbox": [0, 0, 1, 1]}

    monkeypatch.setattr(pm, "_SIBLING_DRAIN_TIMEOUT_S", 0.1)

    sid = "s-f4"
    plan = pm.PlanProposal(
        title="t",
        steps=[
            pm.PlanStep(id="bad", tool="audit_fail_fast", args={}),
            pm.PlanStep(id="stuck", tool="audit_stuck_sync", args={}),
        ],
    )
    plan_id = await pm.store_plan(sid, plan)

    async def _run():
        return await pm.execute_plan_async(sid, plan_id, reg)

    run_task = asyncio.create_task(_run())
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # The wave failed fast; the stuck sibling gets drained within the bound
    # even though `release` is never set (straggler cancelled).
    ret = await asyncio.wait_for(run_task, timeout=5.0)
    assert ret["success"] is False
    assert ret["failed_step"] == "bad"
    release.set()  # cleanup


# ── CONC-F5: stop() survives concurrent _process clearing ───────────────────

@pytest.mark.asyncio
async def test_F5_stop_tolerates_reader_clearing_process():
    from app.services.chat.pi_rpc_client import PiRpcClient

    client = PiRpcClient.__new__(PiRpcClient)

    class _Proc:
        def __init__(self):
            self.killed = False
            self._exited = False

        def terminate(self):
            # The process exits immediately; the reader's finally clears
            # client._process right after terminate returns.
            self._exited = True

            async def _clear():
                client._process = None

            asyncio.get_running_loop().create_task(_clear())

        def poll(self):
            return 0 if self._exited else None

        def kill(self):
            self.killed = True

    client._process = _Proc()
    client._reader_task = None
    client._stderr_task = None

    # Pre-fix: AttributeError on self._process.poll() after the clear.
    await client.stop()
