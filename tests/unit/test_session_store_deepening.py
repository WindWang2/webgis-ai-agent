import json
from unittest.mock import AsyncMock
import pytest
from app.services.session_data import MemorySessionStore
from app.services.session_data_protocol import SessionRefDataResult, SessionStoreProtocol
from app.services.session_data_redis import RedisSessionStore


@pytest.mark.asyncio
async def test_memory_session_store_get_ref_data_basic():
    store: SessionStoreProtocol = MemorySessionStore(capacity=10)
    sid = "sess-test-ref-data-1"

    # Store raw json string
    ref_id = await store.store(sid, json.dumps({"type": "FeatureCollection", "features": []}))
    await store.set_alias(sid, ref_id, "layer_alias_1")

    # Fetch by ref_id
    res1: SessionRefDataResult = await store.get_ref_data(sid, ref_id)
    assert res1.success is True
    assert res1.data == {"type": "FeatureCollection", "features": []}

    # Fetch by alias
    res2: SessionRefDataResult = await store.get_ref_data(sid, "layer_alias_1")
    assert res2.success is True
    assert res2.data == {"type": "FeatureCollection", "features": []}

    # Non-existent ref
    res3: SessionRefDataResult = await store.get_ref_data(sid, "non_existent_ref")
    assert res3.success is False
    assert res3.error_type == "NotFound"


@pytest.mark.asyncio
async def test_memory_session_store_get_ref_data_owner_token():
    store = MemorySessionStore(capacity=10)
    sid = "sess-test-ref-token-1"

    # Use public set_map_state interface
    await store.set_map_state(sid, "owner_token", "secret_token_123")

    ref_id = await store.store(sid, {"hello": "world"})

    # Valid token
    res1 = await store.get_ref_data(sid, ref_id, owner_token="secret_token_123")
    assert res1.success is True
    assert res1.data == {"hello": "world"}

    # Invalid token
    res2 = await store.get_ref_data(sid, ref_id, owner_token="wrong_token")
    assert res2.success is False
    assert res2.error_type == "PermissionDenied"


@pytest.mark.asyncio
async def test_redis_session_store_get_ref_data_fallback():
    redis_store = RedisSessionStore("redis://localhost:6379/0")

    # Mock get_session_metadata and get
    redis_store.get_session_metadata = AsyncMock(return_value={"map_state": {}})
    redis_store.get = AsyncMock(return_value={"status": "ok"})

    res = await redis_store.get_ref_data("sess_redis_1", "ref:data-123")
    assert res.success is True
    assert res.data == {"status": "ok"}
