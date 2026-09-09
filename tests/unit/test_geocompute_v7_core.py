"""GeoCompute V7 核心单元测试 —— capability 探针/放置管线/事件 store/
局部性注册表（wave 13）。

测试纪律与 V6 相同：真 SQLite 临时库验证存储行为；纯函数层直测；
观测路径全部验证 fail-open（观测失败绝不倒灌执行）。
"""
from __future__ import annotations

import json
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_model import GeoComputeRunEvent as Ev
from app.models.db_model import GeoComputeWorkerCache as WCache
from app.services.geocompute.cluster.capabilities import (
    WorkerCapabilityProfile,
    capability_from_row,
    capability_json,
    probe_capability,
)
from app.services.geocompute.cluster.contracts import ResourceRequest
from app.services.geocompute.cluster.events import (
    EVENT_VOCABULARY,
    MAX_NODE_EVENTS_PER_RUN,
    RunEventStore,
    counters_snapshot,
)
from app.services.geocompute.cluster.locality import (
    MAX_CACHE_ENTRIES_PER_WORKER,
    WorkerCacheRegistry,
    cache_key_for,
    locality_keys,
)
from app.services.geocompute.cluster.placement import (
    eligible_workers,
    rank_workers,
    request_digest,
    request_from_run_row,
)
from app.services.geocompute.plan import (
    ExecutionNode,
    NodeCategory,
)


@pytest.fixture()
def factory(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v7-core.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


@pytest.fixture()
def events(factory, monkeypatch):
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(csm, "session_factory", factory)
    return RunEventStore()


@pytest.fixture()
def registry(factory, monkeypatch):
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(csm, "session_factory", factory)
    return WorkerCacheRegistry()


def _node(**kw) -> ExecutionNode:
    base = dict(node_id="n1", category=NodeCategory.QUERY, parameters={})
    base.update(kw)
    return ExecutionNode(**base)


# ── capabilities ─────────────────────────────────────────────────────────

class TestCapabilities:
    def test_probe_honest_fields(self):
        cap = probe_capability()
        assert cap.cpu_cores >= 0 and cap.mem_mb >= 0
        assert cap.gpu_count >= 0
        vocab = __import__(
            "app.services.geocompute.cluster.capabilities",
            fromlist=["CAPABILITY_VOCABULARY"]).CAPABILITY_VOCABULARY
        assert set(cap.capabilities) <= set(vocab)
        # 诚实性（round1 M3）：能力词由可证事实推导
        caps = set(cap.capabilities)
        if cap.gpu_count == 0:
            assert "gpu" not in caps
        else:
            assert "gpu" in caps
        if "rasterio" in sys.modules:
            assert "raster" in caps  # 已安装的后端必须申报
        if cap.cpu_cores >= 4:
            assert "heavy_cpu" in caps
        if cap.cpu_cores > 0:
            assert "light_cpu" in caps
        assert cap.zone
        assert cap.version.startswith("v")

    def test_satisfies(self):
        cap = WorkerCapabilityProfile(cpu_cores=4, mem_mb=8192, gpu_count=1)
        assert cap.satisfies()
        assert cap.satisfies(min_cpu=4, min_mem_mb=8192, gpu=1)
        assert not cap.satisfies(min_cpu=8)
        assert not cap.satisfies(min_mem_mb=16384)
        assert not cap.satisfies(gpu=2)
        # 缺省 profile（全 0）只满足零要求
        assert WorkerCapabilityProfile().satisfies()
        assert not WorkerCapabilityProfile().satisfies(gpu=1)

    def test_json_roundtrip_and_clamp(self):
        cap = probe_capability()
        blob = capability_json(cap)
        assert blob is not None
        assert capability_from_row(blob) is not None
        # 超界投影 → None（诚实降级为 V6 语义）
        huge = WorkerCapabilityProfile(
            backends={f"b{i}": "x" * 500 for i in range(16)},
        )
        assert capability_json(huge) is None
        # 损坏行 → None
        assert capability_from_row({"cpu_cores": "not-an-int"}) is None
        assert capability_from_row(None) is None
        assert capability_from_row("junk") is None


# ── placement ────────────────────────────────────────────────────────────

def _worker(wid="w1", *, profiles=None, capability=None):
    return {"worker_id": wid, "role": "worker",
            "profiles": profiles or {"celery": 1},
            "capability": capability, "heartbeat_age_s": 1.0}


class TestPlacement:
    def test_request_from_row_v6_compat(self):
        row = {"required_profiles": ["raster"]}
        req = request_from_run_row(row)
        assert req.required_profiles == ["raster"]
        assert req.gpu == 0 and req.min_mem_mb == 0
        # 脏数据 → 诚实降级
        row2 = {"required_profiles": ["raster"], "resource_request": {"gpu": "bad"}}
        assert request_from_run_row(row2).required_profiles == ["raster"]

    def test_eligible_profiles_and_capability(self):
        cap = WorkerCapabilityProfile(cpu_cores=4, mem_mb=4096, gpu_count=1)
        cap_row = capability_json(cap)
        w = _worker(profiles={"raster": 1}, capability=cap_row)
        req = ResourceRequest(required_profiles=["raster"])
        assert len(eligible_workers(req, [w])) == 1
        # profile 不覆盖 → 不合格
        assert eligible_workers(
            ResourceRequest(required_profiles=["raster", "science"]),
            [_worker(profiles={"raster": 1})],
        ) == []
        # envelope 超出能力 → 不合格
        assert eligible_workers(
            ResourceRequest(required_profiles=["raster"], gpu=2),
            [_worker(profiles={"raster": 1},
                     capability=capability_json(cap))],
        ) == []
        # capability 缺席 → 只按 profiles（V6 兼容语义）
        assert len(eligible_workers(
            ResourceRequest(required_profiles=["raster"], gpu=8),
            [_worker(profiles={"raster": 1}, capability=None)],
        )) == 1
        # round1 C2 回归锚：capability.capabilities **不**放宽队列覆盖 ——
        # 声明 raster 后端但不消费 raster_queue 的 worker 不合格
        assert eligible_workers(
            ResourceRequest(required_profiles=["raster"]),
            [_worker(profiles={"celery": 1},
                     capability={"cpu_cores": 8, "mem_mb": 32768,
                                 "gpu_count": 0, "gpu_mem_mb": 0,
                                 "backends": {"raster": "1.0"},
                                 "capabilities": ["raster"],
                                 "zone": "default", "version": "v"})],
        ) == []

    def test_rank_locality_then_overprovision(self, registry):
        keys = frozenset({"fp-a"})
        cap_big = capability_json(WorkerCapabilityProfile(
            cpu_cores=64, mem_mb=262144, gpu_count=0))
        cap_fit = capability_json(WorkerCapabilityProfile(
            cpu_cores=2, mem_mb=2048, gpu_count=0))
        workers = [
            _worker("w-big", profiles={"raster": 1}, capability=cap_big),
            _worker("w-fit", profiles={"raster": 1}, capability=cap_fit),
        ]
        lookup = lambda wid, owner, ks: 1 if wid == "w-fit" else 0  # noqa: E731
        ranked = rank_workers(
            ResourceRequest(required_profiles=["raster"]), workers,
            owner_scope="u:x", node_locality_keys=keys,
            locality_lookup=lookup,
        )
        assert ranked[0]["worker_id"] == "w-fit"

    def test_rank_without_locality_lookup(self):
        workers = [_worker("w1"), _worker("w2")]
        ranked = rank_workers(ResourceRequest(), workers)
        assert {w["worker_id"] for w in ranked} == {"w1", "w2"}

    def test_request_digest_bounded(self):
        d = request_digest(ResourceRequest(min_mem_mb=1024, gpu=1,
                                           required_profiles=["raster"]))
        assert len(json.dumps(d)) < 1024


# ── events ───────────────────────────────────────────────────────────────

class TestEvents:
    def test_append_and_window_monotonic(self, events, factory):
        assert events.append("r1", "run_started")
        assert events.append("r1", "node_started", node_id="n1")
        assert events.append("r1", "node_completed", node_id="n1", rows=5)
        window = events.window("r1")
        assert [e["event"] for e in window] == [
            "run_started", "node_started", "node_completed"]
        ids = [e["id"] for e in window]
        assert ids == sorted(ids)
        # 断点续读
        assert [e["event"] for e in events.window("r1", after_id=ids[0])] == [
            "node_started", "node_completed"]

    def test_vocabulary_enforced(self, events):
        assert not events.append("r1", "made_up_event")
        assert counters_snapshot()["event_rejected_invalid"] >= 1

    def test_node_budget_with_run_level_exempt(self, events, factory):
        for i in range(MAX_NODE_EVENTS_PER_RUN + 5):
            ok = events.append("r2", "node_started", node_id=f"n{i % 8}")
        assert not ok  # 超限丢弃
        assert counters_snapshot()["event_budget_exhausted_total"] >= 1
        # run 级/治理事件豁免
        assert events.append("r2", "run_failed", error_code="X")
        assert events.append("r2", "waiting_resource", status="no_worker")
        assert events.append("r2", "straggler_detected")

    def test_orphan_append_fail_open(self, events):
        # run 行不存在（retention 后孤儿 append）→ 行为化断言：append
        # 成功落表、窗口可读、全程不抛（trace 域独立 TTL 兜底清理）
        assert events.append("ghost-run", "node_started", node_id="n") is True
        window = events.window("ghost-run")
        assert [e["event"] for e in window] == ["node_started"]

    def test_progress_projection_dedup_across_attempts(self, events):
        events.append("r3", "node_started", node_id="a")
        events.append("r3", "node_completed", node_id="a", rows=1)
        # attempt 2 失败覆盖此前完成 → done 不含 a
        events.append("r3", "node_failed", node_id="a", error_code="X")
        events.append("r3", "node_completed", node_id="b", rows=2)
        events.append("r3", "node_reused", node_id="c")
        proj = events.progress_projection("r3")
        assert proj["settled"] == 3
        assert proj["done"] == 2  # b + c（a 被最新失败覆盖）
        assert proj["failed"] == 1

    def test_purge_older_than(self, events, factory):
        events.append("r4", "run_started")
        with factory() as db:
            from app.services.geocompute.cluster.store import _utcnow
            from datetime import timedelta

            db.query(Ev).update({Ev.created_at: _utcnow() - timedelta(hours=48)})
            db.commit()
        assert events.purge_older_than(older_than_s=3600) >= 1
        assert events.window("r4") == []

    def test_vocabulary_closed(self):
        assert "run_completed" in EVENT_VOCABULARY
        assert "node_lost" in EVENT_VOCABULARY
        assert "worker_cache_hit" in EVENT_VOCABULARY


# ── locality ─────────────────────────────────────────────────────────────

class TestLocality:
    def test_locality_keys_sources(self):
        node = _node(
            dataset_fingerprints={"ds": "fp-123"},
            parameters={"data_object_id": "a" * 64, "query": {"q": 1}},
        )
        keys = locality_keys(node)
        assert "fp-123" in keys
        assert ("a" * 64) in keys
        # data_object_id 非 sha256 → 拒绝（无注入面）
        node_bad = _node(parameters={"data_object_id": "../../etc"})
        assert locality_keys(node_bad) == frozenset()
        # 有界 ≤16
        many = _node(dataset_fingerprints={
            f"k{i}": f"fp-{i}" for i in range(32)})
        assert len(locality_keys(many)) <= 16

    def test_owner_scoped_keys_never_shared(self):
        k1 = cache_key_for("u:alice", "fp-1")
        k2 = cache_key_for("u:bob", "fp-1")
        assert k1 != k2

    def test_registry_put_hit_and_isolation(self, registry):
        registry.record_put("w1", "u:alice", "fp-1", size_bytes=100)
        assert registry.worker_holds("w1", "u:alice", ["fp-1"]) == 1
        # 跨 owner 不可见
        assert registry.worker_holds("w1", "u:bob", ["fp-1"]) == 0
        assert registry.worker_entries("w1") == 1
        assert registry.worker_cached_bytes("w1") == 100

    def test_registry_lru_eviction(self, registry):
        for i in range(MAX_CACHE_ENTRIES_PER_WORKER + 4):
            registry.record_put("w1", "u:o", f"fp-{i}", size_bytes=1)
        assert registry.worker_entries("w1") == MAX_CACHE_ENTRIES_PER_WORKER

    def test_registry_drop_worker(self, registry):
        registry.record_put("w1", "u:o", "fp-1")
        assert registry.drop_worker("w1") == 1
        assert registry.worker_entries("w1") == 0

    def test_registry_corrupt_row_is_miss(self, registry, factory):
        # 损坏行（键不符词表）→ worker_holds 查不到（miss 方向安全）
        with factory() as db:
            db.add(WCache(worker_id="w2", cache_key="not-a-hash",
                          owner_scope="o:x", size_bytes=1,
                          cached_at=__import__("datetime").datetime.utcnow(),
                          last_hit_at=__import__("datetime").datetime.utcnow()))
            db.commit()
        assert registry.worker_holds("w2", "u:o", ["fp-1"]) == 0

    def test_ttl_purge(self, registry, factory):
        registry.record_put("w1", "u:o", "fp-old")
        with factory() as db:
            from app.services.geocompute.cluster.store import _utcnow
            from datetime import timedelta

            db.query(WCache).update(
                {WCache.cached_at: _utcnow() - timedelta(hours=48)})
            db.commit()
        assert registry.purge_older_than(older_than_s=3600) == 1
        assert registry.worker_entries("w1") == 0


# ── worker cache (process LRU) ──────────────────────────────────────────

class TestWorkerPayloadCache:
    def test_put_get_isolated_by_owner(self):
        from app.services.geocompute.cluster.object_cache import PayloadCache

        cache = PayloadCache(max_entries=8, max_bytes=10 ** 6)
        payload = {"features": [{"id": 1}]}
        cache.put("s1", "ref1", "u:alice", payload)
        assert cache.get("s1", "ref1", "u:alice") is not None
        # 跨 owner miss（缓存身份含 owner 域）
        assert cache.get("s1", "ref1", "u:bob") is None

    def test_lru_bound_and_oversize_skip(self):
        from app.services.geocompute.cluster.object_cache import PayloadCache

        cache = PayloadCache(max_entries=2, max_bytes=10 ** 6)
        cache.put("s", "r1", "u", {"rows": [{"a": 1}]})
        cache.put("s", "r2", "u", {"rows": [{"a": 2}]})
        cache.put("s", "r3", "u", {"rows": [{"a": 3}]})
        assert cache.stats()["entries"] == 2
        big = PayloadCache(max_entries=8, max_bytes=10)
        big.put("s", "r-big", "u", {"rows": [{"a": "x" * 100}]})
        assert big.stats()["entries"] == 0  # 超预算不入缓存

    def test_registry_wiring_fail_open(self):
        from app.services.geocompute.cluster.object_cache import PayloadCache

        class _Boom:
            def record_put(self, *a, **k):
                raise RuntimeError("registry down")

            def record_hit(self, *a, **k):
                raise RuntimeError("registry down")

        cache = PayloadCache(registry=_Boom(), worker_id="w1")
        cache.put("s", "r", "u", {"rows": [1]}, locality_key="fp-1")
        assert cache.get("s", "r", "u", locality_key="fp-1") is not None


# ── ResourceRequest contract ─────────────────────────────────────────────

class TestResourceRequest:
    def test_defaults_are_v6_noop(self):
        assert ResourceRequest() == ResourceRequest(
            min_mem_mb=0, min_cpu=0, gpu=0, zone=None,
            required_profiles=[])

    def test_forbid_extra_and_bounds(self):
        with pytest.raises(Exception):
            ResourceRequest(unbounded_gpu_farm=99)
        with pytest.raises(Exception):
            ResourceRequest(gpu=99)

    def test_normalized_filters_words(self):
        req = ResourceRequest(required_profiles=["raster", "nope"])
        assert req.normalized().required_profiles == ["raster"]

    def test_resource_class_mapping_untouched(self):
        # 节点级 resource_class → V6 队列路由语义不变（回归锚）
        from app.services.geocompute.durable import queue_for_node

        heavy = _node(category=NodeCategory.RASTER_OPERATION)
        assert queue_for_node(heavy) == "raster_queue"
