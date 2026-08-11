"""Concurrency & multi-worker safety tests (@pytest.mark.cartography).

Validates the stability acceptance criteria:
- Same-session MapSpec mutations serialize (no lost update under concurrency).
- Concurrent different-session mutations do not contaminate each other.
- The per-session distributed lock (Redis in prod, in-process fallback in tests)
  provides mutual exclusion; the in-process asyncio.Lock serializes coroutines.

Cross-pod (two-process) mutual exclusion is provided by the Redis lock and is
documented in the multi-worker ADR; these tests cover the in-process semantics
that the lock preserves under concurrent coroutines.
"""
import asyncio
import shutil
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"conc-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_concurrent_same_session_mutations_serialize_no_lost_update(clean_session):
    """N concurrent layer upserts on the SAME session must all land — no lost
    update. Without per-session serialization the read-modify-write would lose
    layers (last-writer-wins on a stale read)."""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())

    n = 12

    async def upsert(i):
        layer = {
            "id": f"L{i}", "source": f"s{i}", "type": "circle",
            "paint": {"circle-color": "#ff0000"},
        }
        res = await engine.apply_mutation(
            clean_session,
            UpsertLayerIntent(layer=layer, source_data={"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, i]},
                 "properties": {"v": i}}
            ]}),
        )
        assert not res.is_error, f"upsert {i} failed: {res.error_msg}"

    await asyncio.gather(*(upsert(i) for i in range(n)))

    # All n layers must be present — serialization prevented lost updates.
    from app.services.mapspec.store import mapspec_store_instance
    final = await mapspec_store_instance.get_mapspec(clean_session)
    ids = {layer["id"] for layer in final["layers"]}
    missing = {f"L{i}" for i in range(n)} - ids
    assert not missing, f"lost updates — missing layers: {sorted(missing)}"


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_concurrent_different_sessions_no_contamination():
    """Two sessions mutated concurrently must not pollute each other's MapSpec."""
    sessions = [f"conc-iso-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    for s in sessions:
        await session_data_manager.clear_session(s)
    try:
        engine = MapSpecLifecycleEngine()

        async def seed(sid, layer_id):
            await engine.apply_mutation(sid, InitProjectIntent())
            res = await engine.apply_mutation(
                sid,
                UpsertLayerIntent(
                    layer={"id": layer_id, "source": "s", "type": "circle",
                           "paint": {"circle-color": "#00f"}},
                    source_data={"type": "FeatureCollection", "features": [
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                         "properties": {}}
                    ]},
                ),
            )
            assert not res.is_error

        await asyncio.gather(
            seed(sessions[0], "only-in-A"),
            seed(sessions[1], "only-in-B"),
        )

        from app.services.mapspec.store import mapspec_store_instance
        a = await mapspec_store_instance.get_mapspec(sessions[0])
        b = await mapspec_store_instance.get_mapspec(sessions[1])
        a_ids = {lyr["id"] for lyr in a["layers"]}
        b_ids = {lyr["id"] for lyr in b["layers"]}
        assert a_ids == {"only-in-A"}, f"session A contaminated: {a_ids}"
        assert b_ids == {"only-in-B"}, f"session B contaminated: {b_ids}"
    finally:
        for s in sessions:
            await session_data_manager.clear_session(s)
            d = BASE_STORAGE_DIR / s
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_distributed_lock_falls_back_in_process_when_no_redis():
    """With USE_REDIS=false (tests), the lock registry uses in-process locks.
    Verify the registry still grants a working async context manager that
    serializes — i.e. the fallback path is functional, not a no-op."""
    from app.services.distributed_lock import SessionLockRegistry

    reg = SessionLockRegistry()  # fresh; USE_REDIS=false → in-process fallback
    order = []

    async def hold(name, delay):
        async with reg.lock("sess-x"):
            order.append(f"{name}-enter")
            await asyncio.sleep(delay)
            order.append(f"{name}-exit")

    await asyncio.gather(hold("a", 0.02), hold("b", 0.01))
    # Serialized: each enter is followed by its own exit before the other enters.
    a_idx = order.index("a-enter")
    assert order[a_idx + 1] == "a-exit"
    assert "b-enter" in order[order.index("a-exit") + 1:]
