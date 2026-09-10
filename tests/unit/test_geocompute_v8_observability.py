"""GeoCompute V8 — observability（Phase H）。

覆盖：events 有界聚合（count_kind / sum_bytes）、ClusterMetrics 快照
的 V8 投影（transfer/cache/lineage/utilization/quarantine/spill/
resource_rejections/oom_avoided/gpu_fallbacks）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def obs_env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v8-obs.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import events as ev_mod
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)
    monkeypatch.setattr(ev_mod, "session_factory", factory)
    event_store = ev_mod.RunEventStore()
    store = __import__(
        "app.services.geocompute.cluster.store",
        fromlist=["ClusterRunStore"]).ClusterRunStore(factory)

    metrics = __import__(
        "app.services.geocompute.cluster.metrics",
        fromlist=["ClusterMetrics"]).ClusterMetrics(store)
    # events store 同库
    metrics._event_store = event_store
    yield store, factory, event_store, metrics
    eng.dispose()


def _run(store, fingerprint="fp-obs"):
    return store.create_run(
        plan_snapshot={"plan_id": "p", "nodes": [], "budget": {}},
        plan_fingerprint=fingerprint, owner_scope="u:obs")


class TestEventAggregates:
    def test_count_kind_closed_vocabulary(self, obs_env):
        store, factory, events, _ = obs_env
        rid = _run(store)
        assert events.append(rid, "node_completed", rows=3) is True
        assert events.append(rid, "node_completed", rows=4) is True
        assert events.count_kind("node_completed") == 2
        assert events.count_kind("node_reused") == 0
        # 词表外事件恒 0（append 本身也会拒绝）
        assert events.count_kind("not_in_vocabulary") == 0

    def test_sum_bytes_transfer(self, obs_env):
        store, factory, events, _ = obs_env
        rid = _run(store)
        events.append(rid, "node_output_ready", bytes_=1000)
        events.append(rid, "node_output_ready", bytes_=250)
        events.append(rid, "node_output_ready", bytes_=None)
        assert events.sum_bytes() == 1250


class TestMetricsV8Snapshot:
    def test_v8_fields_present(self, obs_env):
        import app.services.geocompute.cluster.metrics as m

        store, factory, events, metrics = obs_env
        # 隔离进程内计数器（避免污染）
        monkey_clean = {"_resource_rejections": {}, "_oom_avoided": 0,
                        "_gpu_fallbacks": 0, "_spill_count": 0,
                        "_spill_bytes": 0, "_spill_rehydrate": {True: 0, False: 0}}
        for k, v in monkey_clean.items():
            setattr(m, k, v)

        m.record_resource_rejection("mem_mb")
        m.record_oom_avoided()
        m.record_gpu_fallback()
        m.record_spill(2048)
        m.record_spill_rehydrate(True)

        snap = metrics.snapshot()
        assert snap["resource_rejections"] == {"mem_mb": 1}
        assert snap["oom_avoided"] == 1
        assert snap["gpu_fallbacks"] == 1
        assert snap["spill"] == {"count": 1, "bytes": 2048,
                                 "rehydrate_hits": 1, "rehydrate_misses": 0}
        assert "transfer" in snap and "bytes_total" in snap["transfer"]
        assert "worker_cache_hits" in snap["cache"]
        lineage = snap["lineage"]
        for key in ("node_completed", "node_reused", "node_lost",
                    "partition_planned", "speculative_dispatched",
                    "poison_quarantined"):
            assert key in lineage
        assert set(snap["utilization"]) == {"reserved_units",
                                            "capacity_units", "ratio"}
        assert isinstance(snap["quarantine"], list)

    def test_worker_utilization_projection(self, obs_env):
        store, factory, events, metrics = obs_env
        store.upsert_worker("w1", profiles={"celery": 2, "raster_queue": 1},
                            capability={"cpu_cores": 4, "mem_mb": 8192,
                                        "gpu_count": 0, "gpu_mem_mb": 0,
                                        "backends": {}, "capabilities": [],
                                        "zone": "default", "version": "v"})
        snap = metrics.snapshot()
        util = snap["utilization"]
        assert util["capacity_units"] == 3
        assert util["ratio"] is not None

    def test_lineage_counts_events(self, obs_env):
        store, factory, events, metrics = obs_env
        rid = _run(store, "fp-lineage")
        events.append(rid, "node_reused")
        events.append(rid, "node_reused")
        events.append(rid, "node_completed")
        snap = metrics.snapshot()
        assert snap["lineage"]["node_reused"] >= 2
        assert snap["lineage"]["node_completed"] >= 1

    def test_quarantine_snapshot_projection(self, obs_env):
        from app.services.geocompute.cluster.quarantine import (
            TaskQuarantine,
            reset_quarantine_for_tests,
        )

        store, factory, events, metrics = obs_env
        reset_quarantine_for_tests()
        q = TaskQuarantine(factory=factory, threshold=1, cooldown_s=600)
        q.record_failure("u:obs", "abcdef0123456789", error_code="X")
        metrics._quarantine = lambda: q
        snap = metrics.snapshot()
        assert len(snap["quarantine"]) == 1
        assert snap["quarantine"][0]["active"] is True
        reset_quarantine_for_tests()
