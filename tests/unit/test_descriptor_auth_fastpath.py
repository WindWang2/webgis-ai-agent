"""
Store-level contract tests for the descriptor fast-path authorization seam
``BaseSessionStore.get_ref_descriptor_authorized`` (V3 perf work, route
``GET /api/v1/layers/descriptor/{ref_id}`` in app/api/routes/layer.py).

What this seam promises:
- returns ``SessionRefDataResult``; ``success=True`` → ``data`` is the
  pre-computed descriptor dict, WITHOUT reading the full payload
  (no ``get()`` / ``get_ref_data()`` call)
- owner-token mismatch → ``error_type="PermissionDenied"``
- descriptor missing (pre-V3 ref) OR payload gone (``ref_exists`` False)
  → ``error_type="NotFound"``

Every test here runs against BOTH backends (MemorySessionStore +
RedisSessionStore over fakeredis) so protocol parity is enforced, mirroring
tests/unit/test_session_store_contract.py.

KNOWN CONTRACT DEVIATION (Redis, pre-existing — NOT introduced by this branch):
``RedisSessionStore.get_ref_descriptor`` (session_data_redis.py:895) has an
on-the-fly fallback: when the descriptor/meta key is missing but the data key
exists, it reads the full payload, recomputes and caches the descriptor, and
returns it. Consequently the seam's "descriptor missing → NotFound" promise
does NOT hold on Redis for pre-V3 refs — the seam returns success with the
recomputed descriptor. End-user behaviour (200 with a correct descriptor) is
identical through either path; test_pre_v3_ref_without_descriptor_falls_back
asserts each backend's actual behaviour and documents this. App source was
NOT modified per task constraints.
"""
import time
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.services.session_data import MemorySessionStore
from app.services.session_data_redis import RedisSessionStore

STORE_FACTORIES = [MemorySessionStore]


def _redis_store_factory():
    """RedisSessionStore backed by in-process fakeredis (same pattern as
    tests/unit/test_session_store_contract.py). The injected client is wired
    in via the ``redis=`` test seam so the production code path runs unchanged
    and `store._r` is already the FakeRedis (no lazy-connect step to trigger)."""
    import fakeredis.aioredis

    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


if RedisSessionStore is not None:
    STORE_FACTORIES.append(_redis_store_factory)


def _point_fc(n=1000):
    """n-Point FeatureCollection; minimal payload (short coords, single int
    property) so building/store()ing the 100k variant stays fast."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.0 + i * 0.001, 39.9],
                },
                "properties": {"id": i},
            }
            for i in range(n)
        ],
    }


def _spy_store_methods(store, stack: ExitStack) -> dict:
    """Wrap the seam's callable surface with counting mocks (wraps= keeps the
    real implementation live while call_count tracks actual invocations).

    Patches are entered on the caller's ExitStack (caller controls lifetime)
    and the mocks are returned keyed by method name."""
    spies = {}
    for name in (
        "get",
        "get_ref_data",
        "get_session_metadata",
        "get_ref_descriptor",
        "ref_exists",
    ):
        spies[name] = stack.enter_context(
            patch.object(store, name, wraps=getattr(store, name))
        )
    return spies


# ───────────────────────────────────────────── A. deterministic work-count ──


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_descriptor_auth_never_hydrates_payload(store_factory):
    """Core acceptance: the seam serves the descriptor without hydrating the
    payload — zero get() and zero get_ref_data() calls."""
    store = store_factory()
    sid = "fastpath_sid_1"
    ref_id = await store.store(sid, _point_fc(1000), prefix="data")

    with ExitStack() as stack:
        spies = _spy_store_methods(store, stack)
        res = await store.get_ref_descriptor_authorized(sid, ref_id)

    assert res.success is True
    assert res.data is not None
    assert res.data["feature_count"] == 1000
    assert spies["get"].call_count == 0, "fast path must never call get()"
    assert spies["get_ref_data"].call_count == 0, "fast path must never call get_ref_data()"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_descriptor_auth_work_count_independent_of_feature_count(store_factory):
    """Deterministic work-count acceptance (no wall-clock / sleep).

    Run the seam once per payload size (1k and 100k features) and snapshot the
    call count of every seam-relevant store method. Both sizes must yield
    byte-identical counts — the fast path's work is independent of feature
    count / payload bytes. (100k kept: FC build ~0.14s, Redis store ~0.5s —
    far under the 30s budget.)
    """
    counts = {}
    for n in (1000, 100000):
        store = store_factory()
        sid = f"fastpath_sid_{n}"
        ref_id = await store.store(sid, _point_fc(n), prefix="data")

        with ExitStack() as stack:
            spies = _spy_store_methods(store, stack)
            res = await store.get_ref_descriptor_authorized(sid, ref_id)
            assert res.success is True
            counts[n] = {name: spies[name].call_count for name in spies}

    assert counts[1000] == counts[100000], (
        f"seam work count must be independent of payload size; got {counts}"
    )
    # Sanity: the profile is exactly the metadata-only path (no payload reads).
    assert counts[1000] == {
        "get": 0,
        "get_ref_data": 0,
        "get_session_metadata": 1,
        "get_ref_descriptor": 1,
        "ref_exists": 1,
    }


@pytest.mark.asyncio
async def test_descriptor_auth_redis_never_gets_data_key():
    """Redis backend only: the seam must never issue a GET against a data key.

    Both the descriptor read (get_ref_descriptor) and the payload read (get)
    funnel through the same ``store._r.get`` command — only the KEY
    distinguishes them: ``session:{sid}:data:{ref_id}`` (payload) vs
    ``session:{sid}:meta:{ref_id}`` (descriptor, see _data_key/_descriptor_key
    in app/services/session_data_redis.py). Spy on ``_r.get`` and assert no
    argument carries the data-key prefix. ``store._r`` is already the injected
    FakeRedis here, so no _ensure_connected step to sequence.
    """
    store = _redis_store_factory()
    sid = "fastpath_sid_redis"
    ref_id = await store.store(sid, _point_fc(1000), prefix="data")
    data_key = store._data_key(sid, ref_id)
    assert data_key.startswith(f"session:{sid}:data:")

    calls = []
    original_get = store._r.get

    async def _spy(key, *args, **kwargs):
        calls.append(key)
        return await original_get(key, *args, **kwargs)

    store._r.get = _spy
    try:
        res = await store.get_ref_descriptor_authorized(sid, ref_id)
    finally:
        store._r.get = original_get

    assert res.success is True
    assert calls, "expected at least the descriptor-key GET"
    data_hits = [
        (k.decode() if isinstance(k, bytes) else k)
        for k in calls
        if (k.decode() if isinstance(k, bytes) else k).startswith(data_key)
    ]
    assert not data_hits, (
        f"fast path must never GET the payload data key; hit on {data_hits}"
    )


# ─────────────────────────────────────────────── B. security regression matrix ──


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_owner_session_no_token_required(store_factory):
    """Session without an owner_token: no token needed → success."""
    store = store_factory()
    sid = "fastpath_sid_b1"
    ref_id = await store.store(sid, _point_fc(10), prefix="data")

    res = await store.get_ref_descriptor_authorized(sid, ref_id)

    assert res.success is True
    assert res.data["ref_id"] == ref_id


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_anonymous_correct_owner_token_success(store_factory):
    """Anonymous session guarded by an owner_token: correct token → success,
    still zero payload reads."""
    store = store_factory()
    sid = "fastpath_sid_b2"
    await store.set_map_state(sid, "owner_token", "secret-token")
    ref_id = await store.store(sid, _point_fc(10), prefix="data")

    with ExitStack() as stack:
        spies = _spy_store_methods(store, stack)
        res = await store.get_ref_descriptor_authorized(
            sid, ref_id, owner_token="secret-token"
        )

    assert res.success is True
    assert res.data["feature_count"] == 10
    assert spies["get"].call_count == 0


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_anonymous_wrong_token_denied(store_factory):
    """Wrong owner token → PermissionDenied, and the token is validated BEFORE
    the descriptor is read: an unauthorized caller must not learn whether a
    descriptor exists (get_ref_descriptor not called) nor read payloads
    (get not called)."""
    store = store_factory()
    sid = "fastpath_sid_b3"
    await store.set_map_state(sid, "owner_token", "secret-token")
    ref_id = await store.store(sid, _point_fc(10), prefix="data")

    with ExitStack() as stack:
        spies = _spy_store_methods(store, stack)
        res = await store.get_ref_descriptor_authorized(
            sid, ref_id, owner_token="wrong-token"
        )

    assert res.success is False
    assert res.error_type == "PermissionDenied"
    assert spies["get"].call_count == 0
    assert spies["get_ref_descriptor"].call_count == 0, (
        "token check must precede descriptor read — unauthenticated callers "
        "get no existence oracle"
    )


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_missing_ref_not_found(store_factory):
    """Never-stored ref → NotFound."""
    store = store_factory()
    sid = "fastpath_sid_b4"

    res = await store.get_ref_descriptor_authorized(sid, "ref:does-not-exist")

    assert res.success is False
    assert res.error_type == "NotFound"


@pytest.mark.asyncio
async def test_evicted_ref_not_found_memory():
    """Memory LRU: capacity=1 evicts the first ref (payload + descriptor) →
    the seam must report NotFound for it while the surviving ref still works."""
    store = MemorySessionStore(capacity=1)
    sid = "fastpath_sid_b5"
    ref1 = await store.store(sid, {"payload": 1}, prefix="data")
    ref2 = await store.store(sid, {"payload": 2}, prefix="data")
    assert ref1 != ref2

    res1 = await store.get_ref_descriptor_authorized(sid, ref1)
    assert res1.success is False
    assert res1.error_type == "NotFound"

    res2 = await store.get_ref_descriptor_authorized(sid, ref2)
    assert res2.success is True
    assert res2.data["ref_id"] == ref2


@pytest.mark.asyncio
async def test_evicted_ref_not_found_redis():
    """Redis has no capacity=1 analogue in this simulation; model an
    evicted/expired ref by deleting BOTH the descriptor key and the data key
    (real eviction in session_data_redis.py `_evict_ref` deletes both) →
    NotFound."""
    store = _redis_store_factory()
    sid = "fastpath_sid_b5r"
    ref1 = await store.store(sid, {"payload": 1}, prefix="data")

    await store._r.delete(store._descriptor_key(sid, ref1))
    await store._r.delete(store._data_key(sid, ref1))

    res = await store.get_ref_descriptor_authorized(sid, ref1)
    assert res.success is False
    assert res.error_type == "NotFound"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_cross_session_ref_not_found(store_factory):
    """A ref stored in session A is invisible to session B → NotFound."""
    store = store_factory()
    sid_a, sid_b = "fastpath_sid_b6a", "fastpath_sid_b6b"
    ref_id = await store.store(sid_a, _point_fc(10), prefix="data")

    res = await store.get_ref_descriptor_authorized(sid_b, ref_id)

    assert res.success is False
    assert res.error_type == "NotFound"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_descriptor_orphaned_but_data_gone_not_found(store_factory):
    """Descriptor residue with the payload gone must NOT yield success.

    Memory: ``del store._store[sid][ref_id]`` keeps the descriptor entry.
    Redis: delete only the data key, keep the descriptor key. The seam's
    ref_exists check must catch the orphaned state → NotFound (never success),
    and must not hydrate (get call count 0)."""
    store = store_factory()
    sid = "fastpath_sid_b7"
    ref_id = await store.store(sid, _point_fc(10), prefix="data")
    if isinstance(store, RedisSessionStore):
        await store._r.delete(store._data_key(sid, ref_id))
    else:
        del store._store[sid][ref_id]

    with ExitStack() as stack:
        spies = _spy_store_methods(store, stack)
        res = await store.get_ref_descriptor_authorized(sid, ref_id)

    assert res.success is False
    assert res.error_type == "NotFound"
    assert spies["get"].call_count == 0


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_pre_v3_ref_without_descriptor_falls_back(store_factory):
    """Pre-V3 ref (no stored descriptor) must remain servable.

    Memory: remove the descriptor entry → the seam reports NotFound and the
    ROUTE-level fallback (get_ref_data) recomputes the descriptor; store-level
    assertion is NotFound. get_ref_data must still return the payload intact
    (fallback path not broken).

    Redis: deleting the meta key does NOT surface NotFound —
    RedisSessionStore.get_ref_descriptor's pre-existing on-the-fly fallback
    reads the data key, recomputes and caches the descriptor, so the seam
    returns success. This deviates from the seam's documented contract (see
    module docstring) and is asserted here as the ACTUAL behavior; end-user
    outcome (200 with a correct descriptor) is identical through either path.
    """
    store = store_factory()
    sid = "fastpath_sid_b8"
    ref_id = await store.store(sid, _point_fc(10), prefix="data")

    if isinstance(store, RedisSessionStore):
        await store._r.delete(store._descriptor_key(sid, ref_id))
        res = await store.get_ref_descriptor_authorized(sid, ref_id)
        assert res.success is True, (
            "Redis get_ref_descriptor recomputes a missing descriptor on the "
            "fly (pre-existing fallback) — see module docstring; flip this to "
            "expect NotFound if that fallback is ever removed"
        )
        assert res.data["feature_count"] == 10
    else:
        store._descriptors[sid].pop(ref_id)
        res = await store.get_ref_descriptor_authorized(sid, ref_id)
        assert res.success is False
        assert res.error_type == "NotFound"

    # Deep read path (route-level fallback) is intact on both backends.
    deep = await store.get_ref_data(sid, ref_id)
    assert deep.success is True
    assert deep.data is not None


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_ref_exists_parity(store_factory):
    """ref_exists primitive parity: present / missing / cross-session."""
    store = store_factory()
    sid_a, sid_b = "fastpath_sid_b9a", "fastpath_sid_b9b"
    ref_id = await store.store(sid_a, {"payload": 1}, prefix="data")

    assert await store.ref_exists(sid_a, ref_id) is True
    assert await store.ref_exists(sid_a, "ref:does-not-exist") is False
    assert await store.ref_exists(sid_b, ref_id) is False


# ─────────────────────────────────────── C. recency side-effect regression ──


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_descriptor_access_refreshes_payload_recency(store_factory):
    """Fix 1 regression (both backends): a descriptor fast-path hit must carry
    the same recency side-effects as a payload get() — Memory LRU bump, Redis
    data-key TTL + refs_order score refresh — so an actively-polled descriptor
    keeps its payload alive under LRU/TTL eviction (master's
    get_ref_data → get() semantics).

    Memory: capacity=2 — accessing refA's descriptor, then storing refC must
    evict the unaccessed refB, not the just-accessed refA.
    Redis: shrinking the data key's TTL to 1s, then accessing the descriptor
    must renew it back to ~DATA_TTL (7200s) and re-stamp the refs_order score.
    """
    sid = "fastpath_sid_fix1"
    if store_factory is MemorySessionStore:
        store = MemorySessionStore(capacity=2)
        ref_a = await store.store(sid, _point_fc(10), prefix="data")
        ref_b = await store.store(sid, _point_fc(10), prefix="data")

        res = await store.get_ref_descriptor_authorized(sid, ref_a)
        assert res.success is True

        ref_c = await store.store(sid, _point_fc(10), prefix="data")
        assert ref_c not in (ref_a, ref_b)
        assert await store.ref_exists(sid, ref_a) is True, (
            "descriptor access must refresh LRU recency so refA survives eviction"
        )
        assert await store.ref_exists(sid, ref_b) is False, (
            "unaccessed refB must be the evicted ref"
        )
    else:
        store = store_factory()
        ref_id = await store.store(sid, _point_fc(10), prefix="data")
        data_key = store._data_key(sid, ref_id)
        # Simulate a payload about to expire (meta key TTL still intact).
        await store._r.pexpire(data_key, 1000)

        res = await store.get_ref_descriptor_authorized(sid, ref_id)
        assert res.success is True

        # DATA_TTL = 7200; the hit branch must restore the near-full TTL.
        ttl = await store._r.ttl(data_key)
        assert ttl > 7000, (
            f"descriptor access must renew data-key TTL near DATA_TTL; got ttl={ttl}"
        )

        # refs_order zset score re-stamped by the hit branch (optional check).
        score = await store._r.zscore(store._refs_order_key(sid), ref_id)
        assert score is not None and abs(score - time.time()) < 30, (
            f"descriptor access must refresh refs_order score; got {score}"
        )
