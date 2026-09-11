"""GeoCompute V8 — 验收集成测试（Goal 05 验收标准逐条锚定）。

一条 composite 场景串起验收：enforcing 账本下内存超限 run 被预防性拒绝
（不崩溃、可观测）、GPU run 在无 GPU worker 时按 fallback 语义降级并在
CPU 路径完成、vector 与 raster workload 走通分区 fan-out 分布式路径、
worker 丢失后 attempt 恢复语义与既有 V6 契约共存。
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def cluster_env(tmp_path, monkeypatch):
    """coordinator + jobs + 复用/证据/事件 同库；SOURCE_SCAN 桩。"""
    eng = create_engine(f"sqlite:///{tmp_path / 'v8-accept.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)

    @contextlib.contextmanager
    def fake_db_session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    import app.services.jobs.submit as submit_mod
    import app.services.jobs.worker as worker_mod
    from app.services.geocompute import durable, reuse_index, run_evidence
    from app.services.geocompute.cluster import store as csm
    from app.services.geocompute.cluster.scheduler import ClusterCoordinator
    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("raster_path"):
            return {"raster_path": params["raster_path"],
                    "metadata": {"via": "stub"}}
        return {"features": params.get("features") or [],
                "metadata": {"via": "stub"}}

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)
    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session,
                        raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(worker_mod, "_default_session_factory",
                            lambda: fake_db_session())
    monkeypatch.setattr(durable, "session_factory", lambda: fake_db_session())
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)

    from app.services.geocompute.cluster.store import ClusterLedger, ClusterRunStore

    def make_coord(name, ledger=None, **kw):
        return ClusterCoordinator(
            store=ClusterRunStore(factory), coordinator_id=name,
            local_slots=kw.pop("local_slots", 2),
            heartbeat_interval_s=0.05, tick_interval_s=0.02,
            leadership_ttl_s=1.0, lease_ttl_s=5.0,
            ledger=ledger, **kw)

    yield {"factory": factory, "store_factory": ClusterRunStore,
           "ledger": ClusterLedger, "make_coord": make_coord}
    eng.dispose()


def _submit(cluster_env, plan_dict, **kw):
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    plan = ExecutionPlan.model_validate(plan_dict)
    graph.validate_plan(plan)
    store = cluster_env["store_factory"](cluster_env["factory"])
    # 每 run 独立 session（并发 run 共享 session 会互相驱逐 refs ——
    # 真实提交语义也是一 run 一会话）
    kw.setdefault("session_id", f"s-accept-{uuid.uuid4().hex[:8]}")
    return store.create_run(
        plan_snapshot=plan.model_dump(),
        plan_fingerprint=plan.graph_fingerprint(),
        owner_scope=kw.pop("owner_scope", "u:accept"), **kw)


def _wait_terminal(store, coord, run_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            coord.tick()
        except Exception:
            pass
        row = store.get_run(run_id)
        if row and row["status"] in {"completed", "failed", "cancelled"}:
            return row["status"]
        time.sleep(0.02)
    return "timeout"


import time  # noqa: E402
import uuid  # noqa: E402


class TestAcceptanceLowMemoryRejection:
    def test_low_memory_run_rejected_not_crashed(self, cluster_env):
        """模拟低内存集群：plan 估计内存 > enforcing 账本限额 → run 留队
        （拒绝，不启动），waiting_resource[resource:mem_mb] 事件 +
        oom_avoided 量化 —— 「低内存自动拒绝而不是崩溃」验收锚点。"""
        import app.services.geocompute.cluster.metrics as metrics_mod

        saved = (metrics_mod._resource_rejections, metrics_mod._oom_avoided)
        metrics_mod._resource_rejections = {}
        metrics_mod._oom_avoided = 0
        try:
            factory = cluster_env["factory"]
            store = cluster_env["store_factory"](factory)
            ledger = cluster_env["ledger"](
                enforcing=True,
                limits={"global": {"rows": None, "bytes": None, "units": 8,
                                   "mem_mb": 512, "gpu": None}},
                factory=factory,
            )
            rid = _submit(cluster_env, {
                "plan_id": "acc-mem", "nodes": [
                    {"node_id": "heavy", "category": "filter",
                     "operation": "eq", "inputs": [],
                     "parameters": {"predicate": {"op": "eq", "field": "k",
                                                  "value": "a"},
                                    "features": []},
                     "policy": "durable_job",
                     "estimate": {"rows": 10, "memory_mb": 8192,
                                  "confidence": "assumption"}},
                ], "budget": {}})
            coord = cluster_env["make_coord"]("coord-acc", ledger=ledger)
            for _ in range(2):
                coord.tick()
            row = store.get_run(rid)
            assert row["status"] == "queued"  # 留队 = 拒绝而非崩溃
            from app.services.geocompute.cluster.events import RunEventStore

            evs = RunEventStore().window(rid)
            waiting = [e for e in evs if e["event"] == "waiting_resource"]
            assert waiting and waiting[0]["status"] == "resource:mem_mb"
            snap = metrics_mod.ClusterMetrics(store).snapshot()
            assert snap["oom_avoided"] >= 1
        finally:
            metrics_mod._resource_rejections, metrics_mod._oom_avoided = saved


class TestAcceptanceGpuFallbackStablePath:
    def test_non_gpu_path_stable_without_gpu_workers(self, cluster_env):
        """无 GPU worker 的集群里，非 GPU run 稳定完成；GPU run 声明
        fallback_cpu 时被持久降级到 CPU 路径（同一 coordinator）。"""
        factory = cluster_env["factory"]
        store = cluster_env["store_factory"](factory)
        coord = cluster_env["make_coord"]("coord-gpu-acc")
        feats = [{"type": "Feature", "geometry": {"type": "Point",
                                                  "coordinates": [1.0, 1.0]},
                  "properties": {"kind": "a"}}]
        # CPU run（无 envelope）：稳定完成
        rid_cpu = _submit(cluster_env, {
            "plan_id": "acc-cpu", "nodes": [
                {"node_id": "scan", "category": "source_scan",
                 "operation": "inline", "inputs": [],
                 "parameters": {"features": feats},
                 "policy": "durable_job"},
                {"node_id": "filt", "category": "filter", "operation": "eq",
                 "inputs": ["scan"], "policy": "durable_job",
                 "parameters": {"predicate": {"op": "eq", "field": "kind",
                                              "value": "a"}}},
            ], "budget": {"max_rows": 100000}})
        # GPU run + fallback
        rid_gpu = _submit(cluster_env, {
            "plan_id": "acc-gpu", "nodes": [
                {"node_id": "scan", "category": "source_scan",
                 "operation": "inline", "inputs": [],
                 "parameters": {"features": feats},
                 "policy": "durable_job"},
                {"node_id": "filt", "category": "filter", "operation": "eq",
                 "inputs": ["scan"], "policy": "durable_job",
                 "parameters": {"predicate": {"op": "eq", "field": "kind",
                                              "value": "a"}}},
            ], "budget": {"max_rows": 100000}},
            resource_request={"gpu": 1, "fallback_cpu": True,
                              "min_mem_mb": 0, "min_cpu": 0,
                              "required_profiles": []})
        coord._gpu_fallback_wait_s = 0.0  # 立即回退（测试注入）
        status_cpu = _wait_terminal(store, coord, rid_cpu)
        status_gpu = _wait_terminal(store, coord, rid_gpu)
        assert status_cpu == "completed"
        assert status_gpu == "completed"
        row = store.get_run(rid_gpu)
        assert row["resource_request"]["gpu"] == 0
        assert row["resource_request"]["fallback_from_gpu"] is True
        from app.services.geocompute.cluster.events import RunEventStore

        evs = RunEventStore().window(rid_gpu)
        assert any(e["event"] == "gpu_fallback" for e in evs)


class TestAcceptanceDistributedWorkloads:
    def test_vector_partitioned_distributed_run(self, cluster_env):
        """vector workload 经 coordinator 派发 → durable tile jobs →
        seam 合并 → run 完成（raster 版见
        test_geocompute_v8_partition.TestRasterPartitionE2E，rasterio
        heavy 环境逐像素校验）。"""
        factory = cluster_env["factory"]
        store = cluster_env["store_factory"](factory)
        coord = cluster_env["make_coord"]("coord-part")
        feats = []
        for i in range(12):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [116.0 + (i % 4) * 0.3,
                                             39.0 + (i // 4) * 0.3]},
                "properties": {"kind": "a", "i": i},
            })
        rid = _submit(cluster_env, {
            "plan_id": "acc-part", "nodes": [
                {"node_id": "scan", "category": "source_scan",
                 "operation": "inline", "inputs": [],
                 "parameters": {"features": feats},
                 "policy": "durable_job"},
                {"node_id": "filt", "category": "filter", "operation": "eq",
                 "inputs": ["scan"], "policy": "durable_job",
                 "parameters": {"predicate": {"op": "eq", "field": "kind",
                                              "value": "a"}},
                 "partition": {"scheme": "vector_grid", "target_tiles": 4,
                               "halo_ratio": 0.05, "min_rows_per_tile": 0}},
            ], "budget": {"max_rows": 100000}})
        status = _wait_terminal(store, coord, rid)
        assert status == "completed"
        from app.services.geocompute.cluster.events import RunEventStore

        evs = RunEventStore().window(rid)
        assert any(e["event"] == "partition_planned" for e in evs)

    def test_worker_loss_recovery_semantics_preserved(self, cluster_env):
        """worker 丢失恢复：lease 过期 reclaim → requeue（attempt 预算内）
        —— V6 契约与 V8 账本五维共存（mem/gpu 预留也精确归还）。"""
        from datetime import timedelta

        factory = cluster_env["factory"]
        store = cluster_env["store_factory"](factory)
        ledger = cluster_env["ledger"](enforcing=True, factory=factory)
        rid = _submit(cluster_env, {
            "plan_id": "acc-loss", "nodes": [
                {"node_id": "scan", "category": "source_scan",
                 "operation": "inline", "inputs": [],
                 "parameters": {"features": []},
                 "estimate": {"rows": 1, "memory_mb": 128,
                              "confidence": "high"}},
            ], "budget": {}})
        coord = cluster_env["make_coord"]("coord-loss", ledger=ledger)
        epoch = coord._store.claim_lease(
            rid, coordinator_id="coord-lost", ttl_s=30.0, ledger=ledger,
            return_detail=True)
        assert epoch[0] is not None
        # 模拟 worker 死亡：lease 过期 → coordinator reclaim
        from app.services.geocompute.cluster.store import _utcnow

        with factory() as db:
            import sqlalchemy as sa
            from app.models.db_model import GeoComputeClusterRun

            db.execute(
                sa.update(GeoComputeClusterRun)
                .where(GeoComputeClusterRun.run_id == rid)
                .values(lease_expires_at=_utcnow() - timedelta(seconds=1))
            )
            db.commit()
        reclaimed = coord._store.reclaim_expired(
            max_attempts=3, ledger=ledger)
        assert any(r["run_id"] == rid for r in reclaimed)
        row = store.get_run(rid)
        assert row["status"] == "queued"  # attempt 预算内回队
        # 账本守恒：mem/gpu 预留已随 reclaim 精确归还
        snap = store.ledger_snapshot()
        global_row = next(s for s in snap if s["scope_key"] == "global")
        assert global_row["usage_mem_mb"] == 0
        assert global_row["usage_gpu"] == 0
