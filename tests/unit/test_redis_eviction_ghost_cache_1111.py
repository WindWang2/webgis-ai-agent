"""#1111: Redis capacity eviction must invalidate the in-process derived caches.

RedisSessionStore.store() evicts the oldest refs beyond capacity. Before the
fix it only dropped ref_payload_cache entries — spatial_index_cache and
tile_lru_cache kept serving the evicted ref's STRtree/MVT bytes, so tile and
feature endpoints returned ghost data for refs no longer in Redis (parity
gap vs the Memory backend eviction path).
"""
import pytest

import fakeredis.aioredis

import app.services.mvt as mvt_module
from app.services.session_data_redis import RedisSessionStore


def _store(capacity: int = 3) -> RedisSessionStore:
    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
        capacity=capacity,
    )


@pytest.mark.asyncio
async def test_redis_eviction_invalidates_spatial_and_tile_caches(monkeypatch):
    store = _store(capacity=3)
    sid = "sess-1111"

    spatial_calls: list[tuple[str, str]] = []
    tile_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        mvt_module.spatial_index_cache,
        "invalidate_ref",
        lambda s, r: spatial_calls.append((s, r)) or True,
    )
    monkeypatch.setattr(
        mvt_module.tile_lru_cache,
        "invalidate_ref",
        lambda s, r: tile_calls.append((s, r)) or 1,
    )

    refs = [
        await store.store(sid, {"type": "FeatureCollection", "features": [], "i": i})
        for i in range(3)
    ]
    assert not spatial_calls and not tile_calls, (
        "staying under capacity must not invalidate anything"
    )

    # 4th store overflows capacity → oldest ref (refs[0]) is evicted.
    await store.store(sid, {"type": "FeatureCollection", "features": [], "i": 3})

    assert (sid, refs[0]) in spatial_calls, (
        f"evicted ref's spatial index survived: spatial_calls={spatial_calls}"
    )
    assert (sid, refs[0]) in tile_calls, (
        f"evicted ref's tile cache survived: tile_calls={tile_calls}"
    )
    # Only the oldest ref is evicted — the newly stored ref must NOT be in the
    # invalidation set, and mid-recency refs survive.
    evicted_spatial = {r for _, r in spatial_calls}
    assert evicted_spatial == {refs[0]}, f"unexpected invalidation set: {spatial_calls}"


@pytest.mark.asyncio
async def test_redis_eviction_ghost_data_regression():
    """End-to-end ghost-data guard: after eviction, the authoritative payload
    is gone from Redis AND the derived tile/index caches no longer hold the
    evicted ref (they would serve 200s from stale bytes otherwise)."""
    from app.services.mvt import spatial_index_cache, tile_lru_cache

    store = _store(capacity=2)
    sid = "sess-1111-ghost"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"n": 1},
                "geometry": {"type": "Point", "coordinates": [116.0, 39.9]},
            }
        ],
    }
    r1 = await store.store(sid, fc)
    # Pre-populate the derived caches so the post-eviction assertions are
    # discriminative (an unfixed eviction path leaves these entries behind).
    def _seed_spatial():
        from app.services.mvt import build_spatial_index_entry
        return build_spatial_index_entry((sid, r1), fc)

    spatial_index_cache.get_or_build((sid, r1), _seed_spatial)
    tile_lru_cache.put((sid, r1, 5, 10, 10), b"stale-gzip-bytes")
    assert spatial_index_cache.invalidate_ref(sid, r1) is True, (
        "fixture error: spatial index entry was not seeded"
    )
    spatial_index_cache.get_or_build((sid, r1), _seed_spatial)
    assert tile_lru_cache.invalidate_ref(sid, r1) == 1, (
        "fixture error: tile bytes were not seeded"
    )
    tile_lru_cache.put((sid, r1, 5, 10, 10), b"stale-gzip-bytes")

    await store.store(sid, fc)
    await store.store(sid, fc)  # evicts r1

    # Authoritative payload gone.
    assert await store.get(sid, r1) is None
    # Derived caches also dropped (real singletons — no monkeypatch).
    assert spatial_index_cache.invalidate_ref(sid, r1) is False, (
        "spatial index entry for evicted ref still present (ghost STRtree)"
    )
    assert tile_lru_cache.invalidate_ref(sid, r1) == 0, (
        "tile bytes for evicted ref still present (ghost tiles)"
    )


@pytest.mark.asyncio
async def test_memory_backend_eviction_parity():
    """Parity: the Memory backend eviction path invalidates the same two
    caches (existing behavior, guards the contract from both sides)."""
    from app.services.mvt import spatial_index_cache, tile_lru_cache
    from app.services.session_data import MemorySessionStore

    store = MemorySessionStore(capacity=2)
    sid = "sess-1111-parity"
    fc = {"type": "FeatureCollection", "features": []}
    r1 = await store.store(sid, fc)
    await store.store(sid, fc)
    await store.store(sid, fc)  # evicts r1

    assert spatial_index_cache.invalidate_ref(sid, r1) is False
    assert tile_lru_cache.invalidate_ref(sid, r1) == 0
