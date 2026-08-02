import json
import pytest
from app.services.session_data import MemorySessionStore
from app.services.session_data_protocol import SessionRefDataResult, SessionStoreProtocol


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

    # Set metadata with owner token
    store._map_state[sid] = {"owner_token": "secret_token_123"}

    ref_id = await store.store(sid, {"hello": "world"})

    # Valid token
    res1 = await store.get_ref_data(sid, ref_id, owner_token="secret_token_123")
    assert res1.success is True
    assert res1.data == {"hello": "world"}

    # Invalid token
    res2 = await store.get_ref_data(sid, ref_id, owner_token="wrong_token")
    assert res2.success is False
    assert res2.error_type == "PermissionDenied"
