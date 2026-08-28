"""#1043 — session-lock resilience on the #1037 line.

Three red-team findings:
1. Degraded mode (Redis acquire error → in-process fallback) must be observable
   at the caller, and the SessionPlan apply path fails closed on it — two pods
   degrading concurrently must not both mutate the envelope last-write-wins.
2. TTL expiry mid-mutation must be visible (`lock.lost`) so apply paths abort
   the save instead of clobbering whatever the new owner wrote.
3. The 10s hardcoded acquire budget loses deterministically against the
   documented >30s cartographic-evaluation hold; acquirers queueing behind that
   holder class need a matching budget.
"""
import asyncio
import os
import uuid
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-1043-secret-32chars-minimum!!")
os.environ.setdefault("ENV", "development")


# ---------------------------------------------------------------- fakes
class _ConnErrClient:
    """Redis client whose SET fails the way a transient outage does."""

    async def set(self, *a, **k):
        import redis.exceptions
        raise redis.exceptions.ConnectionError("redis down")

    async def eval(self, *a, **k):
        return 1


class _NeverAcquireClient:
    """SET NX always misses — pure contention, never an error."""

    async def set(self, *a, **k):
        return None

    async def eval(self, *a, **k):
        return 1


class _StubLock:
    """Test seam: a session lock whose degraded/lost state is scripted."""

    def __init__(self, *, degraded=False, lost_on_enter=False, lost=False):
        self.degraded = degraded
        self.lost = lost
        self._lost_on_enter = lost_on_enter

    async def __aenter__(self):
        self.lost = self.lost or self._lost_on_enter
        return self

    async def __aexit__(self, *exc):
        return False


class _StubRegistry:
    def __init__(self, lock):
        self._lock = lock
        self.kwargs = None

    def lock(self, session_id, **kwargs):
        self.kwargs = dict(kwargs)
        return self._lock


def _gis(query: str, scope: str = "成都市") -> dict:
    return {
        "query": query,
        "intent": {"scope": {"name": scope}, "task": "分布"},
        "data_requirements": [{"capability": "heatmap_data"}],
        "analysis_steps": [],
    }


# ---------------------------------------------------------------- finding 1
class TestDegradedMode:
    @pytest.mark.asyncio
    async def test_acquire_error_sets_degraded_flag(self):
        from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock

        lock = _ResilientSessionLock(_ConnErrClient(), "k1043a", _InProcessLock())
        async with lock:
            assert lock.degraded is True, "acquire error must mark the acquisition degraded"
            assert lock._mode == "inprocess"
            assert lock.lost is False

    @pytest.mark.asyncio
    async def test_no_redis_configured_is_not_degraded(self):
        from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock

        lock = _ResilientSessionLock(None, "k1043b", _InProcessLock())
        async with lock:
            assert lock.degraded is False, (
                "in-process mode without Redis configured is the normal "
                "single-pod/test path, not a degraded acquisition"
            )

    @pytest.mark.asyncio
    async def test_apply_tool_result_fails_closed_on_degraded(self, monkeypatch):
        """Two pods degrading concurrently must not both mutate the envelope:
        the apply raises and NOTHING is written."""
        from app.services import session_plan as sp

        reg = _StubRegistry(_StubLock(degraded=True))
        monkeypatch.setattr(sp, "session_lock_registry", reg)
        sid = f"s1043d-{uuid.uuid4().hex[:6]}"
        with pytest.raises(sp.SessionLockDegradedError):
            await sp.apply_tool_result(
                sid, "webgis_map_intent", {"plan": _gis("成都市学校")}, success=True
            )
        assert await sp.load_session_plan(sid) is None, (
            "no envelope may be written while the lock is degraded"
        )
        await sp.session_data_manager.clear_session(sid)

    @pytest.mark.asyncio
    async def test_slot_open_fails_closed_on_degraded(self, monkeypatch):
        from app.services import session_plan as sp

        reg = _StubRegistry(_StubLock(degraded=True))
        monkeypatch.setattr(sp, "session_lock_registry", reg)
        sid = f"s1043e-{uuid.uuid4().hex[:6]}"
        with pytest.raises(sp.SessionLockDegradedError):
            await sp.ensure_session_plan_slot(sid)
        assert await sp.load_session_plan(sid) is None
        await sp.session_data_manager.clear_session(sid)


# ---------------------------------------------------------------- finding 2
class TestLostOwnership:
    @pytest.mark.asyncio
    async def test_renew_refusal_marks_ownership_lost(self, monkeypatch):
        from app.services import distributed_lock as dl

        class _LoseItClient:
            async def set(self, *a, **k):
                return True

            async def eval(self, script, numkeys, key, *args):
                return 0  # renewal refused → someone else owns the key

        monkeypatch.setattr(dl, "_RENEW_INTERVAL_S", 0.01)
        lock = dl._ResilientSessionLock(_LoseItClient(), "k1043f", dl._InProcessLock())
        async with lock:
            await asyncio.sleep(0.05)
            assert lock.lost is True, "refused renewal must expose lost ownership"

    @pytest.mark.asyncio
    async def test_release_token_mismatch_marks_lost(self):
        from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock

        class _NotOwnedClient:
            async def set(self, *a, **k):
                return True

            async def eval(self, *a, **k):
                return 0  # release refused — we no longer own the token

        lock = _ResilientSessionLock(_NotOwnedClient(), "k1043g", _InProcessLock())
        async with lock:
            assert lock.lost is False
        assert lock.lost is True, "token-checked release failure must set lost"

    @pytest.mark.asyncio
    async def test_apply_aborts_save_when_ownership_lost(self, monkeypatch):
        """The save after a lost lock would clobber the new owner's write —
        the apply must raise and leave the stored envelope untouched."""
        from app.services import session_plan as sp

        sid = f"s1043h-{uuid.uuid4().hex[:6]}"
        try:
            await sp.apply_tool_result(
                sid, "webgis_map_intent", {"plan": _gis("成都市学校")}, success=True
            )
            reg = _StubRegistry(_StubLock(lost_on_enter=True))
            monkeypatch.setattr(sp, "session_lock_registry", reg)
            with pytest.raises(sp.SessionLockLostError):
                # Same goal → replace branch, which always ends in a save.
                await sp.apply_tool_result(
                    sid, "webgis_map_intent", {"plan": _gis("成都市学校")}, success=True
                )
            stored = await sp.load_session_plan(sid)
            assert stored.gis_chapter is not None, "stored envelope must be untouched"
        finally:
            await sp.session_data_manager.clear_session(sid)

    @pytest.mark.asyncio
    async def test_supersede_aborts_before_archive_when_ownership_lost(self, monkeypatch):
        """The supersede branch archives the old envelope then saves the new
        one — both writes must be guarded by the ownership check."""
        from app.services import session_plan as sp

        sid = f"s1043i-{uuid.uuid4().hex[:6]}"
        try:
            await sp.apply_tool_result(
                sid, "webgis_map_intent", {"plan": _gis("成都市学校")}, success=True
            )
            old = await sp.load_session_plan(sid)
            reg = _StubRegistry(_StubLock(lost_on_enter=True))
            monkeypatch.setattr(sp, "session_lock_registry", reg)
            with pytest.raises(sp.SessionLockLostError):
                await sp.apply_tool_result(
                    sid, "webgis_map_intent",
                    {"plan": _gis("北京市学校", scope="北京市")}, success=True,
                )
            stored = await sp.load_session_plan(sid)
            assert stored.envelope_id == old.envelope_id, (
                "the current envelope must not be replaced by a lost-owner save"
            )
            archived = await sp.session_data_manager.resolve_alias(
                sid, sp._history_alias(old.envelope_id)
            )
            assert archived == sp._history_alias(old.envelope_id) or archived is None
        finally:
            await sp.session_data_manager.clear_session(sid)

    @pytest.mark.asyncio
    async def test_supersede_save_aborts_when_ownership_lost_during_archive(
        self, monkeypatch
    ):
        """The archive is an await — ownership can be lost across it. The
        new-envelope save must re-check, not trust the branch-entry guard."""
        from app.services import session_plan as sp

        sid = f"s1043o-{uuid.uuid4().hex[:6]}"
        try:
            await sp.apply_tool_result(
                sid, "webgis_map_intent", {"plan": _gis("成都市学校")}, success=True
            )
            old = await sp.load_session_plan(sid)
            stub = _StubLock()
            reg = _StubRegistry(stub)
            monkeypatch.setattr(sp, "session_lock_registry", reg)

            real_archive = sp._archive_envelope

            async def _archive_then_lose(plan, *, store):
                # TTL expires while the archive await is in flight.
                stub.lost = True
                return await real_archive(plan, store=store)

            monkeypatch.setattr(sp, "_archive_envelope", _archive_then_lose)
            with pytest.raises(sp.SessionLockLostError):
                await sp.apply_tool_result(
                    sid, "webgis_map_intent",
                    {"plan": _gis("北京市学校", scope="北京市")}, success=True,
                )
            stored = await sp.load_session_plan(sid)
            assert stored.envelope_id == old.envelope_id, (
                "the new envelope must not be saved after ownership was lost "
                "during the archive"
            )
        finally:
            await sp.session_data_manager.clear_session(sid)


# ---------------------------------------------------------------- finding 3
class TestAcquireBudget:
    @pytest.mark.asyncio
    async def test_acquire_timeout_is_configurable(self):
        from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock

        lock = _ResilientSessionLock(
            _NeverAcquireClient(), "k1043j", _InProcessLock(), acquire_timeout_s=0.05
        )
        with pytest.raises(TimeoutError, match="0.05"):
            await lock.__aenter__()

    def test_registry_passes_budget_through(self):
        from app.services.distributed_lock import SessionLockRegistry

        reg = SessionLockRegistry()  # USE_REDIS=false in tests → in-process
        lock = reg.lock("s1043k", acquire_timeout_s=42.0)
        assert lock._acquire_timeout_s == 42.0

    @pytest.mark.asyncio
    async def test_apply_uses_long_holder_budget(self, monkeypatch):
        """A long cartographic evaluation holds the session lock >30s; the
        apply path's acquire budget must match that holder class or its
        contention retries deterministically fail."""
        from app.services import session_plan as sp
        from app.services.distributed_lock import LONG_HOLDER_ACQUIRE_TIMEOUT_S

        sid = f"s1043l-{uuid.uuid4().hex[:6]}"
        reg = _StubRegistry(_StubLock())
        monkeypatch.setattr(sp, "session_lock_registry", reg)
        try:
            await sp.apply_tool_result(
                sid, "heatmap_data", {"success": True}, success=True
            )
            assert reg.kwargs.get("acquire_timeout_s") == LONG_HOLDER_ACQUIRE_TIMEOUT_S
        finally:
            await sp.session_data_manager.clear_session(sid)

    @pytest.mark.asyncio
    async def test_observation_endpoint_uses_long_holder_budget(self, monkeypatch):
        from app.api.routes import chat as _chat_mod
        from app.services.distributed_lock import LONG_HOLDER_ACQUIRE_TIMEOUT_S

        reg = _StubRegistry(_StubLock())
        monkeypatch.setattr(_chat_mod, "session_lock_registry", reg)
        req = _chat_mod.CartographicRuntimeObservationRequest(
            client_generation=1,
            mapspec_fingerprint="f" * 64,
            layers=[],
            viewport={},
            style_loaded=True,
        )
        # The stub lock never blocks; the fingerprint miss drives the endpoint
        # down the stale-observation rejection branch, which returns normally.
        await _chat_mod.push_cartographic_runtime_observation(
            "s1043m", req, _conv=object()
        )
        assert reg.kwargs.get("acquire_timeout_s") == LONG_HOLDER_ACQUIRE_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_ack_endpoint_uses_long_holder_budget(self, monkeypatch):
        from app.api.routes import chat as _chat_mod
        from app.services.distributed_lock import LONG_HOLDER_ACQUIRE_TIMEOUT_S

        reg = _StubRegistry(_StubLock())
        monkeypatch.setattr(_chat_mod, "session_lock_registry", reg)

        limiter = MagicMock()

        async def _is_allowed(key, max_requests, window_seconds):
            return True

        limiter.is_allowed = _is_allowed

        async def _get_limiter():
            return limiter

        monkeypatch.setattr(_chat_mod, "get_rate_limiter", _get_limiter)
        request = MagicMock()
        request.headers = {}
        request.client = None
        acks = _chat_mod.MapActionAckRequest(acks=[{
            "action_id": "ma-1043", "command": "fly_to", "status": "succeeded",
        }])
        sid = f"s1043n-{uuid.uuid4().hex[:6]}"
        try:
            await _chat_mod.push_map_action_acks(sid, acks, request=request, _conv=object())
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(sid)
        assert reg.kwargs.get("acquire_timeout_s") == LONG_HOLDER_ACQUIRE_TIMEOUT_S
