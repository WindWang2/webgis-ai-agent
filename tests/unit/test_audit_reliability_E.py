"""Category-E audit fixes: #745-#752 regression tests."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_745_lock_client_has_socket_timeouts(monkeypatch):
    """The distributed-lock Redis client must be bounded (socket_timeout <=
    acquire deadline) so a half-open connection degrades to the in-process
    fallback instead of hanging every MapSpec mutation."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:16379/0")
    monkeypatch.setenv("USE_REDIS", "true")
    from app.services import distributed_lock as dl

    reg = dl.session_lock_registry
    reg._client = None      # force rebuild with the new kwargs
    reg._bound_loop = None
    reg._last_check_s = 0.0  # clear the 60s re-verify gate
    client = reg._get_client()
    assert client is not None
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs.get("socket_timeout") is not None and kwargs["socket_timeout"] <= 10.0
    assert kwargs.get("socket_connect_timeout") is not None


@pytest.mark.asyncio
async def test_746_rollback_missing_blob_overwrites_nothing(tmp_path):
    """A pruned blob mid-set must abort BEFORE the first overwrite — the old
    loop restored N-1 refs and only then reported missing_blobs."""
    import json
    import uuid

    from app.services.mapspec import checkpoint as cp
    from app.services.session_data import session_data_manager

    sid = f"ck746-{uuid.uuid4().hex[:6]}"
    session_dir = tmp_path / sid
    ckpt_dir = session_dir / "checkpoints" / "ck1"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "mapspec.json").write_text(json.dumps({"version": "1.0", "layers": []}))
    (ckpt_dir / "descriptor.json").write_text(json.dumps(
        {"refs": {"ref:a": "hash-a", "ref:b": "hash-b"}, "mode": "full"}))

    # seed live refs the way a pre-checkpoint session would hold them
    session_data_manager._store.setdefault(sid, {})["ref:a"] = {"marker": "original-a"}
    session_data_manager._store[sid]["ref:b"] = {"marker": "original-b"}
    assert await session_data_manager.get(sid, "ref:a") == {"marker": "original-a"}

    res = await cp.rollback(session_dir, "ck1", session_data_manager)
    assert res.get("success") is False
    assert "missing blobs" in res.get("message", "")
    a = await session_data_manager.get(sid, "ref:a")
    b = await session_data_manager.get(sid, "ref:b")
    assert a == {"marker": "original-a"}, "partial restore must not happen (#746)"
    assert b == {"marker": "original-b"}
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_747_missing_token_fails_closed(monkeypatch):
    """A token-bearing conversation + NO token must return an empty context
    (like a mismatch); the engine's internal_ok path still loads."""
    from app.services.history_service_async import AsyncHistoryService

    class _Msg:
        def __init__(self, i, role, content):
            self.id, self.role, self.content = i, role, content
            self.tool_calls = None
            self.tool_call_id = None
            self.reasoning_content = None

    class _Conv:
        id = "s747"
        user_id = None
        owner_token = "real-token"
        messages = [_Msg(1, "user", "hello")]

    svc = AsyncHistoryService(db=object())  # non-None db keeps the call local

    async def fake_loader(session_id, user_id=None):
        return _Conv()

    monkeypatch.setattr(svc, "_get_or_create_with_messages", fake_loader)

    ctx = await svc.load_context("s747", owner_token=None)
    assert ctx.llm_messages == []
    assert ctx.owner_token is None

    ctx_ok = await svc.load_context("s747", owner_token=None, internal_ok=True)
    assert len(ctx_ok.llm_messages) == 1
    assert ctx_ok.owner_token == "real-token"


@pytest.mark.asyncio
async def test_748_rollback_failure_hint_is_honest(monkeypatch):
    """When the rollback write itself fails, correction_hint must say the
    state may be inconsistent instead of the fixed 已回滚 text."""
    from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine as engine
    from app.services.mapspec.lifecycle_engine import UpsertLayerIntent

    async def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(engine, "_rollback_to_snapshot", boom)
    # force the commit path to fail by making layer_upsert ingestion explode
    monkeypatch.setattr(
        "app.services.mapspec.lifecycle_engine.process_layer_ingestion", boom
    )
    res = await engine.apply_mutation(
        "s748",
        UpsertLayerIntent(layer={"id": "L", "source": "s", "type": "circle", "paint": {}},
                          source_data={"type": "FeatureCollection", "features": []}),
    )
    assert res.is_error
    assert "回滚尝试失败" in (res.correction_hint or "")


@pytest.mark.asyncio
async def test_749_get_map_state_returns_private_copy():
    """Mutating the returned map_state must not corrupt stored state."""
    import uuid

    from app.services.session_data import session_data_manager

    sid = f"ms749-{uuid.uuid4().hex[:6]}"
    await session_data_manager.set_map_state(sid, "layers", [{"id": "L1"}])
    state = await session_data_manager.get_map_state(sid)
    state["layers"].append({"id": "HACKED"})
    state["layers"][0]["id"] = "MUTATED"
    fresh = await session_data_manager.get_map_state(sid)
    assert fresh["layers"] == [{"id": "L1"}]
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_750_clearing_marker_roundtrip():
    """set_session_clearing → is_session_clearing True within TTL."""
    import uuid

    from app.services.session_data import session_data_manager

    sid = f"cl750-{uuid.uuid4().hex[:6]}"
    assert await session_data_manager.is_session_clearing(sid) is False
    await session_data_manager.set_session_clearing(sid, ttl_s=30)
    assert await session_data_manager.is_session_clearing(sid) is True
    await session_data_manager.clear_session(sid)


def test_751_rate_limiter_unique_members():
    """Two same-tick admits must both be counted (zadd member collision
    undercounted concurrent requests)."""
    from app.core.rate_limiter import RedisRateLimiter

    members = []

    class _Pipe:
        def zremrangebyscore(self, *a): ...
        def zadd(self, key, mapping):
            members.extend(mapping.keys())
        def zcard(self, key): ...
        def expire(self, key, ttl): ...

    class _Client:
        def pipeline(self): return _Pipe()

    async def run():
        rl = RedisRateLimiter(_Client())
        await rl.is_allowed("k", 10, 60)
        await rl.is_allowed("k", 10, 60)

    asyncio.run(run())
    assert len(members) == 2 and len(set(members)) == 2, members


@pytest.mark.asyncio
async def test_752_clear_session_degrades_on_redis_error(monkeypatch):
    """RedisError during clear_session must not raise (DELETE stays
    idempotent-ish; the cleanup loop continues)."""
    import uuid

    from app.services.session_data_redis import RedisSessionStore
    import redis.asyncio as aioredis

    store = RedisSessionStore.__new__(RedisSessionStore)
    object.__setattr__(store, '_l1', {})

    async def boom():
        raise aioredis.RedisError("black hole")

    monkeypatch.setattr(store, "_ensure_connected", boom)
    await store.clear_session(f"s752-{uuid.uuid4().hex[:6]}")  # must not raise
