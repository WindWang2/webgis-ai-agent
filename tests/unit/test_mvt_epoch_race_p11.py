"""P1-1: invalidate/build resurrection race — epoch-guarded derived caches.

The race: SpatialIndexCache.get_or_build runs the heavy build outside the
lock; an overwrite/rollback invalidating the ref DURING the build was a
no-op (entry not yet inserted), and the completed build then inserted ghost
geometry derived from the superseded payload. Same window for tile bytes
(encode/gzip after the index insert, put after gzip).

Fix: per-key generation counters bumped on EVERY invalidation; the index
insert and the conditional tile put validate the captured generation and
refuse (raise / return False) on mismatch — the tile route's existing
RefDataUnavailableError path then refetches fresh data and retries.

Tests here reproduce the race cross-thread (the production interleaving:
encode on a to_thread worker while the event loop thread invalidates) and
would FAIL on the pre-fix code.
"""
import threading

import pytest

from app.services.mvt import (
    RefDataUnavailableError,
    SpatialIndexCache,
    TileLRUCache,
    spatial_index_cache,
    tile_lru_cache,
)

_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"n": i},
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.001 * i, 39.9]}}
        for i in range(2)
    ],
}


def _make_entry(data):
    from app.services.mvt import build_spatial_index_entry
    return build_spatial_index_entry(("k", "k"), data)


# ─── SpatialIndexCache: invalidate during build discards the build ─────────


def test_index_build_staled_by_invalidate_during_build():
    sic = SpatialIndexCache()
    key = ("s-idx", "r-idx")
    calls = []

    def build():
        # Simulate the production interleaving: the authoritative payload is
        # overwritten (invalidated) WHILE the heavy build is running.
        calls.append(1)
        sic.invalidate_ref(*key)
        return _make_entry(_FC)

    with pytest.raises(RefDataUnavailableError, match="staled"):
        sic.get_or_build(key, build)
    assert sic.get(key) is None, "stale build must not be inserted (ghost index)"

    # The refused build does not count: a clean rebuild happens and inserts.
    result = sic.get_or_build(key, lambda: _make_entry(_FC))
    assert result is not None and sic.get(key) is result
    assert len(calls) == 1  # refused build_fn ran once; second build separate


def test_index_build_started_before_invalidate_discarded_cross_thread():
    """Deterministic cross-thread reproduction of the production race:
    build captures the pre-invalidation epoch; invalidation lands mid-build
    from another thread; the completed build must be refused."""
    sic = SpatialIndexCache()
    key = ("s-x", "r-x")
    started = threading.Event()
    release = threading.Event()
    outcome = {}

    def build():
        started.set()
        release.wait(timeout=5)
        return _make_entry(_FC)

    def runner():
        try:
            sic.get_or_build(key, build)
            outcome["result"] = "inserted"
        except RefDataUnavailableError:
            outcome["result"] = "staled"

    t = threading.Thread(target=runner)
    t.start()
    started.wait(timeout=5)
    # Event-loop-thread invalidation lands mid-build (entry absent → the old
    # code's invalidate was a no-op here — exactly the resurrection window).
    sic.invalidate_ref(*key)
    release.set()
    t.join(timeout=5)

    assert outcome["result"] == "staled", (
        f"stale build was accepted: {outcome} — resurrection race NOT fixed"
    )
    assert sic.get(key) is None


def test_index_build_after_invalidation_uses_new_epoch():
    """A build that STARTS after the invalidation must succeed (fresh payload)."""
    sic = SpatialIndexCache()
    key = ("s-new", "r-new")
    sic.invalidate_ref(*key)  # pre-existing invalidation
    entry = sic.get_or_build(key, lambda: _make_entry(_FC))
    assert entry is not None and sic.get(key) is entry


def test_index_invalidate_session_bumps_epochs_for_all_keys():
    sic = SpatialIndexCache()
    k1, k2 = ("s1", "r1"), ("s1", "r2")
    started = threading.Event()
    release = threading.Event()

    def build():
        started.set()
        release.wait(timeout=5)
        return _make_entry(_FC)

    # Collect the outcome via the exception channel
    outcome = {}

    def runner():
        try:
            sic.get_or_build(k1, build)
            outcome["r"] = "inserted"
        except RefDataUnavailableError:
            outcome["r"] = "staled"

    t2 = threading.Thread(target=runner)
    t2.start()
    started.wait(timeout=5)
    sic.invalidate_session("s1")  # no entry exists yet — old code: no-op
    release.set()
    t2.join(timeout=5)
    assert outcome["r"] == "staled", "session invalidation must kill in-flight builds"
    assert sic.get(k2) is None and sic.get(k1) is None


def test_index_clear_wipes_epochs_safe_direction():
    sic = SpatialIndexCache()
    key = ("s-c", "r-c")
    started = threading.Event()
    release = threading.Event()
    outcome = {}

    def build():
        started.set()
        release.wait(timeout=5)
        return _make_entry(_FC)

    def runner():
        try:
            sic.get_or_build(key, build)
            outcome["r"] = "inserted"
        except RefDataUnavailableError:
            outcome["r"] = "staled"

    t = threading.Thread(target=runner)
    t.start()
    started.wait(timeout=5)
    sic.clear()
    release.set()
    t.join(timeout=5)
    # Either outcome is SAFE post-clear (cache is empty either way); assert no
    # entry was inserted from the pre-clear build.
    assert sic.get(key) is None
    assert outcome["r"] in ("staled", "inserted")


# ─── TileLRUCache: conditional put refuses superseded generations ──────────


def test_tile_put_if_current_accepts_and_refuses():
    tlc = TileLRUCache()
    key = ("s-t", "r-t", 3, 4, 4)
    epoch0 = tlc.get_epoch("s-t", "r-t")
    assert tlc.put_if_current(key, b"v1", epoch0) is True
    assert tlc.get(key) == b"v1"

    tlc.invalidate_ref("s-t", "r-t")  # generation bumped (bytes removed)
    assert tlc.get(key) is None
    # The old generation's bytes must be refused (the ghost-tile case).
    assert tlc.put_if_current(key, b"stale", epoch0) is False
    assert tlc.get(key) is None
    # New generation's bytes accepted.
    epoch1 = tlc.get_epoch("s-t", "r-t")
    assert epoch1 != epoch0, "invalidation must change the token"
    assert tlc.put_if_current(key, b"v2", epoch1) is True
    assert tlc.get(key) == b"v2"


def test_tile_invalidate_without_bytes_still_bumps():
    """The core race: no bytes existed at invalidation time (entry not yet
    inserted) — the bump must still reject the in-flight generation."""
    tlc = TileLRUCache()
    tlc.invalidate_ref("s-x", "r-x")  # nothing to remove; old code: pure no-op
    epoch_now = tlc.get_epoch("s-x", "r-x")
    assert epoch_now[1] == 1, "invalidation with no bytes must still bump the per-key generation"
    assert tlc.put_if_current(("s-x", "r-x", 1, 1, 1), b"stale", (0, 0)) is False
    assert tlc.get(("s-x", "r-x", 1, 1, 1)) is None


def test_tile_invalidate_session_and_clear_bump():
    """Session invalidation uses the GLOBAL generation, so ALL outstanding
    captures are refused — including other sessions' (conservative
    over-invalidation; the only cost is one re-encode, never staleness).
    Fresh captures taken AFTER the bump succeed."""
    tlc = TileLRUCache()
    k1 = ("s1", "r1", 1, 1, 1)
    k2 = ("s2", "r2", 1, 1, 1)
    e1, e2 = tlc.get_epoch("s1", "r1"), tlc.get_epoch("s2", "r2")
    tlc.invalidate_session("s1")
    assert tlc.put_if_current(k1, b"x", e1) is False
    # Over-invalidation by design: even s2's pre-bump capture is refused...
    assert tlc.put_if_current(k2, b"y", e2) is False
    # ...but a FRESH capture (post-bump) of s2 succeeds.
    assert tlc.put_if_current(k2, b"y", tlc.get_epoch("s2", "r2")) is True
    tlc.clear()
    # Post-clear fresh capture accepted; the pre-clear capture is refused.
    assert tlc.put_if_current(k2, b"z", tlc.get_epoch("s2", "r2")) is True
    assert tlc.put_if_current(k2, b"z", e2) is False


# ─── End-to-end: overwrite mid-encode leaves no ghost bytes ────────────────


@pytest.mark.asyncio
async def test_encode_overwrite_midway_serves_no_stale_bytes(monkeypatch):
    """Route-level repro: invalidation lands between index build and tile
    byte caching. The encode must refuse to cache AND signal the route's
    refetch-retry, which then builds from the NEW payload."""
    import app.api.routes.layer as layer_mod

    sid, ref = "sess-p11", "ref:p11-1"
    z, x, y = 5, 10, 10
    key = (sid, ref, z, x, y)

    real_encode = layer_mod.encode_tile_from_index
    state = {"invalidated": False}

    def encode_with_midway_invalidate(entry, zz, xx, yy):
        if not state["invalidated"]:
            state["invalidated"] = True
            # Exactly what MemorySessionStore.overwrite does to derived caches.
            spatial_index_cache.invalidate_ref(sid, ref)
            tile_lru_cache.invalidate_ref(sid, ref)
        return real_encode(entry, zz, xx, yy)

    monkeypatch.setattr(layer_mod, "encode_tile_from_index", encode_with_midway_invalidate)

    # Seed the real spatial index (route fetched data before the race began).
    spatial_index_cache.get_or_build(
        (sid, ref), lambda: layer_mod.build_spatial_index_entry((sid, ref), _FC)
    )
    try:
        with pytest.raises(RefDataUnavailableError):
            layer_mod._encode_tile_cached(sid, ref, z, x, y, None)
        assert tile_lru_cache.get(key) is None, (
            "stale bytes cached across a concurrent invalidation (ghost tile)"
        )

        # Retry path (route refetches fresh data): clean encode succeeds.
        body = layer_mod._encode_tile_cached(sid, ref, z, x, y, _FC)
        assert tile_lru_cache.get(key) == body
    finally:
        spatial_index_cache.invalidate_ref(sid, ref)
        tile_lru_cache.invalidate_ref(sid, ref)
