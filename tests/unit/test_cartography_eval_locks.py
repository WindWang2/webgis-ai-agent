"""Test GIS-05: Bound and prune cartography evaluation locks.

Verifies that:
1. _get_cartography_eval_lock returns and maintains locks in bounded OrderedDict.
2. Exceeding _CARTOGRAPHY_EVAL_LOCKS_LIMIT evicts the least recently used locks.
3. clear_cartographic_session_state prunes locks on session deletion.
4. Loop affinity changes safely refresh the lock without cross-loop Future attachment crashes.
5. #1681: a lock that is held (or has waiters) is NEVER evicted — eviction must
   not let one session hold two "mutexes" at once.
"""
import asyncio
from unittest.mock import patch
import pytest

from app.services import cartography_runtime
from app.services.cartography_runtime import (
    _get_cartography_eval_lock,
    _lock_in_use,
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


@pytest.mark.asyncio
async def test_cartography_eval_lock_held_never_evicted():
    """#1681: 持锁中的会话锁绝不逐出 —— 否则同一会话双持两把「互斥锁」。

    旧实现逐出完全不检查 locked()/_waiters：压满上限时持锁会话的条目被
    当 LRU 淘汰，下一次取锁拿到新对象，同一会话同时持两把锁（评审与
    harness 写入并发执行且无任何异常迹象）。
    """
    sid = "held-session"
    limit = 3
    with patch.object(cartography_runtime, "_CARTOGRAPHY_EVAL_LOCKS_LIMIT", limit):
        _cartography_eval_locks.clear()
        held = _get_cartography_eval_lock(sid)
        await held.acquire()  # 持锁不放（模拟评审进行中）
        try:
            # 压力远超上限：逐出只能拿空闲锁开刀，持锁会话必须留在表内
            for i in range(limit + 4):
                _get_cartography_eval_lock(f"filler-{i}")
            assert sid in _cartography_eval_locks, "持锁中的会话锁被逐出（#1681 复发）"
            # 同会话再次取锁必须是同一对象（不得双持）
            assert _get_cartography_eval_lock(sid) is held
            assert held.locked() is True
            # 有界性保持（至少留出一个可回收的空闲槽）
            assert len(_cartography_eval_locks) <= limit
        finally:
            if held.locked():
                held.release()
            _cartography_eval_locks.clear()


@pytest.mark.asyncio
async def test_lock_in_use_covers_waiter_handoff_window():
    """#1681: _lock_in_use 同时看 locked() 与 _waiters（release 交接窗口）。

    release() 经 call_soon 唤醒等待者：等待者尚未重新持锁的窗口里
    locked() 已是 False，只看 locked() 会在这个窗口误判「可逐出」。
    """
    lock = asyncio.Lock()
    assert _lock_in_use(lock) is False
    await lock.acquire()
    assert _lock_in_use(lock) is True
    waiter = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0.05)
    assert lock._waiters, "等待者未注册 —— 无法构造交接窗口"
    lock.release()  # 唤醒经 call_soon 派发：此刻 locked()==False 但仍有等待者
    assert lock.locked() is False
    assert _lock_in_use(lock) is True
    await waiter
