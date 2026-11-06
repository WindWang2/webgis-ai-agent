"""#1929 S50-005: shared durable dedupe and non-layer context references."""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest

from app.services.session_context_refs import store_context_payload
from app.services.session_data import MemorySessionStore


@pytest.fixture(autouse=True)
def isolated_lock(monkeypatch):
    import app.services.session_context_refs as offloads
    from app.services.distributed_lock import SessionLockRegistry
    registry = SessionLockRegistry()
    monkeypatch.setattr(registry, "_get_client", lambda: None)
    monkeypatch.setattr(registry, "_is_redis_configured", lambda: False)
    monkeypatch.setattr(offloads, "session_lock_registry", registry)
    return registry


def _hash(text="same result"):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.asyncio
async def test_memory_dedup_survives_new_caller_and_stays_out_of_inventory():
    from app.lib.runtime.ref_capture import capture_created_refs
    store = MemorySessionStore()
    layer = await store.store("session", {"layer": "public"}, prefix="data")
    with capture_created_refs() as produced:
        first = await store_context_payload(store, "session", {"result": "private"}, _hash())
        second = await store_context_payload(store, "session", {"result": "private"}, _hash())
    assert first == second and first.startswith("ref:contextoffload-")
    assert await store.get("session", first) == {"result": "private"}
    assert await store.list_refs("session") == {layer: ""}
    assert (await store.get_session_metadata("session"))["list_refs"] == {layer: ""}
    assert produced.snapshot() == []


@pytest.mark.asyncio
async def test_deleted_cached_payload_is_regenerated_instead_of_reusing_dead_ticket():
    store = MemorySessionStore()
    first = await store_context_payload(store, "session", {"result": "private"}, _hash())
    assert await store.delete_ref("session", first)
    second = await store_context_payload(store, "session", {"result": "private"}, _hash())
    assert second and second != first and await store.ref_exists("session", second)


@pytest.mark.asyncio
async def test_content_hash_aliases_are_scoped_to_the_session():
    store = MemorySessionStore()
    left = await store_context_payload(store, "left", {"data": 1}, _hash())
    right = await store_context_payload(store, "right", {"data": 1}, _hash())
    assert left != right
    assert await store.get("right", left) is None


@pytest.mark.asyncio
async def test_failed_alias_publication_cleans_private_candidate(monkeypatch):
    store = MemorySessionStore()
    monkeypatch.setattr(store, "set_alias_if_absent", AsyncMock(return_value=False))
    assert await store_context_payload(store, "session", {"data": 1}, _hash()) is None
    assert not store._store.get("session")


@pytest.mark.asyncio
async def test_two_redis_workers_share_one_hash_ticket_and_metadata_hides_it(monkeypatch, isolated_lock):
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore
    server = fakeredis.FakeServer()
    clients = [fakeredis.aioredis.FakeRedis(server=server) for _ in range(3)]
    monkeypatch.setattr(isolated_lock, "_get_client", lambda: clients[-1])
    monkeypatch.setattr(isolated_lock, "_is_redis_configured", lambda: True)
    stores = []
    for client in clients[:2]:
        store = RedisSessionStore("redis://fake-context-review/0")
        store._r = client
        async def connected(client=client):
            return client
        store._ensure_connected = connected
        stores.append(store)
    try:
        refs = await asyncio.gather(*[
            store_context_payload(store, "session", {"same": "payload"}, _hash()) for store in stores
        ])
        assert refs[0] == refs[1] and refs[0].startswith("ref:contextoffload-")
        # Fresh caller/backend state has no process-local dedupe memory.
        assert await store_context_payload(stores[1], "session", {"same": "payload"}, _hash()) == refs[0]
        assert await clients[0].zcard(stores[0]._refs_order_key("session")) == 1
        assert await stores[0].list_refs("session") == {}
        assert (await stores[1].get_session_metadata("session"))["list_refs"] == {}
        assert await stores[1].get("session", refs[0]) == {"same": "payload"}
        # Expiration removes the data key while alias/index metadata may remain.
        await clients[0].delete(stores[0]._data_key("session", refs[0]))
        replacement = await store_context_payload(stores[1], "session", {"same": "payload"}, _hash())
        assert replacement and replacement != refs[0]
        assert await stores[0].ref_exists("session", replacement)
    finally:
        for client in clients:
            await client.aclose()
