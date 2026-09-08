"""Cache / singleflight / invalidation chaos matrix — Wave 10 V5 (audit 07 §6.2 steps 3-5).

Windows NOT pinned elsewhere (complements test_cache_race_windows_v3.py,
test_mvt_epoch_race_p11.py, test_cache_lineage_v4.py,
test_tool_cache_singleflight.py — reuse their Event/barrier patterns):

- R4a: invalidate-during-index-singleflight-build — the shared build is refused
  per caller by the unchanged epoch guards (singleflight stays an optimization).
- R4a: concurrent cold misses of one (session, ref) share exactly ONE build.
- R4b: ref-payload fetch singleflight — leader crash → followers degrade to a
  direct rebuild; Redis outage → direct compute, no hang.
- R3: tool_cache owner-domain key isolation (cross-user sharing disappears for
  identity-bearing calls).
- D11: broadcast storm — N rapid events applied without re-publishing.
- D11: listener reconnect — dropped fake Redis connection resumes applying
  (bounded wait via the injected reconnect interval).
- R6: raster tile/stats caches under the lifecycle — authority invalidation
  clears by registered path; byte bound evicts LRU; stats entries expire.
- delete-during-read through the authority: put_if_current refuses (existing
  epoch) + spatial index projection invalidated.

Correctness stance (unchanged): broadcast/singleflight are advisory or
optimization; epochs/revisions/TTL stay the only correctness mechanisms.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import app.services.cache_broadcast as cb
import app.services.ref_lifecycle as rl
import app.services.session_data_redis as sdr
from app.lib.tool_cache import make_cache_key
from app.services.mvt import RefDataUnavailableError, SpatialIndexCache, spatial_index_cache
from app.services.ref_lifecycle import (
    RefInvalidationReason,
    invalidate_ref_caches,
)
from app.services.raster_tile_service import (
    _RASTER_CACHE_LOCK,
    _RASTER_REF_REGISTRY,
    _RASTER_TILE_CACHE,
    _STATS_CACHE,
    _STATS_CACHE_LOCK,
    invalidate_raster_ref,
    register_raster_ref,
    raster_tile_cache_bytes,
    _get_cached_tile,
    _set_cached_tile,
)


# ── 1. R4a: invalidate during a shared index build → stale result refused ───


def test_index_singleflight_invalidate_during_build_refuses_stale():
    """The production interleaving through the new singleflight: leader builds,
    a follower joins, an overwrite invalidates the ref mid-build. Both callers
    re-validate their OWN captured epoch at insert time — the shared build is
    refused for every participant and nothing is resurrected."""
    cache = SpatialIndexCache()
    key = ("sess-chaos-1", "ref:chaos-1")
    started, release = threading.Event(), threading.Event()
    builds = {"n": 0}
    outcome: dict = {}

    def build_fn():
        builds["n"] += 1
        started.set()
        release.wait(timeout=5)
        return SimpleNamespace(estimated_bytes=128, features=[])

    def caller(box: dict):
        try:
            box["entry"] = cache.get_or_build(key, build_fn)
        except Exception as e:  # noqa: BLE001 - asserted below
            box["err"] = e

    leader = threading.Thread(target=caller, args=(outcome,), name="leader")
    leader.start()
    assert started.wait(timeout=5)

    follower_box: dict = {}
    follower = threading.Thread(target=caller, args=(follower_box,), name="follower")
    follower.start()
    # Deterministic: wait until the follower registered on the in-flight key
    # (SingleFlight length == number of keys with an in-flight build).
    deadline = time.monotonic() + 5
    while len(cache._build_flight) < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(cache._build_flight) == 1, "follower never joined the in-flight build"

    # Authoritative overwrite lands mid-build (same epoch discipline the
    # ref_lifecycle authority drives — unit-level on this cache instance).
    cache.invalidate_ref(key[0], key[1])
    release.set()
    leader.join(timeout=5)
    follower.join(timeout=5)
    assert not leader.is_alive() and not follower.is_alive()

    assert isinstance(outcome.get("err"), RefDataUnavailableError), outcome
    assert isinstance(follower_box.get("err"), RefDataUnavailableError), follower_box
    assert builds["n"] == 1, "concurrent misses must share ONE build"
    assert len(cache) == 0, "stale build must not be cached"

    # A builder starting AFTER the invalidation publishes cleanly.
    entry = cache.get_or_build(key, build_fn)
    assert builds["n"] == 2 and len(cache) == 1
    assert entry.estimated_bytes == 128


def test_index_singleflight_shares_one_cold_build_across_tiles():
    """N concurrent cold misses of one (session, ref) → exactly one build_fn
    execution, all callers receive the same entry (R4a stampede collapse)."""
    cache = SpatialIndexCache()
    key = ("sess-chaos-2", "ref:cold")
    builds = {"n": 0}

    def build_fn():
        builds["n"] += 1
        time.sleep(0.15)  # let every worker reach the flight (lineage-v4 pattern)
        return SimpleNamespace(estimated_bytes=64, features=[])

    results: list = []
    lock = threading.Lock()

    def worker():
        entry = cache.get_or_build(key, build_fn)
        with lock:
            results.append(entry)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    assert builds["n"] == 1, f"expected one shared build, got {builds['n']}"
    assert len(results) == 8 and all(r is results[0] for r in results)
    assert len(cache) == 1 and cache.total_bytes == 64


def test_index_build_crash_propagates_and_cache_stays_usable():
    """Builder exception propagates (honest failure), nothing cached, and the
    cache accepts a fresh build afterwards."""
    cache = SpatialIndexCache()
    key = ("sess-chaos-3", "ref:boom")

    def bad_build():
        raise RuntimeError("index boom")

    with pytest.raises(RuntimeError, match="index boom"):
        cache.get_or_build(key, bad_build)
    assert len(cache) == 0
    assert len(cache._build_flight) == 0, "crashed build must release its flight slot"

    entry = cache.get_or_build(key, lambda: SimpleNamespace(estimated_bytes=8, features=[]))
    assert entry.estimated_bytes == 8 and len(cache) == 1


# ── 2. R4b: ref-payload fetch singleflight — crash degrade / outage no-hang ─


@pytest.fixture()
def _fresh_ref_fetch_flight():
    sdr._reset_ref_fetch_flight_for_tests()
    yield
    sdr._reset_ref_fetch_flight_for_tests()


async def test_ref_fetch_leader_crash_followers_degrade_to_direct(_fresh_ref_fetch_flight):
    """The singleflight leader's fetch crashes: waiters must not inherit a
    permanent failure — each degrades to one direct rebuild and succeeds."""
    calls = {"n": 0}

    class FakeStore:
        async def _fetch_shared_payload(self, session_id, ref_id, data_key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("leader boom")
            return ({"gen": "ok"}, 32, 0)

    r1, r2 = await asyncio.gather(
        sdr.ref_fetch_shared(FakeStore(), "sess-crash", "ref:x", "data:1"),
        sdr.ref_fetch_shared(FakeStore(), "sess-crash", "ref:x", "data:1"),
    )
    assert r1 == ({"gen": "ok"}, 32, 0)
    assert r2 == ({"gen": "ok"}, 32, 0)
    assert 2 <= calls["n"] <= 3, "degrade must be bounded (direct rebuild), not a storm"


async def test_ref_fetch_redis_outage_degrades_without_hang(_fresh_ref_fetch_flight):
    """Redis outage during a singleflight get_or_build → the error surfaces
    promptly (direct compute path), never a hang (get_shared maps RedisError
    to cache-miss semantics upstream)."""
    import redis.asyncio as aioredis

    class DownStore:
        async def _fetch_shared_payload(self, session_id, ref_id, data_key):
            raise aioredis.RedisError("redis down")

    t0 = time.monotonic()
    with pytest.raises(aioredis.RedisError):
        await sdr.ref_fetch_shared(DownStore(), "sess-outage", "ref:y", "data:2")
    assert time.monotonic() - t0 < 5.0, "degrade path must not hang"


async def test_ref_fetch_shared_singleflight_dedups_concurrent_misses(_fresh_ref_fetch_flight):
    """Concurrent misses of one (session, ref) share one GET+parse (the R4b
    stampede collapse that motivates the seam)."""
    calls = {"n": 0}
    gate = asyncio.Event()

    class FakeStore:
        async def _fetch_shared_payload(self, session_id, ref_id, data_key):
            calls["n"] += 1
            await asyncio.wait_for(gate.wait(), timeout=5)
            return ({"gen": "shared"}, 16, 0)

    async def caller():
        return await sdr.ref_fetch_shared(FakeStore(), "sess-dedup", "ref:z", "data:3")

    task1 = asyncio.create_task(caller())
    await asyncio.sleep(0.02)  # let task1 become the leader
    task2 = asyncio.create_task(caller())
    await asyncio.sleep(0.05)  # let task2 register on the leader's future
    gate.set()
    r1, r2 = await asyncio.gather(task1, task2)
    assert r1 == ({"gen": "shared"}, 16, 0) and r2 == ({"gen": "shared"}, 16, 0)
    assert calls["n"] == 1


async def test_get_shared_follower_never_resurrects_pre_overwrite_payload(
    _fresh_ref_fetch_flight,
):
    """CONC MAJOR-1（round1）：确定性交错 —— leader 的 GET 阻塞期间发生
    overwrite（epoch 递增），跟随者带着自己的新 epoch 加入飞行共享到 leader
    的**前覆写**取回结果。

    修复语义：跟随者入缓存用 min(own_epoch, fetch_epoch) → 旧取回对不上
    当前 epoch，put_if_current 拒收；缓存随后的读取只服务新 payload。
    """
    from app.services.ref_payload_cache import ref_payload_cache

    sid, ref = "sess-epoch-race", "ref:race"
    redis_state = {"data": {"old": "payload"}}
    gate = asyncio.Event()
    leader_started = asyncio.Event()

    class FakeStore:
        async def _fetch_shared_payload(self, session_id, ref_id, data_key):
            # leader：飞行体内、读源前捕获 fetch_epoch（=0）与 GET 读到的
            # 前覆写载荷，随后阻塞 —— 覆写（epoch→1）发生在捕获与返回之间
            # 的窗口内（GET 在覆写前发出，返回的必然是旧字节）。
            fetch_epoch = ref_payload_cache.current_epoch(session_id, ref_id)
            payload = redis_state["data"]
            leader_started.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            return (payload, 8, fetch_epoch)

    async def leader():
        # 直接走 get_shared 的共享单元路径（epoch 捕获/put 语义由真实
        # get_shared 执行 —— 这里手工复刻同一交错，避免拉起真 Redis 客户端）。
        fetched = await sdr.ref_fetch_shared(
            FakeStore(), sid, ref, "data:race")
        data, raw_len, fetch_epoch = fetched
        own_epoch = ref_payload_cache.current_epoch(sid, ref)
        return ref_payload_cache.put_if_current(
            sid, ref, data, raw_len, min(own_epoch, fetch_epoch))

    async def follower():
        await leader_started.wait()
        # 覆写：epoch 0 → 1（invalidate 递增），Redis 里已是新 payload。
        ref_payload_cache.invalidate(sid, ref)
        redis_state["data"] = {"new": "payload"}
        fetched = await sdr.ref_fetch_shared(
            FakeStore(), sid, ref, "data:race")
        data, raw_len, fetch_epoch = fetched
        own_epoch = ref_payload_cache.current_epoch(sid, ref)
        assert own_epoch == 1 and fetch_epoch == 0, "交错前置条件"
        return ref_payload_cache.put_if_current(
            sid, ref, data, raw_len, min(own_epoch, fetch_epoch))

    leader_task = asyncio.create_task(leader())
    follower_task = asyncio.create_task(follower())
    await asyncio.sleep(0.05)  # follower 已在飞行上等待
    gate.set()
    put_leader, put_follower = await asyncio.gather(leader_task, follower_task)

    assert put_leader is False, "leader 的旧 epoch 入缓存被拒（既有 M7 语义）"
    assert put_follower is False, (
        "跟随者绝不能把 leader 的前覆写 payload 复活到新 epoch 下")
    assert ref_payload_cache.get(sid, ref) is None, "缓存只服务覆写后的 payload"


# ── 3. R3: tool_cache owner-domain key isolation ────────────────────────────


def test_tool_cache_owner_domain_isolates_identity_bearing_calls():
    """Cross-user sharing disappears for identity-bearing calls: identical
    ref-free args with different sessions produce different keys; anonymous
    calls (no identity available) keep their own distinct shared domain."""
    k_anon = make_cache_key("tool", {"q": 1})
    k_a = make_cache_key("tool", {"q": 1, "session_id": "sess-aaa"})
    k_b = make_cache_key("tool", {"q": 1, "session_id": "sess-bbb"})

    assert k_anon.startswith("tool_cache:v2:")
    assert len({k_anon, k_a, k_b}) == 3, "session identity must partition the keyspace"
    # deterministic
    assert k_a == make_cache_key("tool", {"q": 1, "session_id": "sess-aaa"})
    # explicit stronger identity (user scope) overrides the session-derived one
    assert make_cache_key("tool", {"q": 1, "session_id": "sess-aaa"},
                          owner_scope="u:alice") != k_a
    # the ref correctness gate is untouched by the key-shape change
    assert make_cache_key("tool", {"q": "ref:abc"}) is None
    # non-dict args cannot carry identity → anonymous domain, still a key
    assert make_cache_key("tool", {"q": 1}, owner_scope=None) == k_anon


def test_tool_cache_owner_domain_hashed_not_raw():
    """The owner domain appears in the key only hashed (executor.owner_scope_for
    discipline) — the 16-hex digest never contains the session id."""
    sid = "sess-sensitive-identifier"
    key = make_cache_key("tool", {"q": 1, "session_id": sid})
    assert sid not in key
    assert key.startswith("tool_cache:v2:")


# ── 4. D11: broadcast storm suppression at N-speed ──────────────────────────


def test_broadcast_storm_applies_without_republishing(monkeypatch):
    """N rapid invalidation events through the (fake-bus) listener path are all
    applied locally with publish_broadcast=False — zero re-publishes, no loop."""
    publishes = {"n": 0}

    class FakeBus:
        def publish(self, channel, message):
            publishes["n"] += 1
            return 1

    applied = {"n": 0}

    def spy_invalidate(session_id, ref_ids, reason=RefInvalidationReason.REPLACE,
                       include_payload_cache=True, publish_broadcast=True):
        assert publish_broadcast is False, "listener path must never re-publish"
        applied["n"] += 1
        return 1

    monkeypatch.setattr(cb, "_client_cached", lambda: FakeBus())
    monkeypatch.setattr(rl, "invalidate_ref_caches", spy_invalidate)

    for i in range(200):
        cb._apply_event(json.dumps({
            "kind": "ref_invalidation",
            "session_id": "sess-storm",
            "ref_id": f"ref:storm-{i}",
            "reason": "OVERWRITE",
        }))
    assert applied["n"] == 200
    assert publishes["n"] == 0, "apply-through-authority must not re-broadcast"


def test_rapid_authority_invalidations_stay_bounded(monkeypatch):
    """The real authority survives a 200-ref rapid-fire invalidation: every ref
    counted, hooks/handlers never raise, broadcast mocked away."""
    monkeypatch.setattr(
        "app.services.cache_broadcast.broadcast_ref_invalidation",
        lambda *a, **k: False,
    )
    refs = [f"ref:burst-{i}" for i in range(200)]
    n = invalidate_ref_caches("sess-burst", refs,
                              reason=RefInvalidationReason.REPLACE,
                              publish_broadcast=True)
    assert n == 200
    # the process singletons took the drops without error
    assert all((("sess-burst", r)) not in spatial_index_cache.keys() for r in refs)


# ── 5. D11: listener restart/reconnect resumes applying ─────────────────────


def test_listener_reconnects_after_dropped_connection(monkeypatch):
    """Fake Redis drops the connection mid-listen → the loop backs off (bounded,
    injected interval) and the reconnected epoch resumes applying events."""
    applied: list = []
    stop = threading.Event()
    epochs = {"n": 0}

    class FlakyPubSub:
        def subscribe(self, channel):
            pass

        def listen(self):
            epochs["n"] += 1
            if epochs["n"] == 1:
                raise ConnectionError("connection dropped")  # first epoch dies
            yield {"type": "message", "data": json.dumps({
                "kind": "ref_invalidation",
                "session_id": "sess-re",
                "ref_id": "ref:re",
            })}
            raise ConnectionError("dropped again")  # loop exits via stop

    class FakeClient:
        def pubsub(self, ignore_subscribe_messages=True):
            return FlakyPubSub()

    def fake_apply(message: str):
        applied.append(message)
        stop.set()

    monkeypatch.setattr(cb, "_apply_event", fake_apply)
    t0 = time.monotonic()
    cb._listen_loop(FakeClient(), reconnect_wait_s=0.01, stop=stop)
    elapsed = time.monotonic() - t0

    assert epochs["n"] >= 2, "listener must re-subscribe after the drop"
    assert len(applied) == 1, "event after reconnect must be applied"
    assert elapsed < 5.0, "reconnect wait must honor the injected (bounded) interval"


# ── 6. R6: raster tile/stats caches under the lifecycle ─────────────────────


@pytest.fixture()
def _raster_registry_cleanup():
    yield
    with _RASTER_CACHE_LOCK:
        _RASTER_REF_REGISTRY.clear()


def test_raster_invalidation_hook_clears_tile_and_stats_caches(_raster_registry_cleanup):
    """R6: overwrite/rollback of a raster ref clears the path-keyed tile cache
    AND the band-stats cache via the additive lifecycle hook (best-effort)."""
    sid, rid, path = "sess-raster-chaos", "ref:raster-x", "/data/chaos-a.tif"
    register_raster_ref(sid, rid, path)
    tkey = (path, 5, 1, 1, 256, "", ())
    skey = (path, (1,))
    _set_cached_tile(tkey, b"\x89PNG-fake-bytes" * 4)
    with _STATS_CACHE_LOCK:
        _STATS_CACHE[skey] = (((0.0, 1.0),), time.monotonic() + 600)
    assert _get_cached_tile(tkey) is not None
    assert skey in _STATS_CACHE

    n = invalidate_ref_caches(sid, [rid], reason=RefInvalidationReason.OVERWRITE,
                              publish_broadcast=False)
    assert n == 1
    assert _get_cached_tile(tkey) is None, "tiles of the superseded raster must drop"
    assert skey not in _STATS_CACHE, "stats of the superseded raster must drop"
    # idempotent: the association is consumed, a re-run is a no-op
    assert invalidate_raster_ref(sid, rid) == 0


def test_raster_hook_failure_never_breaks_the_authority(monkeypatch):
    """The authority's contract: an observer that raises is swallowed."""
    sid, rid = "sess-raster-hookfail", "ref:raster-y"

    def exploding_hook(session_id, ref_id, reason):
        raise RuntimeError("hook boom")

    monkeypatch.setattr(rl, "_invalidation_hooks", [exploding_hook])
    n = invalidate_ref_caches(sid, [rid], reason=RefInvalidationReason.DELETE,
                              publish_broadcast=False)
    assert n == 1, "authority must complete despite the hook failure"


def test_raster_tile_cache_byte_bound_evicts_lru(_raster_registry_cleanup, monkeypatch):
    """R6: the PNG tile cache gains a byte bound — LRU eviction by bytes, and
    oversized single entries are never cached (TileLRUCache policy)."""
    import app.services.raster_tile_service as rts

    with _RASTER_CACHE_LOCK:
        _RASTER_TILE_CACHE.clear()
        rts._raster_tile_total_bytes = 0
    monkeypatch.setattr(rts, "_RASTER_TILE_MAX_BYTES", 100)
    big = b"x" * 60
    key_a = ("/p/a.tif", 1, 0, 0, 256, "", ())
    key_b = ("/p/b.tif", 1, 0, 0, 256, "", ())
    _set_cached_tile(key_a, big)
    _set_cached_tile(key_b, big)  # 120 > 100 → oldest (a) evicted
    assert _get_cached_tile(key_a) is None
    assert _get_cached_tile(key_b) == big
    assert raster_tile_cache_bytes() == 60, "byte accounting must track eviction"

    key_c = ("/p/c.tif", 1, 0, 0, 256, "", ())
    _set_cached_tile(key_c, b"y" * 200)  # oversized single entry
    assert _get_cached_tile(key_c) is None
    assert raster_tile_cache_bytes() == 60

    with _RASTER_CACHE_LOCK:
        _RASTER_TILE_CACHE.clear()
        rts._raster_tile_total_bytes = 0


def test_raster_stats_cache_ttl_expires(monkeypatch):
    """R6: band stats are TTL-bounded — an expired entry recomputes (from the
    currently open dataset) instead of serving a stale stretch forever."""
    import app.services.raster_tile_service as rts

    path = "/data/chaos-b.tif"
    skey = (path, (1,))
    compute_calls = {"n": 0}

    def fake_compute(src, indexes):
        compute_calls["n"] += 1
        return ((0.0, 9.0),)

    monkeypatch.setattr(rts, "_compute_band_stats", fake_compute)
    with _STATS_CACHE_LOCK:
        _STATS_CACHE.pop(skey, None)
        # expired entry (TTL elapsed)
        _STATS_CACHE[skey] = (((0.0, 1.0),), time.monotonic() - 1.0)

    stats = rts._get_band_stats(path, object(), (1,))
    assert stats == ((0.0, 9.0),), "expired stats must recompute"
    assert compute_calls["n"] == 1
    # fresh entry is served without recompute
    stats2 = rts._get_band_stats(path, object(), (1,))
    assert stats2 == ((0.0, 9.0),) and compute_calls["n"] == 1
    with _STATS_CACHE_LOCK:
        _STATS_CACHE.pop(skey, None)


# ── 7. delete-during-read through the authority ─────────────────────────────


def test_delete_during_read_refuses_cache_put_and_bumps_index_epoch():
    """Ref payload deleted between the reader's epoch capture and its cache
    put: put_if_current refuses (existing epoch discipline) and the authority
    bumped the spatial index projection's epoch (delete-mid-read window).

    Uses the process singletons: the authority invalidates the real
    projections (unique session keeps the test hermetic)."""
    sid, rid = "sess-del-chaos", "ref:del-chaos"
    from app.services.ref_payload_cache import ref_payload_cache as payload_singleton

    # CONC MINOR-1：epoch 行现为 (gen, bumped_at) 二元组（prune 宽限期）。
    before_index_epoch = spatial_index_cache._epochs.get((sid, rid), (0, 0.0))[0]

    started, release = threading.Event(), threading.Event()
    outcome: dict = {}

    def reader():
        epoch = payload_singleton.current_epoch(sid, rid)  # capture BEFORE source read
        started.set()
        release.wait(timeout=5)
        outcome["stored"] = payload_singleton.put_if_current(sid, rid, {"gen": "old"}, 64, epoch)

    t = threading.Thread(target=reader, name="delete-during-read")
    t.start()
    assert started.wait(timeout=5)

    invalidate_ref_caches(sid, [rid], reason=RefInvalidationReason.DELETE,
                          publish_broadcast=False)  # authoritative delete mid-read
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()

    assert outcome["stored"] is False, "superseded payload must be refused"
    assert payload_singleton.get(sid, rid) is None
    assert spatial_index_cache._epochs.get((sid, rid), (0, 0.0))[0] == before_index_epoch + 1, (
        "authority must bump the index projection even with no entry materialized"
    )


# ── 7. CONC MINOR-4a: dispatch-level tool-cache key isolation ────────────────


@pytest.fixture()
def _mock_tool_cache_store():
    """Fake redis backing the tool cache (same fake as
    test_tool_cache_singleflight.py — storage-backed MagicMock client)."""
    from unittest.mock import MagicMock, patch

    from app.lib.tool_cache import _reset_redis_client_for_tests

    storage = {}
    locks = {}

    def fake_set(name, value, nx=False, px=None):
        if nx:
            if name in locks:
                return False
            locks[name] = value
            return True
        storage[name] = value
        return True

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.set.side_effect = fake_set
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.exists.side_effect = lambda k: 1 if k in locks else 0
        mock_redis.eval.side_effect = lambda script, num, key, token: (
            1 if locks.get(key) == token and locks.pop(key, None) is not None else 0
        )
        mock_client.return_value = mock_redis
        _reset_redis_client_for_tests()
        yield storage
        _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_dispatch_session_injection_partitions_tool_cache(_mock_tool_cache_store):
    """CONC MINOR-4a（round1）：带会话身份的缓存工具经**真实 registry
    dispatch** 调用 —— 两个不同 session_id 的同名同参调用必须得到不同的
    缓存条目。registry.dispatch 把上下文 session 注入工具 kwargs，注入是
    make_cache_key owner 域隔离的前提 —— 注入一旦丢失，此测试即回归
    （跨会话缓存共享 = 跨用户时序侧信道）。"""
    from app.lib.tool_cache import cached_tool, make_cache_key
    from app.tools.registry import ToolRegistry

    calls = {"n": 0}

    @cached_tool(ttl=3600)
    async def chaos_probe_tool(region: str, session_id=None):
        calls["n"] += 1
        return {"region": region, "epoch": calls["n"]}

    reg = ToolRegistry()
    reg.register("chaos_probe_tool", "cache key isolation probe", chaos_probe_tool)

    # 注入的 session_id 必须参与键派生（键形状回归的对称断言）。
    k_a = make_cache_key("chaos_probe_tool", {"region": "cn", "session_id": "sess-iso-a"})
    k_b = make_cache_key("chaos_probe_tool", {"region": "cn", "session_id": "sess-iso-b"})
    assert k_a is not None and k_b is not None and k_a != k_b

    r1 = await reg.dispatch("chaos_probe_tool", {"region": "cn"}, session_id="sess-iso-a")
    r2 = await reg.dispatch("chaos_probe_tool", {"region": "cn"}, session_id="sess-iso-a")
    r3 = await reg.dispatch("chaos_probe_tool", {"region": "cn"}, session_id="sess-iso-b")

    assert r1 == r2, "同 session 同参调用必须命中同一缓存条目"
    assert calls["n"] == 2, "两个 session 各自计算一次（绝不共享条目）"
    assert r3.get("epoch") == 2, "sess-b 不得读到 sess-a 的缓存值"
    stored_keys = [k for k in _mock_tool_cache_store if k.startswith("tool_cache:v2:")]
    assert len(stored_keys) == 2, "两个 session 各占一个独立缓存条目"
