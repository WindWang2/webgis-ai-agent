"""V5-C/D/E: ref lifecycle contract, cache dependency matrix, content revision.

V5-C — the single invalidation contract: every backend write path drops ALL
derived projections (reason-tagged).
V5-D — the dependency matrix: authoritative payload change ⇒ the derived
states below MUST be invalidated (parametrized, so a new write path that
forgets one fails here — the #1111 class).
V5-E — content revision: same ref_id + overwrite ⇒ revision bumps; descriptor
reads carry the live revision; revision survives backend parity.
"""
import pytest

import fakeredis.aioredis

from app.services.mvt import spatial_index_cache, tile_lru_cache
from app.services.ref_lifecycle import (
    RefInvalidationReason,
    invalidate_ref_caches,
)
from app.services.session_data import MemorySessionStore
from app.services.session_data_redis import RedisSessionStore

_FC_V1 = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}}
    ],
}
_FC_V2 = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}},
        {"type": "Feature", "properties": {"n": 2},
         "geometry": {"type": "Point", "coordinates": [116.01, 39.91]}},
        {"type": "Feature", "properties": {"n": 3},
         "geometry": {"type": "Point", "coordinates": [116.02, 39.92]}},
    ],
}


def _seed_derived(sid: str, ref_id: str, with_payload_cache: bool = True) -> None:
    """Populate every derived projection for (sid, ref)."""
    def _build():
        from app.services.mvt import build_spatial_index_entry
        return build_spatial_index_entry((sid, ref_id), _FC_V1)

    spatial_index_cache.get_or_build((sid, ref_id), _build)
    tile_lru_cache.put((sid, ref_id, 5, 10, 10), b"stale-bytes")
    if with_payload_cache:
        from app.services.ref_payload_cache import ref_payload_cache
        ref_payload_cache.put(sid, ref_id, {"cached": True}, approx_bytes=64)


def _assert_all_invalidated(sid: str, ref_id: str, with_payload_cache: bool = True) -> None:
    """V5-D matrix: no derived projection may outlive its authoritative ref."""
    assert spatial_index_cache.invalidate_ref(sid, ref_id) is False, "ghost spatial index"
    assert tile_lru_cache.invalidate_ref(sid, ref_id) == 0, "ghost tiles"
    if with_payload_cache:
        from app.services.ref_payload_cache import ref_payload_cache
        assert ref_payload_cache.get(sid, ref_id) is None, "ghost payload cache"


# ─── V5-C: contract unit semantics ─────────────────────────────────────────


def test_invalidate_reasons_are_enumerable_and_complete():
    expected = {"OVERWRITE", "DELETE", "EVICT", "EXPIRE", "ROLLBACK", "REPLACE"}
    assert {r.value for r in RefInvalidationReason} == expected


def test_contract_covers_all_three_caches_per_ref():
    sid, ref = "sess-v5c", "ref:v5c-1"
    _seed_derived(sid, ref)
    n = invalidate_ref_caches(sid, [ref], reason=RefInvalidationReason.OVERWRITE)
    assert n == 1
    _assert_all_invalidated(sid, ref)


# ─── V5-D: write paths satisfy the dependency matrix ───────────────────────


@pytest.mark.asyncio
async def test_memory_overwrite_invalidates_every_projection():
    store = MemorySessionStore(capacity=10)
    sid = "sess-v5d-m"
    ref = await store.store(sid, _FC_V1)
    _seed_derived(sid, ref, with_payload_cache=False)
    ok = await store.overwrite(sid, ref, _FC_V2)
    assert ok is True
    _assert_all_invalidated(sid, ref, with_payload_cache=False)
    # Authoritative payload IS the new one.
    assert await store.get(sid, ref) == _FC_V2


@pytest.mark.asyncio
async def test_memory_delete_invalidates_every_projection():
    store = MemorySessionStore(capacity=10)
    sid = "sess-v5d-mdel"
    ref = await store.store(sid, _FC_V1)
    _seed_derived(sid, ref, with_payload_cache=False)
    ok = await store.delete_ref(sid, ref)
    assert ok is True
    _assert_all_invalidated(sid, ref, with_payload_cache=False)


@pytest.mark.asyncio
async def test_redis_eviction_invalidates_every_projection():
    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
        capacity=2,
    )
    sid = "sess-v5d-r"
    r1 = await store.store(sid, _FC_V1)
    r2 = await store.store(sid, _FC_V1)
    _seed_derived(sid, r1)
    await store.store(sid, _FC_V1)  # evicts r1
    assert await store.get(sid, r1) is None
    _assert_all_invalidated(sid, r1)
    # Survivor untouched.
    assert await store.get(sid, r2) == _FC_V1


@pytest.mark.asyncio
async def test_redis_overwrite_and_delete_invalidates_every_projection():
    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )
    sid = "sess-v5d-ro"
    ref = await store.store(sid, _FC_V1)
    _seed_derived(sid, ref)
    await store.overwrite(sid, ref, _FC_V2)
    _assert_all_invalidated(sid, ref)

    ref2 = await store.store(sid, _FC_V1)
    _seed_derived(sid, ref2)
    await store.delete_ref(sid, ref2)
    _assert_all_invalidated(sid, ref2)


# ─── V5-E: content revision (S5 overwrite / S7 rollback) ───────────────────


@pytest.mark.asyncio
async def test_memory_revision_bumps_on_overwrite_and_descriptor_stamps():
    """S5: overwrite the same ref — descriptor consumers must see revision go
    1 → 2, so tile URLs change and the browser cannot keep serving stale tiles."""
    store = MemorySessionStore(capacity=10)
    sid = "sess-v5e-m"
    ref = await store.store(sid, _FC_V1)

    d1 = await store.get_ref_descriptor(sid, ref)
    assert d1 is not None and d1.get("content_revision") == 1

    await store.overwrite(sid, ref, _FC_V2)
    d2 = await store.get_ref_descriptor(sid, ref)
    assert d2 is not None
    assert d2.get("content_revision") == 2, (
        "overwrite must bump the content revision (same ref identity, new content)"
    )
    assert d2.get("feature_count") == 3, "descriptor must reflect the NEW payload"


@pytest.mark.asyncio
async def test_memory_rollback_restore_bumps_revision():
    """S7: rollback = overwrite with an older payload — the revision STILL
    increments (content changed back), so tile URLs change and stale browser
    tiles are abandoned."""
    store = MemorySessionStore(capacity=10)
    sid = "sess-v5e-mrb"
    ref = await store.store(sid, _FC_V2)
    await store.overwrite(sid, ref, _FC_V1)  # "rollback"
    d = await store.get_ref_descriptor(sid, ref)
    assert d is not None and d.get("content_revision") == 2


@pytest.mark.asyncio
async def test_redis_revision_bumps_on_overwrite():
    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )
    sid = "sess-v5e-r"
    ref = await store.store(sid, _FC_V1)
    d1 = await store.get_ref_descriptor(sid, ref)
    assert d1 is not None and d1.get("content_revision") == 1

    await store.overwrite(sid, ref, _FC_V2)
    d2 = await store.get_ref_descriptor(sid, ref)
    assert d2 is not None and d2.get("content_revision") == 2


@pytest.mark.asyncio
async def test_revision_is_per_ref_isolated():
    store = MemorySessionStore(capacity=10)
    sid = "sess-v5e-iso"
    ref_a = await store.store(sid, _FC_V1)
    ref_b = await store.store(sid, _FC_V1)
    await store.overwrite(sid, ref_a, _FC_V2)
    da = await store.get_ref_descriptor(sid, ref_a)
    db = await store.get_ref_descriptor(sid, ref_b)
    assert da["content_revision"] == 2
    assert db["content_revision"] == 1, "revision must be per-ref, not per-session"


@pytest.mark.asyncio
async def test_backend_parity_revisions_match_semantics():
    """Memory and Redis revisions agree on the contract: store→1,
    overwrite→+1, delete→gone."""
    m = MemorySessionStore(capacity=10)
    r = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )
    for store, sid in ((m, "s-parity-m"), (r, "s-parity-r")):
        ref = await store.store(sid, _FC_V1)
        d1 = await store.get_ref_descriptor(sid, ref)
        assert d1["content_revision"] == 1
        await store.overwrite(sid, ref, _FC_V2)
        d2 = await store.get_ref_descriptor(sid, ref)
        assert d2["content_revision"] == 2
        await store.delete_ref(sid, ref)
        assert await store.get_ref_descriptor(sid, ref) is None


# ─── V5-E frontend seam: tile URL changes with revision ────────────────────


def test_tile_url_builder_appends_revision_when_present():
    """Guard the TS builder's contract from the Python suite (vitest covers
    behavior; this pins the source-level contract for the review gate)."""
    src = open("frontend/lib/map-kit/tile-url.ts").read()
    assert "v=${contentRevision}" in src
    assert "session_id=${sessionId}" in src
    # Revision only appended when present/positive (pre-V5 URLs stay stable).
    assert "contentRevision <= 0" in src
