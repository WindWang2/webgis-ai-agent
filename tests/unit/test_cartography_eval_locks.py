"""Test GIS-05: Bound and prune cartography evaluation locks.

Verifies that:
1. _get_cartography_eval_lock returns and maintains locks in bounded OrderedDict.
2. Exceeding _CARTOGRAPHY_EVAL_LOCKS_LIMIT evicts the least recently used locks.
3. clear_cartographic_session_state prunes locks on session deletion.
4. Loop affinity changes safely refresh the lock without cross-loop Future attachment crashes.
"""
import asyncio
from unittest.mock import patch
import pytest

from app.services import cartography_runtime
from app.services.cartography_runtime import (
    _get_cartography_eval_lock,
    _cartography_eval_locks,
    clear_cartographic_session_state,
)


@pytest.mark.asyncio
async def test_cartography_eval_lock_reuse_and_prune():
    """Locks are reused per session and pruned on session cleanup."""
    sid = "test-session-eval-1"
    lock1 = _get_cartography_eval_lock(sid)
    lock2 = _get_cartography_eval_lock(sid)
    assert lock1 is lock2
    assert sid in _cartography_eval_locks

    # Session deletion prunes lock
    clear_cartographic_session_state(sid)
    assert sid not in _cartography_eval_locks


@pytest.mark.asyncio
async def test_cartography_eval_locks_lru_bounded():
    """Lock collection enforces LRU capacity bounding."""
    with patch.object(cartography_runtime, "_CARTOGRAPHY_EVAL_LOCKS_LIMIT", 5):
        _cartography_eval_locks.clear()
        for i in range(10):
            _get_cartography_eval_lock(f"sess-{i}")

        assert len(_cartography_eval_locks) == 5
        # The oldest (0..4) should have been evicted, leaving 5..9
        assert "sess-0" not in _cartography_eval_locks
        assert "sess-4" not in _cartography_eval_locks
        assert "sess-9" in _cartography_eval_locks


@pytest.mark.asyncio
async def test_cartography_eval_lock_loop_refresh():
    """Lock bound to a different event loop is safely refreshed."""
    sid = "cross-loop-session"
    stale_lock = asyncio.Lock()
    # Simulate lock bound to another loop
    other_loop = asyncio.new_event_loop()
    try:
        stale_lock._loop = other_loop
        _cartography_eval_locks[sid] = stale_lock

        current_loop = asyncio.get_running_loop()
        refreshed_lock = _get_cartography_eval_lock(sid)
        assert refreshed_lock is not stale_lock
        assert refreshed_lock._loop is current_loop or refreshed_lock._loop is None
    finally:
        other_loop.close()
        clear_cartographic_session_state(sid)
