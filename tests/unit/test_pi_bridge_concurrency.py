"""Tests for CORE-01: Isolate active turn state per PiBridge instance and session_id.

Verifies:
1. Multiple PiBridge instances running concurrent turns do not stomp each other's turn state.
2. Turn state is isolated on each PiBridge instance (self._current_turn) and keyed by session_id in _active_turns.
3. active_turn_correlation returns session-isolated identity and avoids crosstalk.
4. Aborting one session's turn does not cancel another session's active turn.
5. Completion and cleanup of one turn leaves other in-flight turns unaffected.
"""
import asyncio
import pytest
from app.agent_pi_bridge import (
    PiBridge,
    _active_turns,
    active_turn_correlation,
    get_active_turn_entry,
)


class MockRpcClient:
    """Mock RPC client for testing PiBridge turn isolation under concurrency."""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.in_flight_event = asyncio.Event()
        self.can_finish_event = asyncio.Event()
        self._pending_ids: set[str] = {"req-1"}
        self.aborted = False

    def pending_request_ids(self) -> set[str]:
        return set(self._pending_ids)

    def fail_pending_ids(self, ids: set[str], reason: str) -> None:
        pass

    async def request(self, command: str, data: dict = None) -> dict:
        if command == "abort":
            self.aborted = True
            return {"status": "aborted"}
        if command == "prompt":
            self.in_flight_event.set()
            await self.can_finish_event.wait()
            # Push settlement events to the queue
            await self.events.put({"type": "agent_end", "willRetry": False})
            await self.events.put({"type": "agent_settled"})
            return {"status": "ok"}
        return {}


@pytest.mark.asyncio
async def test_concurrent_pi_bridge_turns_isolation_and_no_crosstalk():
    """Verify two PiBridge instances running concurrent turns have isolated state."""
    rpc1 = MockRpcClient()
    rpc2 = MockRpcClient()

    bridge1 = PiBridge(rpc=rpc1)
    bridge2 = PiBridge(rpc=rpc2)

    session_a = "sess-concurrency-A"
    session_b = "sess-concurrency-B"

    # Start prompt turns concurrently on separate bridge instances
    task1 = asyncio.create_task(bridge1.prompt("hello from A", session_id=session_a))
    task2 = asyncio.create_task(bridge2.prompt("hello from B", session_id=session_b))

    # Wait until both turns are actively in-flight
    await asyncio.wait_for(rpc1.in_flight_event.wait(), timeout=5.0)
    await asyncio.wait_for(rpc2.in_flight_event.wait(), timeout=5.0)

    # 1. Verify instance encapsulation
    assert bridge1._current_turn is not None, "bridge1 must have active _current_turn"
    assert bridge2._current_turn is not None, "bridge2 must have active _current_turn"
    assert bridge1._current_turn.session_id == session_a
    assert bridge2._current_turn.session_id == session_b
    assert bridge1._current_turn.bridge is bridge1
    assert bridge2._current_turn.bridge is bridge2
    assert bridge1._current_turn.turn_id != bridge2._current_turn.turn_id

    # 2. Verify session table registration
    entry_a = get_active_turn_entry(session_a)
    entry_b = get_active_turn_entry(session_b)
    assert entry_a is not None and entry_a.session_id == session_a
    assert entry_b is not None and entry_b.session_id == session_b
    assert entry_a.token is not None
    assert entry_b.token is not None
    assert entry_a.token is not entry_b.token

    # 3. Verify correlation lookup isolation (no cross-contamination)
    tid_a, rid_a, sid_a = active_turn_correlation(session_id=session_a)
    assert sid_a == session_a
    assert tid_a == entry_a.turn_id
    assert rid_a == entry_a.run_id

    tid_b, rid_b, sid_b = active_turn_correlation(session_id=session_b)
    assert sid_b == session_b
    assert tid_b == entry_b.turn_id
    assert rid_b == entry_b.run_id

    # Without session_id when multiple turns are in-flight, returns None to avoid crosstalk
    none_tid, none_rid, none_sid = active_turn_correlation(session_id=None)
    assert (none_tid, none_rid, none_sid) == (None, None, None)

    # 4. Abort session A's turn only
    abort_res = await bridge1.abort(session_id=session_a)
    assert abort_res.get("status") == "aborted"
    assert rpc1.aborted is True
    assert entry_a.token.cancelled, "Session A's cancellation token must be cancelled"
    assert not entry_b.token.cancelled, "Session B's token must NOT be cancelled (no crosstalk)"
    assert rpc2.aborted is False, "Bridge 2 RPC must NOT have received abort"

    # 5. Complete session A
    rpc1.can_finish_event.set()
    await asyncio.wait_for(task1, timeout=5.0)

    # Session A is cleaned up, but session B is still active
    assert bridge1._current_turn is None, "bridge1._current_turn should be None after completion"
    assert session_a not in _active_turns, "session_a should be removed from _active_turns"
    assert bridge2._current_turn is not None, "bridge2._current_turn must still be active"
    assert session_b in _active_turns, "session_b must still be in _active_turns"
    assert not entry_b.token.cancelled, "Session B's token must still remain uncancelled"

    # Now single turn in flight -> un-sessioned active_turn_correlation safely resolves session B
    tid_single, _, sid_single = active_turn_correlation(session_id=None)
    assert sid_single == session_b
    assert tid_single == entry_b.turn_id

    # Complete session B
    rpc2.can_finish_event.set()
    await asyncio.wait_for(task2, timeout=5.0)

    assert bridge2._current_turn is None
    assert session_b not in _active_turns
