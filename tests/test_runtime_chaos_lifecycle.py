"""Runtime chaos / lifecycle hardening regressions (WP-LIFECYCLE).

Covers:
- F11: async engine must be disposed at shutdown (was: only sync Engine disposed,
  leaving the async pool bound to a closed loop across lifespan cycles).
- F15-wiring: lifespan shutdown must drain chat fire-and-forget background tasks
  via ``app.services.chat.execution_engine.drain_background_tasks``.
- F14: async_db_session() must always close the session
  (success / exception-rollback / cancellation paths).
- F16: ConnectionManager.broadcast — bounded per-send wait, dead-socket eviction,
  snapshot iteration.
- Repeated-lifecycle invariant: N full lifespan cycles in one loop leave no
  orphan asyncio tasks and dispose the async engine every cycle.
"""
import asyncio

import pytest


# ─── F14: async_db_session close guarantees ─────────────────────────────────


class _TrackingSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def close(self):
        self.closes += 1


@pytest.fixture
def tracking_sessions(monkeypatch):
    """Replace AsyncSessionLocal with a factory producing close-tracking fakes."""
    import app.core.database as db_module

    made = []

    def factory():
        session = _TrackingSession()
        made.append(session)
        return session

    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    return made


@pytest.mark.asyncio
async def test_async_db_session_closes_on_success(tracking_sessions):
    from app.tools._utils import async_db_session

    async with async_db_session() as db:
        assert db is tracking_sessions[0]

    session = tracking_sessions[0]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closes == 1


@pytest.mark.asyncio
async def test_async_db_session_closes_and_rolls_back_on_error(tracking_sessions):
    from app.tools._utils import async_db_session

    with pytest.raises(ValueError, match="boom"):
        async with async_db_session():
            raise ValueError("boom")

    session = tracking_sessions[0]
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closes == 1


@pytest.mark.asyncio
async def test_async_db_session_closes_on_cancellation(tracking_sessions):
    """Cancellation must still close the session (finally path).

    Characterization note: CancelledError is BaseException, so the
    ``except Exception`` rollback branch is intentionally skipped — only
    the finally/close guarantee is asserted here.
    """
    from app.tools._utils import async_db_session

    entered = asyncio.Event()

    async def _use_session():
        async with async_db_session():
            entered.set()
            await asyncio.Event().wait()  # park until cancelled

    task = asyncio.create_task(_use_session())
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    session = tracking_sessions[0]
    assert session.commits == 0
    assert session.closes == 1


# ─── F16: ConnectionManager.broadcast ───────────────────────────────────────


class _HealthyWS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.sent.append(message)


class _StuckWS:
    """send_json parks forever — simulates a dead-but-not-errored socket."""

    def __init__(self):
        self.release = asyncio.Event()
        self.closed = False

    async def accept(self):
        pass

    async def send_json(self, message):
        await self.release.wait()

    async def close(self):
        self.closed = True


class _FailingWS:
    def __init__(self):
        self.attempts = 0
        self.closed = False

    async def accept(self):
        pass

    async def send_json(self, message):
        self.attempts += 1
        raise RuntimeError("socket dead")

    async def close(self):
        self.closed = True


@pytest.fixture
def fast_ws_timeout(monkeypatch):
    """Shrink the per-send bound so tests don't pay the production timeout."""
    import app.services.ws_service as ws_module

    monkeypatch.setattr(ws_module, "WS_SEND_TIMEOUT", 0.05, raising=False)
    return ws_module


@pytest.mark.asyncio
async def test_broadcast_stuck_socket_does_not_block_healthy(fast_ws_timeout):
    """F16 repro: stuck socket first in the list must not starve later sockets."""
    ws_module = fast_ws_timeout
    mgr = ws_module.ConnectionManager()
    stuck, healthy = _StuckWS(), _HealthyWS()
    await mgr.connect(stuck, "s1")
    await mgr.connect(healthy, "s1")

    # Outer bound only to turn "hangs forever" (old behavior) into a failure.
    await asyncio.wait_for(mgr.broadcast("s1", {"event": "x"}), timeout=5)

    assert healthy.sent == [{"event": "x"}]
    # Stuck socket evicted AND closed; healthy one kept.
    remaining = mgr.active_connections.get("s1", [])
    assert stuck not in remaining
    assert healthy in remaining
    assert stuck.closed is True, "an evicted socket must be closed, not left parked"


@pytest.mark.asyncio
async def test_broadcast_evicts_failed_socket(fast_ws_timeout):
    ws_module = fast_ws_timeout
    mgr = ws_module.ConnectionManager()
    failing, healthy = _FailingWS(), _HealthyWS()
    await mgr.connect(failing, "s1")
    await mgr.connect(healthy, "s1")

    await mgr.broadcast("s1", {"event": "x"})

    assert failing.attempts == 1
    assert healthy.sent == [{"event": "x"}]
    remaining = mgr.active_connections.get("s1", [])
    assert failing not in remaining
    assert healthy in remaining
    assert failing.closed is True, "an evicted socket must be closed, not left parked"

    # Evicted socket must not receive (or error on) later broadcasts.
    await mgr.broadcast("s1", {"event": "y"})
    assert failing.attempts == 1
    assert healthy.sent == [{"event": "x"}, {"event": "y"}]


@pytest.mark.asyncio
async def test_broadcast_round_bounded_by_single_timeout_with_stuck_sockets(fast_ws_timeout):
    """行为面（#703 拆分）：卡死 socket 在一轮广播后被逐出且已关闭，健康
    socket 不丢消息。并发性计时守卫拆到 perf 标记孪生测试（生产代码内部
    广播无法插桩屏障，墙钟是唯一证明手段——挂 marker 后满载不再间歇红）。"""
    ws_module = fast_ws_timeout
    ws_module.WS_SEND_TIMEOUT = 0.1
    mgr = ws_module.ConnectionManager()
    stuck = [_StuckWS() for _ in range(3)]
    healthy = _HealthyWS()
    for ws in stuck:
        await mgr.connect(ws, "s1")
    await mgr.connect(healthy, "s1")

    await mgr.broadcast("s1", {"event": "x"})

    assert healthy.sent == [{"event": "x"}]
    remaining = mgr.active_connections.get("s1", [])
    for ws in stuck:
        assert ws not in remaining
        assert ws.closed is True
    assert healthy in remaining


@pytest.mark.perf
@pytest.mark.asyncio
async def test_broadcast_round_bounded_by_single_timeout_concurrency_perf(fast_ws_timeout):
    """计时面（#703 挂 perf marker，#664 契约）：P2 (round-2 review) — sends
    are CONCURRENT, N stalled sockets cost ~one WS_SEND_TIMEOUT per round, not
    N× (sequential would be >= 0.3s at timeout=0.1). 只在 -m perf 隔离执行时判定。"""
    ws_module = fast_ws_timeout
    ws_module.WS_SEND_TIMEOUT = 0.1
    mgr = ws_module.ConnectionManager()
    stuck = [_StuckWS() for _ in range(3)]
    healthy = _HealthyWS()
    for ws in stuck:
        await mgr.connect(ws, "s1")
    await mgr.connect(healthy, "s1")

    loop = asyncio.get_running_loop()
    start = loop.time()
    await mgr.broadcast("s1", {"event": "x"})
    elapsed = loop.time() - start

    assert healthy.sent == [{"event": "x"}]
    assert elapsed < 0.25, (
        f"concurrent round must complete within ~one timeout, took {elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_broadcast_healthy_sockets_no_message_loss(fast_ws_timeout):
    ws_module = fast_ws_timeout
    mgr = ws_module.ConnectionManager()
    sockets = [_HealthyWS() for _ in range(3)]
    for ws in sockets:
        await mgr.connect(ws, "s1")

    for i in range(3):
        await mgr.broadcast("s1", {"event": f"m{i}"})

    for ws in sockets:
        assert ws.sent == [{"event": "m0"}, {"event": "m1"}, {"event": "m2"}]
    assert len(mgr.active_connections["s1"]) == 3


# ─── F11 + F15-wiring + repeated-lifecycle invariant ────────────────────────


class _FakeAsyncEngine:
    def __init__(self):
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1


def _drive_lifespan_cycles(monkeypatch, cycles: int):
    """Drive ``lifespan(app)`` through N full startup/shutdown cycles in one loop.

    Returns (fake_async_engine, drain_calls, orphans_per_cycle, cycle_errors).
    Module globals (init_db / AsyncEngine / drain_background_tasks) are
    monkeypatched per the established tests/test_sse_resume.py pattern.
    """
    import app.core.database as db_module
    import app.main as main_module

    monkeypatch.setattr(db_module, "init_db", lambda: None)

    fake_async_engine = _FakeAsyncEngine()
    monkeypatch.setattr(db_module, "AsyncEngine", fake_async_engine)

    drain_calls = []

    async def fake_drain(timeout: float = 5.0):
        drain_calls.append(timeout)

    monkeypatch.setattr(main_module, "drain_background_tasks", fake_drain)

    orphans_per_cycle = []
    cycle_errors = []

    async def _drive():
        for _ in range(cycles):
            baseline = asyncio.all_tasks()
            async with main_module.lifespan(main_module.app):
                # First async DB use of the cycle must not hit a closed loop.
                try:
                    from app.tools._utils import async_db_session

                    async with async_db_session() as db:
                        await db.commit()
                except Exception as e:  # noqa: BLE001
                    cycle_errors.append(e)
            orphans_per_cycle.append(asyncio.all_tasks() - baseline)

    asyncio.run(_drive())
    return fake_async_engine, drain_calls, orphans_per_cycle, cycle_errors


def test_lifespan_shutdown_disposes_async_engine(monkeypatch, tracking_sessions):
    """F11: every shutdown must dispose the async engine, not just the sync one."""
    fake_async_engine, _, _, _ = _drive_lifespan_cycles(monkeypatch, cycles=2)
    assert fake_async_engine.dispose_calls == 2


def test_lifespan_shutdown_drains_background_tasks(monkeypatch, tracking_sessions):
    """F15-wiring: lifespan shutdown calls drain_background_tasks each cycle."""
    _, drain_calls, _, _ = _drive_lifespan_cycles(monkeypatch, cycles=2)
    assert drain_calls == [5.0, 5.0]


def test_repeated_lifespan_cycles_leave_no_orphans(monkeypatch, tracking_sessions):
    """Invariant: 3 cycles — no orphan tasks, no 'Event loop is closed' on
    the next cycle's first async DB use, async engine disposed every cycle."""
    fake_async_engine, drain_calls, orphans_per_cycle, cycle_errors = (
        _drive_lifespan_cycles(monkeypatch, cycles=3)
    )

    assert fake_async_engine.dispose_calls == 3
    assert len(drain_calls) == 3
    for i, orphans in enumerate(orphans_per_cycle):
        names = [getattr(t.get_coro(), "__qualname__", repr(t)) for t in orphans]
        assert not orphans, f"cycle {i}: orphan asyncio tasks after shutdown: {names}"
    closed_loop_errors = [
        e for e in cycle_errors if "Event loop is closed" in str(e)
    ]
    assert not closed_loop_errors, (
        f"async DB use after shutdown hit a closed loop: {closed_loop_errors}"
    )
    assert not cycle_errors, f"unexpected errors during cycles: {cycle_errors}"
