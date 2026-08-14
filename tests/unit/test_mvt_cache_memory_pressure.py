"""Unit tests for MVT cache memory pressure bounds, lifecycle invalidation, and concurrency contracts."""

import asyncio
import pytest

from app.services.mvt import (
    SingleFlightManager,
    SpatialIndexCache,
    TileLRUCache,
    build_spatial_index_entry,
    spatial_index_cache,
    tile_lru_cache,
)
from app.services.session_data import MemorySessionStore


def _sample_fc(n_features: int = 10, geom_type: str = "Point") -> dict:
    features = []
    for i in range(n_features):
        lon = 116.40 + i * 0.001
        lat = 39.90 + i * 0.001
        if geom_type == "Point":
            coords = [lon, lat]
        elif geom_type == "LineString":
            coords = [[lon, lat], [lon + 0.001, lat + 0.001]]
        elif geom_type == "Polygon":
            coords = [[[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat + 0.001], [lon, lat]]]
        else:
            raise ValueError(geom_type)
        features.append({
            "type": "Feature",
            "geometry": {"type": geom_type, "coordinates": coords},
            "properties": {"id": i, "name": f"f_{i}"},
        })
    return {"type": "FeatureCollection", "features": features}


def test_spatial_index_cache_byte_budget_eviction():
    """SpatialIndexCache evicts entries when max_bytes is exceeded, maintaining hard memory upper bound."""
    # Create cache with tiny byte budget
    cache = SpatialIndexCache(max_refs=100, max_bytes=15000)
    fc1 = _sample_fc(10, "Point")
    fc2 = _sample_fc(10, "Point")
    fc3 = _sample_fc(10, "Point")

    cache.get_or_build(("s1", "r1"), lambda: build_spatial_index_entry(("s1", "r1"), fc1))
    assert cache.total_bytes > 0
    assert cache.get(("s1", "r1")) is not None

    cache.get_or_build(("s1", "r2"), lambda: build_spatial_index_entry(("s1", "r2"), fc2))
    assert cache.get(("s1", "r2")) is not None

    # Adding r3 will exceed 15000 bytes and evict oldest entry r1
    cache.get_or_build(("s1", "r3"), lambda: build_spatial_index_entry(("s1", "r3"), fc3))
    assert cache.total_bytes <= cache._max_bytes
    assert cache.get(("s1", "r3")) is not None
    assert cache.get(("s1", "r1")) is None  # r1 was evicted to enforce byte budget


def test_spatial_index_cache_invalidate_ref_and_session():
    """Invalidating by ref or by session removes entries and correctly updates total_bytes."""
    cache = SpatialIndexCache(max_refs=100, max_bytes=1024 * 1024)
    fc = _sample_fc(5, "LineString")

    cache.get_or_build(("s1", "r1"), lambda: build_spatial_index_entry(("s1", "r1"), fc))
    cache.get_or_build(("s1", "r2"), lambda: build_spatial_index_entry(("s1", "r2"), fc))
    cache.get_or_build(("s2", "r1"), lambda: build_spatial_index_entry(("s2", "r1"), fc))

    assert len(cache._entries) == 3
    initial_bytes = cache.total_bytes
    assert initial_bytes > 0

    # Invalidate single ref
    assert cache.invalidate_ref("s1", "r1") is True
    assert cache.get(("s1", "r1")) is None
    assert cache.total_bytes < initial_bytes
    assert cache.invalidate_ref("s1", "r1") is False

    # Invalidate session
    removed = cache.invalidate_session("s1")
    assert removed == 1  # only r2 was left
    assert cache.get(("s1", "r2")) is None
    assert cache.get(("s2", "r1")) is not None  # s2 unaffected


def test_tile_lru_cache_byte_and_entry_budget():
    """TileLRUCache bounds both max_tiles and max_bytes."""
    cache = TileLRUCache(max_tiles=5, max_bytes=1000)
    for i in range(10):
        key = ("s1", "r1", 1, i, 0)
        cache.put(key, b"x" * 150)

    assert len(cache._cache) <= 5
    assert cache.total_bytes <= 1000
    assert cache.get(("s1", "r1", 1, 0, 0)) is None  # oldest evicted


def test_tile_lru_cache_oversized_tile_bypass():
    """Oversized single tiles (> max_entry_bytes) are bypassed without evicting existing entries."""
    cache = TileLRUCache(max_tiles=100, max_bytes=100_000, max_entry_bytes=1000)
    normal_tile = b"small_tile_payload"
    cache.put(("s1", "r1", 1, 0, 0), normal_tile)
    assert cache.get(("s1", "r1", 1, 0, 0)) == normal_tile

    # Oversized tile: 2000 bytes > max_entry_bytes (1000)
    oversized = b"O" * 2000
    cache.put(("s1", "r1", 1, 1, 0), oversized)
    # Oversized tile not cached
    assert cache.get(("s1", "r1", 1, 1, 0)) is None
    # Existing normal tile was not evicted
    assert cache.get(("s1", "r1", 1, 0, 0)) == normal_tile


def test_tile_lru_cache_invalidate_ref_and_session():
    """Invalidating tile cache by ref and by session frees memory immediately."""
    cache = TileLRUCache(max_tiles=100, max_bytes=100_000)
    cache.put(("s1", "r1", 1, 0, 0), b"tile1")
    cache.put(("s1", "r1", 1, 0, 1), b"tile2")
    cache.put(("s1", "r2", 1, 0, 0), b"tile3")
    cache.put(("s2", "r1", 1, 0, 0), b"tile4")

    assert len(cache._cache) == 4
    init_bytes = cache.total_bytes

    # Invalidate ("s1", "r1")
    removed = cache.invalidate_ref("s1", "r1")
    assert removed == 2
    assert cache.get(("s1", "r1", 1, 0, 0)) is None
    assert cache.get(("s1", "r1", 1, 0, 1)) is None
    assert cache.get(("s1", "r2", 1, 0, 0)) == b"tile3"
    assert cache.total_bytes < init_bytes

    # Invalidate session "s1"
    removed_s1 = cache.invalidate_session("s1")
    assert removed_s1 == 1
    assert cache.get(("s1", "r2", 1, 0, 0)) is None
    assert cache.get(("s2", "r1", 1, 0, 0)) == b"tile4"


@pytest.mark.asyncio
async def test_session_data_overwrite_invalidates_mvt_caches():
    """When a session ref is overwritten, its spatial index and tile cache are 100% invalidated."""
    store = MemorySessionStore()
    session_id = "sess_test_ow"
    fc1 = _sample_fc(10, "Point")
    ref_id = await store.store(session_id, fc1)

    # Populate index and tile cache
    spatial_index_cache.get_or_build((session_id, ref_id), lambda: build_spatial_index_entry((session_id, ref_id), fc1))
    tile_lru_cache.put((session_id, ref_id, 12, 1, 1), b"old_tile_bytes")

    assert spatial_index_cache.get((session_id, ref_id)) is not None
    assert tile_lru_cache.get((session_id, ref_id, 12, 1, 1)) == b"old_tile_bytes"

    # Overwrite ref with new data
    fc2 = _sample_fc(20, "Point")
    updated = await store.overwrite(session_id, ref_id, fc2)
    assert updated is True

    # Contract: 100% invalidated
    assert spatial_index_cache.get((session_id, ref_id)) is None
    assert tile_lru_cache.get((session_id, ref_id, 12, 1, 1)) is None


@pytest.mark.asyncio
async def test_session_data_clear_invalidates_mvt_caches():
    """When a session is cleared, all its spatial index entries and tile cache entries are 100% purged."""
    store = MemorySessionStore()
    session_id = "sess_test_clear"
    fc = _sample_fc(10, "Point")
    ref1 = await store.store(session_id, fc)
    ref2 = await store.store(session_id, fc)

    spatial_index_cache.get_or_build((session_id, ref1), lambda: build_spatial_index_entry((session_id, ref1), fc))
    spatial_index_cache.get_or_build((session_id, ref2), lambda: build_spatial_index_entry((session_id, ref2), fc))
    tile_lru_cache.put((session_id, ref1, 10, 0, 0), b"tile_r1")
    tile_lru_cache.put((session_id, ref2, 10, 0, 0), b"tile_r2")

    assert spatial_index_cache.get((session_id, ref1)) is not None
    assert tile_lru_cache.get((session_id, ref1, 10, 0, 0)) is not None

    await store.clear_session(session_id)

    # Contract: 100% purged
    assert spatial_index_cache.get((session_id, ref1)) is None
    assert spatial_index_cache.get((session_id, ref2)) is None
    assert tile_lru_cache.get((session_id, ref1, 10, 0, 0)) is None
    assert tile_lru_cache.get((session_id, ref2, 10, 0, 0)) is None


@pytest.mark.asyncio
async def test_singleflight_no_unretrieved_exception_leak_on_cancellation():
    """When a single in-flight task is cancelled, it clears inflight and avoids unretrieved exception warnings."""
    sf = SingleFlightManager(max_inflight=512)
    started = asyncio.Event()

    async def slow_work():
        started.set()
        await asyncio.sleep(1.0)
        return b"done"

    key = ("s1", "r1", 1, 0, 0)
    task = asyncio.create_task(sf.run(key, slow_work))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Verify inflight is clean and no hanging future
    assert key not in sf._inflight
    assert key not in sf._waiter_counts
