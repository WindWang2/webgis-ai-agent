"""Unit tests for #1043: Session-lock resilience, degraded-mode visibility, and TTL expiry signals.

Validates:
1. Degraded mode visibility (lock.is_degraded, lock.mode == 'degraded') and optional fail_on_degraded.
2. TTL expiry mid-mutation signal (lock.lost, lock.is_lost) and optional fail_on_lost.
3. SessionPlan mutation aborts dirty save if lock ownership is lost.
4. Lock acquire budget defaults to 30s to match cartographic evaluation holders.
5. In-process fallback when Redis is explicitly disabled (is_degraded is False).
6. Release phase (#1690): sync release() issues the token-checked DEL and keeps the
   fire-and-forget task strongly referenced — with and without a running loop.
7. #1681 (loop-affinity): in-process fallback locks are bucketed per event loop —
   a loop change must never clear another live loop's locks (same-loop double-hold),
   closed loops' buckets are reclaimed, and same-loop mutual exclusion still holds.
"""
import asyncio
import gc
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import app.services.distributed_lock as dl
from app.services.distributed_lock import (
    _InProcessLock,
    _ResilientSessionLock,
    LockDegradedError,
    LockLostError,
    SessionLockRegistry,
    session_lock_registry,
)
from app.services.session_plan import apply_tool_result, SessionPlan, save_session_plan


@pytest.mark.asyncio
async def test_lock_degraded_mode_visibility_and_error():
    """Redis connection failure marks lock as degraded or raises LockDegradedError if requested."""
    fake_client = MagicMock()
    fake_client.set = AsyncMock(side_effect=ConnectionError("Redis connection refused"))

    # 1. Default (fail_on_degraded=False) -> degrades to in-process and exposes is_degraded
    lock = _ResilientSessionLock(
        fake_client, "k1043:test", _InProcessLock(), fail_on_degraded=False
    )
    async with lock:
        assert lock.is_degraded is True
        assert lock.mode == "degraded"
        assert lock.is_redis_backed is False

    # 2. Strict (fail_on_degraded=True) -> raises LockDegradedError
    strict_lock = _ResilientSessionLock(
        fake_client, "k1043:test", _InProcessLock(), fail_on_degraded=True
    )
    with pytest.raises(LockDegradedError) as exc_info:
        async with strict_lock:
            pass
    assert "degraded" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_lock_lost_signal_on_renew_expiry():
    """Lost ownership during renew-loop flips lock.lost to True and raises if fail_on_lost=True."""
    class FakeClient:
        def __init__(self):
            self.calls = 0
            self.ownership_lost = asyncio.Event()

        async def set(self, *a, **k):
            return True

        async def eval(self, script, numkeys, key, *args):
            self.calls += 1
            # First renewal succeeds, second reports ownership lost
            if self.calls == 1:
                return 1
            self.ownership_lost.set()
            return 0

    client = FakeClient()
    lock = _ResilientSessionLock(
        client, "k1043:lost", _InProcessLock(), fail_on_lost=False
    )

    orig_interval = dl._RENEW_INTERVAL_S
    dl._RENEW_INTERVAL_S = 0.01
    try:
        async with lock:
            assert lock.is_redis_backed is True
            assert lock.lost is False
            await asyncio.wait_for(client.ownership_lost.wait(), timeout=2)
            assert lock.lost is True
    finally:
        dl._RENEW_INTERVAL_S = orig_interval

    # Test with fail_on_lost=True
    strict_client = FakeClient()
    strict_lock = _ResilientSessionLock(
        strict_client, "k1043:lost_strict", _InProcessLock(), fail_on_lost=True
    )
    dl._RENEW_INTERVAL_S = 0.01
    try:
        with pytest.raises(LockLostError):
            async with strict_lock:
                await asyncio.wait_for(strict_client.ownership_lost.wait(), timeout=2)
    finally:
        dl._RENEW_INTERVAL_S = orig_interval


@pytest.mark.asyncio
async def test_session_plan_aborts_save_if_lock_lost():
    """apply_tool_result checks lock.lost and aborts envelope mutation to avoid dirty writes."""
    from app.services.session_data import SessionDataManager
    from app.services.session_plan import load_session_plan
    store = SessionDataManager()
    sid = "sess-lock-abort"

    plan = SessionPlan(
        envelope_id="env-init",
        session_id=sid,
        user_goal="Initial Goal",
        updated_at=time.time(),
    )
    await save_session_plan(plan, store=store)

    # Mock lock whose .lost is True
    fake_lock = MagicMock()
    fake_lock.lost = True
    fake_lock.__aenter__ = AsyncMock(return_value=fake_lock)
    fake_lock.__aexit__ = AsyncMock(return_value=None)

    with patch.object(session_lock_registry, "lock", return_value=fake_lock):
        events = await apply_tool_result(
            sid,
            "webgis_map_intent",
            {"plan": {"query": "Find parks in Beijing"}},
            success=True,
            store=store,
        )
        assert events == []

    # Verify store was NOT updated with the new goal
    loaded = await load_session_plan(sid, store=store)
    assert loaded.user_goal == "Initial Goal"


@pytest.mark.asyncio
async def test_lock_acquire_budget_defaults_to_30s():
    """Acquisition budget defaults to 30s and respects custom acquire_timeout_s."""
    from app.services.distributed_lock import _DEFAULT_ACQUIRE_TIMEOUT_S
    assert _DEFAULT_ACQUIRE_TIMEOUT_S == 30.0

    default_lock = _ResilientSessionLock(None, "k:def", _InProcessLock())
    assert default_lock._acquire_timeout_s == 30.0

    fake_client = MagicMock()
    # Always fails to acquire with False (another owner holds lock)
    fake_client.set = AsyncMock(return_value=False)

    lock = _ResilientSessionLock(
        fake_client, "k1043:timeout", _InProcessLock(), acquire_timeout_s=0.1
    )
    t0 = time.monotonic()
    with pytest.raises(TimeoutError) as exc_info:
        async with lock:
            pass
    elapsed = time.monotonic() - t0
    assert 0.08 <= elapsed <= 0.5
    assert "could not acquire" in str(exc_info.value)


@pytest.mark.asyncio
async def test_in_process_mode_when_redis_disabled():
    """When Redis is not configured, lock mode is inprocess and is_degraded is False."""
    lock = _ResilientSessionLock(
        None, "k1043:local", _InProcessLock(), redis_configured=False
    )
    async with lock:
        assert lock.mode == "inprocess"
        assert lock.is_degraded is False
        assert lock.is_redis_backed is False
        assert lock.lost is False

    # When Redis was configured but client is None -> degraded mode
    degraded_lock = _ResilientSessionLock(
        None, "k1043:deg", _InProcessLock(), redis_configured=True
    )
    async with degraded_lock:
        assert degraded_lock.mode == "degraded"
        assert degraded_lock.is_degraded is True


# ── #1690：释放相位（此前 12 个锁测试 + 全套 chaos fault spec 零覆盖）─────────


class _ReleaseEchoClient:
    """Fake Redis client recording the token-checked DEL (script, key, token)."""

    def __init__(self, *, del_delay_s: float = 0.0, del_error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self._del_delay_s = del_delay_s
        self._del_error = del_error

    async def set(self, *a, **k):
        return True

    async def eval(self, script, numkeys, key, *args):
        # renew 与 release 都走 eval；只记录 DEL（release script 为单 key 无 TTL 参数）
        if len(args) == 1 and isinstance(args[0], str):
            self.calls.append((key, args[0]))
        if self._del_error is not None:
            raise self._del_error
        if self._del_delay_s:
            await asyncio.sleep(self._del_delay_s)
        return 1


@pytest.mark.asyncio
async def test_sync_release_issues_token_checked_del_and_task_is_tracked():
    """#1690：同步 release()（非 __aexit__）必须发出 token 校验 DEL，且任务被强引用保持。"""
    client = _ReleaseEchoClient(del_delay_s=0.05)
    lock = _ResilientSessionLock(client, "s1690:rel", _InProcessLock())
    await lock.__aenter__()
    assert lock.is_redis_backed

    lock.release()               # 同步路径（不是 __aexit__）
    await asyncio.sleep(0)       # 让 DEL 任务开始执行（协程已在途中）
    gc.collect()                 # GC 压力：未强引用的任务此刻可被回收
    assert client.calls, "sync release() 从未发出 token 校验 DEL —— key 将活到 TTL"
    assert dl._release_tasks, (
        "release 任务是 fire-and-forget（可被 GC）—— 必须强引用保持（F12）"
    )

    await asyncio.sleep(0.1)     # 等 DEL 完成
    assert client.calls[0][1] == lock._token, "DEL 必须带 token 校验（不得释放别人的锁）"
    assert not dl._release_tasks, "DEL 完成后应解除强引用（否则集合无界增长）"


def test_sync_release_without_running_loop_issues_token_del():
    """#1690：无运行 loop 的同步上下文（同步调用方 / 关停路径）也必须立即发完 DEL。

    旧行为：get_running_loop() 抛 RuntimeError 被宽 except 吞掉 → Redis 锁根本不
    释放，只能等 30s TTL 自然过期，期间同 session 的 acquire 全部 LockContentionError。
    """
    client = _ReleaseEchoClient()
    lock = _ResilientSessionLock(client, "s1690:rel-noloop", _InProcessLock())

    async def _acquire():
        await lock.__aenter__()
        # 关停路径语义：续约任务随 loop 收尾先行取消，避免对已关闭 loop 的 cancel
        lock._renewer.cancel()
        try:
            await lock._renewer
        except (asyncio.CancelledError, Exception):
            pass

    # asyncio.run 收尾会把 current loop 置 None —— 保存/恢复，避免影响其它用例
    try:
        prev_loop = asyncio.get_event_loop()
    except RuntimeError:
        prev_loop = None
    try:
        asyncio.run(_acquire())
        assert lock.is_redis_backed
        lock.release()           # 此处无运行 loop
    finally:
        if prev_loop is not None:
            asyncio.set_event_loop(prev_loop)

    assert client.calls, "无运行 loop 时 sync release() 未发出 DEL —— key 只能等 TTL 过期"
    assert client.calls[0][1] == lock._token, "DEL 必须带 token 校验"


def test_sync_release_without_running_loop_failure_is_observable(caplog):
    """#1690：无 loop 的同步 DEL 失败必须升级为 warning（旧代码只 logger.debug）。"""
    client = _ReleaseEchoClient(del_error=ConnectionError("redis gone"))
    lock = _ResilientSessionLock(client, "s1690:rel-fail", _InProcessLock())

    async def _acquire():
        await lock.__aenter__()
        lock._renewer.cancel()
        try:
            await lock._renewer
        except (asyncio.CancelledError, Exception):
            pass

    try:
        prev_loop = asyncio.get_event_loop()
    except RuntimeError:
        prev_loop = None
    try:
        asyncio.run(_acquire())
        with caplog.at_level(logging.WARNING):
            lock.release()       # 失败可观测但不致命：不得抛异常
    finally:
        if prev_loop is not None:
            asyncio.set_event_loop(prev_loop)

    assert any(
        "sync release failed" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "同步 DEL 失败必须 warning 级可观测（旧代码是 debug 级静默丢弃）"


# ── #1681：in-process 回退锁的 loop-affinity ──────────────────────────────


@pytest.mark.asyncio
async def test_fallback_lock_same_loop_second_acquirer_waits():
    """#1681：同一 loop 内第二个 acquire 必须等待，而不是被立即授予。

    这是 fallback 互斥契约本身（Redis 降级期进程内串行化的全部依据）。
    """
    reg = SessionLockRegistry()
    try:
        held = reg._fallback_lock("s-1681-hold")
        await held.acquire()
        second = asyncio.create_task(reg._fallback_lock("s-1681-hold").acquire())
        await asyncio.sleep(0.05)
        assert not second.done(), (
            "同一 loop 的第二个 acquire 被立即授予 —— 进程内互斥失效（#1681）"
        )
        held.release()
        await asyncio.wait_for(second, timeout=1.0)
    finally:
        reg._fallback_by_loop.clear()
        reg._fallback_locks.clear()


@pytest.mark.asyncio
async def test_fallback_lock_loop_change_does_not_wipe_other_loop():
    """#1681：loop 变化不再 clear() 整张表 —— 另一 loop 的持锁会话不得双持。

    旧实现在 running loop 变化时 ``self._fallback_locks.clear()``：REST loop
    持锁期间，另一 loop（geocompute bridge loop / 下一轮测试 loop）任一调用
    都会把表清空，REST loop 随后再取同一会话即拿到**新**锁 ⇒ 同一 loop 同一
    会话双持两把「互斥锁」（Redis 降级期进程内互斥静默失效）。
    """
    reg = SessionLockRegistry()
    lock_a = reg._fallback_lock("s-1681-cross")
    await lock_a.acquire()  # loop A 持锁不放
    acquired_on_b: list = []

    def _worker():
        async def _inner():
            acquired_on_b.append(reg._fallback_lock("s-1681-cross"))

        loop_b = asyncio.new_event_loop()
        try:
            loop_b.run_until_complete(_inner())
        finally:
            loop_b.close()

    try:
        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "loop B 工作线程未在 5s 内结束"
        assert acquired_on_b, "loop B 未取得回退锁"
        # per-loop 分桶：loop B 拿自己的锁对象（跨 loop 共享 asyncio.Lock 是
        # ADR-0096 既有硬约束边界），但绝不清除 loop A 的表。
        assert acquired_on_b[0] is not lock_a
        # 关键回归：loop A 再取同一会话必须是同一对象。
        assert reg._fallback_lock("s-1681-cross") is lock_a, (
            "loop B 的调用摧毁了 loop A 的锁表 —— 同一会话双持（#1681 复发）"
        )
        assert lock_a.locked() is True
    finally:
        lock_a.release()
        reg._fallback_by_loop.clear()
        reg._fallback_locks.clear()


@pytest.mark.asyncio
async def test_fallback_by_loop_reclaims_closed_loops():
    """#1681：已关闭 loop 的死桶被扫除（is_closed 显式回收 + WeakKeyDictionary GC 回收）。"""
    reg = SessionLockRegistry()
    holder: dict = {}

    def _worker():
        async def _inner():
            holder["lock"] = reg._fallback_lock("s-1681-dead")

        loop_b = asyncio.new_event_loop()
        holder["loop"] = loop_b  # 强引用：排除「GC 自动回收」造成的空转通过
        try:
            loop_b.run_until_complete(_inner())
        finally:
            loop_b.close()

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert holder.get("lock") is not None
    # 已关闭但被强引用：仍在分桶表内（等待显式扫除）
    assert holder["loop"] in reg._fallback_by_loop

    # loop A 侧任意一次取锁即触发死桶扫除
    reg._fallback_lock("s-1681-probe")
    assert holder["loop"] not in reg._fallback_by_loop, (
        "已关闭 loop 的死桶未被扫除 —— 分桶表无界增长（#1681）"
    )
    reg._fallback_by_loop.clear()
    reg._fallback_locks.clear()
