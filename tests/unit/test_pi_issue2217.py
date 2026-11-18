"""#2217: clear_session must invalidate the shared history generation for
other replicas — otherwise a pod holding the session keeps serving the
deleted conversation from its local cache (hist_gen unchanged)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chat.execution_engine as ee
from app.services.chat.execution_engine import ChatExecutionEngine


def _make_engine(monkeypatch, *, hist_client):
    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    engine._clearing_sessions = set()
    engine._clear_quiesce_timeout = 0.1
    engine._sessions = {"sid-1": [{"role": "user", "content": "stale"}]}
    engine._session_locks = {}
    engine._session_lock_last_used = {}
    engine._session_owner_tokens = {"sid-1": "tok"}
    engine._session_hist_gen = {"sid-1": 4}
    engine._hist_redis = hist_client
    engine.catalog = None
    engine.cancel_inflight_turn = AsyncMock()

    db = MagicAsyncCtx(None)
    monkeypatch.setattr(ee, "async_db_session", lambda: db)
    monkeypatch.setattr(
        ee, "AsyncHistoryService",
        lambda db: SimpleNamespace(delete_session=AsyncMock(return_value=True)),
    )
    sdm = SimpleNamespace(
        set_session_clearing=AsyncMock(),
        unset_session_clearing=AsyncMock(),
        clear_session=AsyncMock(),
    )
    monkeypatch.setattr(ee, "session_data_manager", sdm)
    # clear_session imports these locally — patch the source modules.
    import app.services.chat.planner as planner_mod
    import app.services.chat.context.layer_schema as layer_schema_mod
    import app.services.planning.store as plan_store_mod

    monkeypatch.setattr(planner_mod, "clear_plan", lambda sid: None)
    monkeypatch.setattr(plan_store_mod.plan_store, "clear", AsyncMock())
    monkeypatch.setattr(layer_schema_mod, "clear_layer_schema_cache", lambda sid: None)
    return engine, sdm


class MagicAsyncCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakeRedis:
    """Minimal async redis client: incr/expire/get on hist_gen keys."""

    def __init__(self):
        self.store = {"webgis:chat:hist_gen:sid-1": 4}
        self.incr_calls = []
        self.expire_calls = []

    async def incr(self, key):
        self.incr_calls.append(key)
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        self.expire_calls.append((key, seconds))
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.mark.asyncio
async def test_clear_session_bumps_shared_history_generation(monkeypatch):
    redis = _FakeRedis()
    engine, _ = _make_engine(monkeypatch, hist_client=redis)

    assert await engine.clear_session("sid-1", user_id="u1") is True

    assert redis.incr_calls == ["webgis:chat:hist_gen:sid-1"]
    assert ("webgis:chat:hist_gen:sid-1", 86400) in redis.expire_calls
    # Local cache entry is aligned with the bumped generation; the in-memory
    # session itself is gone.
    assert engine._sessions == {}
    assert engine._session_hist_gen["sid-1"] == 5


@pytest.mark.asyncio
async def test_clear_session_without_redis_drops_local_generation(monkeypatch):
    engine, _ = _make_engine(monkeypatch, hist_client=False)
    assert await engine.clear_session("sid-1", user_id="u1") is True
    # Redis unavailable: no stale generation may survive locally — the next
    # _get_or_create_session reload must not compare against a dead epoch.
    assert "sid-1" not in engine._session_hist_gen


@pytest.mark.asyncio
async def test_failed_delete_does_not_bump_generation(monkeypatch):
    redis = _FakeRedis()
    engine, _ = _make_engine(monkeypatch, hist_client=redis)
    monkeypatch.setattr(
        ee, "AsyncHistoryService",
        lambda db: SimpleNamespace(delete_session=AsyncMock(return_value=False)),
    )
    assert await engine.clear_session("sid-1", user_id="u1") is False
    assert redis.incr_calls == []
    assert engine._sessions["sid-1"] == [{"role": "user", "content": "stale"}]
