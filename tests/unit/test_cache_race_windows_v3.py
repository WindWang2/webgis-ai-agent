"""Cache race windows V3 — correctness-first cache invariants (GeoCompute V3).

Only windows NOT already pinned elsewhere:

- ``RefPayloadCache`` epoch guard: invalidate-during-build and
  put/invalidate interleavings (the P1-1 semantics that
  test_mvt_epoch_race_p11.py pins for the MVT caches, applied to the
  payload cache). Cache correctness is a first-class invariant here —
  TTL is a performance fallback, never the invalidation mechanism.
- ``StatisticsStore`` revision flow + planner pass-through honesty
  (the optimizer tests cover TTL/invalidate basics, not "new stats
  replace old stats for the same fingerprint").
- ``RefPayloadCache.invalidate_session`` session isolation.

Deliberately NOT duplicated here (already covered):
- ``_DfTileCache.invalidate_item`` + ETag/304 → test_data_fabric_postgis_v2.py
- lifecycle ``invalidate_ref_caches`` payload/index/tile triple → test_ref_lifecycle_v5.py
- spatial index / tile LRU epoch races → test_mvt_epoch_race_p11.py
- StatisticsStore TTL + invalidate basics → test_data_fabric_optimizer_v3.py
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from app.services.ref_payload_cache import RefPayloadCache

_SID = "sess-v3-race"
_REF = "ref:v3-race"


# ── 1. invalidate-during-build: the built payload must not resurrect ────────


def test_invalidate_during_build_does_not_resurrect_entry():
    """The production interleaving: a builder captures the epoch, the heavy
    dereference runs on a worker thread, an overwrite invalidates the ref
    mid-build, and the completed build tries to publish. The epoch guard
    must refuse the superseded payload — no 5s ghost window."""
    cache = RefPayloadCache()
    started = threading.Event()
    release = threading.Event()
    outcome: dict = {}

    def builder():
        epoch = cache.current_epoch(_SID, _REF)  # capture BEFORE reading source
        started.set()
        release.wait(timeout=5)
        outcome["captured_epoch"] = epoch
        outcome["stored"] = cache.put_if_current(_SID, _REF, {"gen": "old"}, 64, epoch)

    t = threading.Thread(target=builder, name="payload-builder")
    t.start()
    assert started.wait(timeout=5)
    cache.invalidate(_SID, _REF)  # authoritative overwrite lands mid-build
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()

    assert outcome["stored"] is False, "pre-invalidation build must be refused"
    assert cache.get(_SID, _REF) is None, "stale payload resurrected after invalidation"
    assert len(cache) == 0 and cache.total_bytes == 0

    # The epoch moved exactly once; a builder starting AFTER the invalidation
    # (fresh capture) publishes cleanly.
    fresh = cache.current_epoch(_SID, _REF)
    assert fresh == outcome["captured_epoch"] + 1
    assert cache.put_if_current(_SID, _REF, {"gen": "new"}, 64, fresh) is True
    assert cache.get(_SID, _REF) == {"gen": "new"}


# ── 2. overwrite-during-encode: put/invalidate interleave via counters ──────


def test_put_invalidate_interleaving_never_serves_superseded_payload():
    """Deterministic cross-thread alternation (Events, not sleeps) of the
    publish/invalidate protocol. Sequence-counter invariant: every payload
    ever served carries the invalidation generation it was published under,
    and a completed invalidation is never rolled back by an older publish."""
    cache = RefPayloadCache()
    cv = threading.Condition()
    turn = {"n": 0}
    inv_count = {"n": 0}
    stored_gens: list[int] = []
    rounds = 60

    def builder():
        for i in range(rounds):
            with cv:
                while turn["n"] % 2 != 0:
                    assert cv.wait(timeout=5), "builder starved"
            epoch = cache.current_epoch(_SID, _REF)
            if cache.put_if_current(_SID, _REF, {"gen": epoch, "builder": i}, 16, epoch):
                stored_gens.append(epoch)
            with cv:
                turn["n"] += 1
                cv.notify_all()

    def invalidator():
        for _ in range(rounds):
            with cv:
                while turn["n"] % 2 != 1:
                    assert cv.wait(timeout=5), "invalidator starved"
            cache.invalidate(_SID, _REF)
            inv_count["n"] += 1
            assert cache.get(_SID, _REF) is None, (
                "a completed invalidation must leave no readable entry"
            )
            with cv:
                turn["n"] += 1
                cv.notify_all()

    tb = threading.Thread(target=builder, name="builder")
    ti = threading.Thread(target=invalidator, name="invalidator")
    tb.start()
    ti.start()
    tb.join(timeout=5)
    ti.join(timeout=5)
    assert not tb.is_alive() and not ti.is_alive()

    # Every fresh capture published exactly once, under strictly increasing
    # generations 0..rounds-1 (each generation's payload is the only one ever
    # readable between its publish and its invalidation).
    assert stored_gens == list(range(rounds))
    assert inv_count["n"] == rounds
    assert cache.get(_SID, _REF) is None

    # After arbitrary churn, a fresh build can succeed immediately.
    fresh = cache.current_epoch(_SID, _REF)
    assert cache.put_if_current(_SID, _REF, {"gen": fresh}, 16, fresh) is True
    assert cache.get(_SID, _REF)["gen"] == fresh


def test_concurrent_put_invalidate_hammer_ends_leak_free():
    """Unordered concurrent hammering (real overlap, bounded rounds): whatever
    the interleaving, a final quiescing invalidation must leave the cache
    empty with byte/entry accounting intact — contention may not leak."""
    cache = RefPayloadCache()

    def builder():
        for _ in range(500):
            epoch = cache.current_epoch(_SID, _REF)
            cache.put_if_current(_SID, _REF, {"gen": epoch}, 16, epoch)

    def invalidator():
        for _ in range(500):
            cache.invalidate(_SID, _REF)

    tb = threading.Thread(target=builder, name="hammer-builder")
    ti = threading.Thread(target=invalidator, name="hammer-invalidator")
    tb.start()
    ti.start()
    tb.join(timeout=10)
    ti.join(timeout=10)
    assert not tb.is_alive() and not ti.is_alive()

    cache.invalidate(_SID, _REF)  # quiesce
    assert cache.get(_SID, _REF) is None
    assert len(cache) == 0
    assert cache.total_bytes == 0, "byte accounting leaked under contention"


# ── 3. session-level drop isolation ─────────────────────────────────────────


def test_invalidate_session_drops_only_that_session():
    cache = RefPayloadCache()
    cache.put("s1", "r1", {"a": 1}, 16)
    cache.put("s1", "r2", {"a": 2}, 16)
    cache.put("s2", "r1", {"b": 1}, 16)

    cache.invalidate_session("s1")

    assert cache.get("s1", "r1") is None
    assert cache.get("s1", "r2") is None
    assert cache.get("s2", "r1") == {"b": 1}, "other sessions must survive"
    assert len(cache) == 1
    assert cache.total_bytes == 16, "byte accounting must track dropped entries"


# ── 4. statistics: revision flow + planner pass-through honesty ─────────────


def test_statistics_store_revision_flow():
    """invalidate(F) → miss; repopulated stats for F are served NEW — the
    cache is a performance optimization whose correctness never depends on
    the TTL (explicit invalidation is the correctness mechanism)."""
    from app.services.data_fabric.query.statistics import (
        ColumnStatistics,
        DatasetStatistics,
        StatisticsStore,
    )

    store = StatisticsStore(ttl_s=60.0)
    fp = "fp-revision"
    v1 = DatasetStatistics(
        dataset_fingerprint=fp,
        row_count=100,
        columns=[ColumnStatistics(name="zone", ndv=50, confidence="measured")],
    )
    store.put(v1)
    assert store.get(fp) is v1

    store.invalidate(fp)
    assert store.get(fp) is None, "invalidated stats must not be served"

    v2 = DatasetStatistics(
        dataset_fingerprint=fp,
        row_count=250,
        columns=[ColumnStatistics(name="zone", ndv=10, confidence="measured")],
    )
    store.put(v2)
    got = store.get(fp)
    assert got is v2, "revised stats must replace, not merge with, the old snapshot"
    assert got.row_count == 250

    # Invalidation is per-fingerprint: unrelated fingerprints leave F intact.
    store.invalidate("fp-someone-else")
    assert store.get(fp) is v2


def test_plan_query_stats_are_pass_through_not_store_reads():
    """Planner honesty: statistics are caller-passed inputs, never read from
    the process-global store. A plan is therefore exactly as fresh as the
    stats object its caller supplies — flushing the global store cannot
    retroactively change (or refresh) an estimate built from stale stats."""
    from app.services.data_fabric.query.models import QuerySpecV2
    from app.services.data_fabric.query.planner import plan_query
    from app.services.data_fabric.query.statistics import (
        ColumnStatistics,
        DatasetStatistics,
        invalidate_statistics,
    )

    fp = "fp-passthrough"
    v1 = DatasetStatistics(
        dataset_fingerprint=fp,
        row_count=100,
        columns=[ColumnStatistics(name="zone", ndv=50, confidence="measured")],
    )
    v2 = DatasetStatistics(
        dataset_fingerprint=fp,
        row_count=100,
        columns=[ColumnStatistics(name="zone", ndv=10, confidence="measured")],
    )
    descriptor = SimpleNamespace(
        id="ds-1", source_type="postgis", feature_count=100_000,
        bbox=[0.0, 0.0, 1.0, 1.0], metadata={},
    )
    spec = QuerySpecV2(filter={"op": "eq", "field": "zone", "value": "x"})

    plan_old = plan_query(spec, descriptor, stats=v1)
    plan_new = plan_query(spec, descriptor, stats=v2)
    assert plan_old.estimated_rows == 2_000, "100k rows x 1/50 ndv selectivity"
    assert plan_new.estimated_rows == 10_000, "100k rows x 1/10 ndv selectivity"

    invalidate_statistics(fp)  # global-store flush between the two planner calls
    plan_after = plan_query(spec, descriptor, stats=v1)
    assert plan_after.estimated_rows == plan_old.estimated_rows, (
        "planner must estimate from the caller-passed stats only"
    )
    assert plan_after.statistics_confidence == "measured"


def test_put_if_current_atomic_against_concurrent_invalidate():
    """回归（GeoCompute V3）：put_if_current 的 epoch 检查与插入必须原子。

    历史 TOCTOU：检查持锁、put 再拿锁 —— invalidate 恰好在两段之间发生时，
    过期 payload 会覆盖失效结果复活 5s。此处以并发压力验证不变量：
    任何 invalidate 之后，携带旧 epoch 的 put_if_current 永不得写入。
    """
    import threading

    cache = RefPayloadCache(ttl=30.0)
    stop = threading.Event()
    resurrects = []

    def invalidator():
        n = 0
        while not stop.is_set():
            cache.invalidate("s", "r")
            n += 1
        invalidator.count = n

    def builder():
        while not stop.is_set():
            epoch = cache.current_epoch("s", "r")
            cache.put_if_current("s", "r", {"gen": epoch}, 10, epoch)
            entry = cache._entries.get(("s", "r"))
            if entry is not None and entry[0]["gen"] < cache._epochs.get(("s", "r"), 0):
                # 可能是 invalidate 进行中的瞬态；真幽灵会持续存在
                _t.sleep(0.02)
                entry2 = cache._entries.get(("s", "r"))
                if entry2 is not None and entry2[0]["gen"] < cache._epochs.get(("s", "r"), 0):
                    resurrects.append((entry2[0]["gen"], cache._epochs.get(("s", "r"), 0)))

    import time as _t2

    threads = [threading.Thread(target=invalidator)] + [
        threading.Thread(target=builder) for _ in range(4)
    ]
    for t in threads:
        t.start()
    import time as _t

    _t.sleep(0.5)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert not resurrects, f"stale resurrection observed: {resurrects[:3]}"
    assert invalidator.count > 10  # 压力真实发生


def test_invalidate_session_bumps_epochs_for_inflight_builders():
    """invalidate_session 必须递增 epoch：否则会话清空后，在飞构建者仍能发布。"""
    cache = RefPayloadCache(ttl=30.0)
    cache.put("s", "a", {"v": 1}, 10)
    stale_epoch = cache.current_epoch("s", "a")

    cache.invalidate_session("s")
    assert cache.get("s", "a") is None
    # 捕获旧 epoch 的在飞构建者不得复活
    assert cache.put_if_current("s", "a", {"v": 2}, 10, stale_epoch) is False
    assert cache.get("s", "a") is None
    # 新 epoch 的构建者正常发布
    new_epoch = cache.current_epoch("s", "a")
    assert cache.put_if_current("s", "a", {"v": 3}, 10, new_epoch) is True
    assert cache.get("s", "a") == {"v": 3}
