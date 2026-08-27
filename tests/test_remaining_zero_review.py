"""Shipped-path tests for leftover master zero-review findings (post-#365)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.auth import authorize_session_write, verify_token
from app.models.db_model import User
from app.services.session_data import MemorySessionStore


def test_issue_token_pair_includes_org_id():
    from app.api.routes.auth import _issue_token_pair

    user = User(
        id="u-org",
        username="orguser",
        email="org@example.com",
        role="viewer",
        org_id=7,
        token_version=0,
    )
    pair = _issue_token_pair(user)
    payload = verify_token(pair.access_token)
    assert payload is not None
    assert payload["org_id"] == 7
    assert payload["sub"] == "u-org"


def test_get_client_ip_prefers_x_real_ip_not_leftmost_xff():
    from app.api.routes.auth import _get_client_ip

    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: {
        "x-real-ip": "10.0.0.9",
        "x-forwarded-for": "1.1.1.1, 8.8.8.8, 10.0.0.9",
    }.get(key, default)
    request.client.host = "127.0.0.1"
    assert _get_client_ip(request) == "10.0.0.9"


def test_get_client_ip_uses_last_xff_hop_without_x_real_ip():
    from app.api.routes.auth import _get_client_ip

    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: {
        "x-forwarded-for": "1.1.1.1, 8.8.8.8, 10.0.0.9",
    }.get(key, default)
    request.client.host = "127.0.0.1"
    assert _get_client_ip(request) == "10.0.0.9"


def test_pi_stream_capability_emits_token_only_on_create():
    from app.api.routes.chat import _pi_stream_capability

    created = SimpleNamespace(user_id=None, owner_token="new-secret")
    assert _pi_stream_capability(created, True, None, None) == "new-secret"

    existing = SimpleNamespace(user_id=None, owner_token="victim-secret")
    with pytest.raises(HTTPException) as ei:
        _pi_stream_capability(existing, False, None, None)
    assert ei.value.status_code == 404

    assert _pi_stream_capability(existing, False, None, "victim-secret") is None
    assert authorize_session_write(existing, None, "victim-secret") is True


@pytest.mark.asyncio
async def test_get_or_create_reports_created_flag(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.db_model import Base
    from app.services.history_service_async import AsyncHistoryService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'created.db'}")
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with Session() as db:
            svc = AsyncHistoryService(db)
            first, created = await svc.get_or_create_conversation_with_created("sess-new")
            assert created is True
            assert first.owner_token
            second, created_again = await svc.get_or_create_conversation_with_created("sess-new")
            assert created_again is False
            assert second.owner_token == first.owner_token
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_cleanup_keeps_last_touched_session():
    mgr = MemorySessionStore(capacity=20)
    refs = {}
    for i in range(15):
        refs[f"s{i}"] = await mgr.store(f"s{i}", {"i": i})
    assert await mgr.get("s0", refs["s0"]) == {"i": 0}
    await mgr.cleanup_idle_sessions(max_sessions=12)
    assert await mgr.get("s0", refs["s0"]) == {"i": 0}


@pytest.mark.asyncio
async def test_redis_set_map_state_refreshes_payload_ttl():
    import fakeredis.aioredis

    from app.services.session_data_redis import DATA_TTL, RedisSessionStore

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw, capacity=20)
    ref = await store.store("sess-ttl", {"v": 1})
    data_key = store._data_key("sess-ttl", ref)
    await raw.expire(data_key, 10)
    assert await raw.ttl(data_key) <= 10
    # #730: WRITE paths no longer fan out per-ref payload TTL refreshes
    # (O(refs) SMEMBERS + 2N EXPIREs per viewport push); the READ path is the
    # liveness owner — a get() must refresh the payload TTL.
    await store.set_map_state("sess-ttl", "viewport", {"zoom": 8})
    assert await raw.ttl(data_key) <= 10, "write path must not refresh payload TTL"
    await store.get("sess-ttl", ref)
    ttl = await raw.ttl(data_key)
    assert ttl > 10
    assert ttl >= DATA_TTL - 5


@pytest.mark.asyncio
async def test_redis_cleanup_keeps_viewport_touched_session():
    import fakeredis.aioredis

    from app.services.session_data_redis import RedisSessionStore

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw, capacity=20)
    refs = {}
    for i in range(15):
        refs[f"s{i}"] = await store.store(f"s{i}", {"i": i})
    await store.set_map_state("s0", "viewport", {"zoom": 3})
    await store.cleanup_idle_sessions(max_sessions=12)
    assert await store.get("s0", refs["s0"]) == {"i": 0}


@pytest.mark.asyncio
async def test_dispatch_tool_records_harness_evidence_once(monkeypatch):
    import app.agent_pi_bridge as bridge
    from app.lib.harness.pi_agent_harness import PiAgentHarness
    from app.services.tool_dispatch_service import ToolDispatchResult

    harness = PiAgentHarness(session_id="dup-sess")
    import app.services.cartography_runtime as cartography_runtime
    monkeypatch.setattr(cartography_runtime, "_harness", harness)
    monkeypatch.setattr(
        cartography_runtime, "_harnesses",
        {**getattr(cartography_runtime, "_harnesses", {}), "dup-sess": harness},
    )

    result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={},
        geojson_ref=None,
        raw_result={
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": "fp-1",
            "mutation_revision": 1,
        },
        error_msg=None,
        map_actions=[{
            "action_id": "ma-dup",
            "command": "upsert_layer",
            "requested": {},
            "mapspec_fingerprint": "fp-1",
        }],
    )
    fake_service = MagicMock()
    fake_service.dispatch = AsyncMock(return_value=result)
    monkeypatch.setattr(bridge, "ToolDispatchService", lambda **kw: fake_service)

    fake_registry = MagicMock()
    fake_registry.list_tools = MagicMock(return_value=["webgis_layer_upsert"])
    fake_registry.metadata = MagicMock(return_value={"tier": 1})
    monkeypatch.setattr(bridge, "_tool_registry", fake_registry)

    evals = {"n": 0}

    async def _fake_eval(session_id):
        evals["n"] += 1
        return None

    async def _fake_persist(session_id, event, map_actions):
        return True

    monkeypatch.setattr(bridge, "evaluate_cartographic_session", _fake_eval)
    monkeypatch.setattr(bridge, "_persist_cartographic_harness_context", _fake_persist)

    request = bridge.PiToolRequest(
        toolCallId="tc-dup",
        name="webgis_layer_upsert",
        arguments={},
        sessionId="dup-sess",
    )
    try:
        resp = await bridge.dispatch_tool(request)
        assert not resp.isError
        issued = [e for e in harness.map_actions_issued if e.get("action_id") == "ma-dup"]
        assert len(issued) == 1, issued
        tool_rows = [c for c in harness.tool_calls if c.get("tool_call_id") == "tc-dup"]
        assert len(tool_rows) == 1, harness.tool_calls
        assert evals["n"] == 1
    finally:
        bridge._session_executed_sets.pop("dup-sess", None)
