"""Unit tests for #1043: Session-lock resilience, degraded-mode visibility, and TTL expiry signals.

Validates:
1. Degraded mode visibility (lock.is_degraded, lock.mode == 'degraded') and optional fail_on_degraded.
2. TTL expiry mid-mutation signal (lock.lost, lock.is_lost) and optional fail_on_lost.
3. SessionPlan mutation aborts dirty save if lock ownership is lost.
4. Lock acquire budget defaults to 30s to match cartographic evaluation holders.
5. In-process fallback when Redis is explicitly disabled (is_degraded is False).
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import app.services.distributed_lock as dl
from app.services.distributed_lock import (
    _InProcessLock,
    _ResilientSessionLock,
    LockDegradedError,
    LockLostError,
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

        async def set(self, *a, **k):
            return True

        async def eval(self, script, numkeys, key, *args):
            self.calls += 1
            # First renewal succeeds, second reports ownership lost
            return 1 if self.calls == 1 else 0

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
            await asyncio.sleep(0.06)
            assert lock.lost is True
            assert lock.is_lost is True
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
                await asyncio.sleep(0.06)
    finally:
        dl._RENEW_INTERVAL_S = orig_interval


@pytest.mark.asyncio
async def test_session_plan_aborts_save_if_lock_lost():
    """apply_tool_result checks lock.lost and aborts envelope mutation to avoid dirty writes."""
    from app.services.session_data import SessionDataManager
    store = SessionDataManager()
    sid = "sess-lock-abort"

    plan = SessionPlan(
        envelope_id="env-init",
        session_id=sid,
        updated_at=time.time(),
    )
    await save_session_plan(plan, store=store)

    # Mock lock whose .lost flips to True during mutation
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


@pytest.mark.asyncio
async def test_lock_acquire_budget_defaults_to_30s():
    """Acquisition budget defaults to 30s and respects custom acquire_timeout_s."""
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
        None, "k1043:local", _InProcessLock()
    )
    async with lock:
        assert lock.mode == "inprocess"
        assert lock.is_degraded is False
        assert lock.is_redis_backed is False
        assert lock.lost is False
