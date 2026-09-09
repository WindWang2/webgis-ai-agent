"""GeoCompute V7 集群/REST 层测试 —— resource envelope 提交、分布式事件
读投影、admin 面（workers/stuck/reset/ledger）、scheduler 放置 gating、
bridge 并发（wave 13）。

纪律与 V6 routes 测试相同：强制认证、owner 域隔离（他人/未知一律 404）、
store 指向临时 SQLite；admin 端点验证非 admin 403。
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app

client = TestClient(app)


def _auth(user_id: str, role: str = "editor") -> dict[str, str]:
    from app.core.auth import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token({'sub': user_id, 'role': role, 'org_id': 1})}"
    }


_AUTH = _auth("gc-v7-user")
_AUTH_OTHER = _auth("gc-v7-other")
_AUTH_ADMIN = _auth("gc-v7-admin", role="admin")


def _plan(nodes: int = 1, policy: str = "in_process") -> dict:
    return {
        "plan_id": "v7-plan",
        "nodes": [
            {
                "node_id": f"n{i}",
                "category": "filter",
                "operation": "op",
                "inputs": [],
                "parameters": {"features": [{"i": i}]},
                "policy": policy,
            }
            for i in range(nodes)
        ],
        "budget": {},
    }


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def v7_env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v7-rest.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import store as cluster_store_mod

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(cluster_store_mod, "session_factory", factory)
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.db_model import Organization, User

    db = SessionLocal()
    try:
        if db.get(Organization, 1) is None:
            db.add(Organization(id=1, name="gc-v7", slug="gc-v7-org"))
        for uid, uname, role in (
            ("gc-v7-user", "gc-v7-user", "editor"),
            ("gc-v7-admin", "gc-v7-admin", "admin"),
            ("gc-v7-other", "gc-v7-other", "editor"),
        ):
            u = db.get(User, uid)
            if u is None:
                db.add(User(
                    id=uid, username=uname, email=f"{uname}@example.com",
                    password_hash="scrypt$16384$8$1$00$00", role=role,
                    is_active=True, token_version=0, org_id=1,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ))
        db.commit()
    finally:
        db.close()
    yield factory
    eng.dispose()


# ── submit resource 字段 ─────────────────────────────────────────────────

class TestSubmitResource:
    def test_submit_without_resource_is_v6(self, v7_env):
        r = client.post("/api/v1/geocompute/plans/runs", json={
            "plan": _plan(), "session_id": None}, headers=_AUTH)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["resource"] is None
        assert body["required_profiles"] == []

    def test_submit_with_resource_union_profiles(self, v7_env):
        plan = _plan()
        plan["nodes"][0]["policy"] = "durable_job"
        plan["nodes"][0]["category"] = "query"
        r = client.post("/api/v1/geocompute/plans/runs", json={
            "plan": plan, "session_id": None,
            "resource": {"gpu": 1, "min_mem_mb": 2048,
                         "required_profiles": ["raster"]},
        }, headers=_AUTH)
        assert r.status_code == 202, r.text
        body = r.json()
        res = body["resource"]
        assert res["gpu"] == 1 and res["min_mem_mb"] == 2048
        # union 语义：raster（用户）∪ light_cpu（query 类别派生）
        assert set(res["required_profiles"]) >= {"raster", "light_cpu"}

    def test_submit_unknown_profile_word_422(self, v7_env):
        r = client.post("/api/v1/geocompute/plans/runs", json={
            "plan": _plan(), "session_id": None,
            "resource": {"required_profiles": ["quantum"]},
        }, headers=_AUTH)
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "RESOURCE_REQUEST_INVALID"

    def test_submit_envelope_oversize_clamped(self, v7_env):
        r = client.post("/api/v1/geocompute/plans/runs", json={
            "plan": _plan(), "session_id": None,
            "resource": {"zone": "z" * 300},
        }, headers=_AUTH)
        assert r.status_code == 422  # zone ≤64 由 pydantic 拒绝


# ── 分布式事件读投影 ─────────────────────────────────────────────────────

class TestRunEventsEndpoint:
    def _submit(self, factory) -> str:
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(factory)
        return store.create_run(
            plan_snapshot=_plan(), plan_fingerprint="fp-v7",
            owner_scope="u:" + __import__("hashlib").sha1(
                b"gc-v7-user", usedforsecurity=False).hexdigest()[:16],
            tenant_raw="org-1",
        )

    def test_events_owner_isolation_and_pagination(self, v7_env):
        factory = v7_env
        from app.services.geocompute.cluster.events import RunEventStore

        rid = self._submit(factory)
        es = RunEventStore()
        es.append(rid, "run_started")
        for i in range(5):
            es.append(rid, "node_started", node_id=f"n{i}")

        r = client.get(f"/api/v1/geocompute/runs/{rid}/events", headers=_AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 6
        # 分页：after_id 断点续读
        mid = body["events"][2]["id"]
        r2 = client.get(f"/api/v1/geocompute/runs/{rid}/events?after_id={mid}",
                        headers=_AUTH)
        assert r2.json()["count"] == 3
        # owner 隔离：他人 404（无存在性预言机）
        r3 = client.get(f"/api/v1/geocompute/runs/{rid}/events", headers=_AUTH_OTHER)
        assert r3.status_code == 404
        # 未知 run 404
        r4 = client.get("/api/v1/geocompute/runs/gexec-deadbeef/events",
                        headers=_AUTH)
        assert r4.status_code == 404
        # 未认证 401
        r5 = client.get(f"/api/v1/geocompute/runs/{rid}/events")
        assert r5.status_code in (401, 403)


# ── admin 面 ─────────────────────────────────────────────────────────────

class TestAdminSurface:
    def test_workers_admin_only_and_projection(self, v7_env):
        from app.services.geocompute.cluster.store import ClusterRunStore

        ClusterRunStore(v7_env).upsert_worker(
            "w-cap", profiles={"raster": 1},
            capability={"cpu_cores": 4, "mem_mb": 8192, "gpu_count": 1,
                        "gpu_mem_mb": 8192, "backends": {}, "capabilities": [
                            "raster", "gpu"], "zone": "z1", "version": "vtest"},
        )
        r = client.get("/api/v1/geocompute/cluster/workers", headers=_AUTH_ADMIN)
        assert r.status_code == 200, r.text
        workers = r.json()["workers"]
        assert workers and workers[0]["capability"]["gpu_count"] == 1
        # 非 admin → 403
        r2 = client.get("/api/v1/geocompute/cluster/workers", headers=_AUTH)
        assert r2.status_code == 403

    def test_stuck_and_reset_flow(self, v7_env):
        from app.services.geocompute.cluster.contracts import ClusterRunStatus
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(v7_env)
        rid = store.create_run(plan_snapshot=_plan(),
                               plan_fingerprint="fp", owner_scope="u:x")
        epoch = store.claim_lease(rid, coordinator_id="c1", ttl_s=30)
        store.mark_running(rid, epoch=epoch)
        # 未过期 → stuck 视图空、reset 拒绝（409）
        assert client.get("/api/v1/geocompute/cluster/runs/stuck",
                          headers=_AUTH_ADMIN).json()["count"] == 0
        r = client.post(f"/api/v1/geocompute/cluster/runs/{rid}/reset",
                        json={"reason": "test"}, headers=_AUTH_ADMIN)
        assert r.status_code == 409
        # lease 过期 → stuck 可见；reset → requeued（attempt+1）
        from app.services.geocompute.cluster.store import _utcnow
        from datetime import timedelta

        with v7_env() as db:
            from app.models.db_model import GeoComputeClusterRun as Run

            db.query(Run).filter(Run.run_id == rid).update({
                Run.lease_expires_at: _utcnow() - timedelta(seconds=60)})
            db.commit()
        stuck = client.get("/api/v1/geocompute/cluster/runs/stuck",
                           headers=_AUTH_ADMIN).json()
        assert stuck["count"] == 1 and stuck["runs"][0]["run_id"] == rid
        r2 = client.post(f"/api/v1/geocompute/cluster/runs/{rid}/reset",
                         json={"reason": "stuck"}, headers=_AUTH_ADMIN)
        assert r2.status_code == 200
        assert r2.json()["outcome"] == "requeued"
        assert r2.json()["attempts"] == 1
        # 非 admin → 403
        r3 = client.post(f"/api/v1/geocompute/cluster/runs/{rid}/reset",
                         json={}, headers=_AUTH)
        assert r3.status_code == 403
        # attempt 耗尽 → failed[WORKER_LOSS]
        rid2 = store.create_run(plan_snapshot=_plan(),
                                plan_fingerprint="fp2", owner_scope="u:x")
        ep = store.claim_lease(rid2, coordinator_id="c1", ttl_s=30)
        with v7_env() as db:
            from app.models.db_model import GeoComputeClusterRun as Run

            db.query(Run).filter(Run.run_id == rid2).update({
                Run.attempts: 3,
                Run.lease_expires_at: _utcnow() - timedelta(seconds=60)})
            db.commit()
        r4 = client.post(f"/api/v1/geocompute/cluster/runs/{rid2}/reset",
                         json={}, headers=_AUTH_ADMIN)
        assert r4.json()["outcome"] == "failed"
        assert store.get_run(rid2)["error_code"] == "WORKER_LOSS"

    def test_ledger_limits_admin(self, v7_env):
        r = client.post("/api/v1/geocompute/cluster/ledger/limits", json={
            "scope_key": "t:abc123", "limit_units": 4}, headers=_AUTH_ADMIN)
        assert r.status_code == 200, r.text
        # 词表外 scope → 422
        r2 = client.post("/api/v1/geocompute/cluster/ledger/limits", json={
            "scope_key": "evil; DROP", "limit_units": 4}, headers=_AUTH_ADMIN)
        assert r2.status_code == 422
        # 非 admin → 403
        r3 = client.post("/api/v1/geocompute/cluster/ledger/limits", json={
            "scope_key": "global", "limit_units": 1}, headers=_AUTH)
        assert r3.status_code == 403

    def test_metrics_extensions(self, v7_env):
        r = client.get("/api/v1/geocompute/cluster/metrics", headers=_AUTH_ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("queue_wait", "waiting_by_profile", "events_counters",
                    "workers"):
            assert key in body
        assert "gpu_workers" in body["workers"]


# ── scheduler 放置 gating ────────────────────────────────────────────────

class TestSchedulerGating:
    @pytest.fixture()
    def sched_env(self, tmp_path, monkeypatch):
        eng = create_engine(f"sqlite:///{tmp_path / 'v7-sched.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        from app.services.geocompute import reuse_index, run_evidence
        from app.services.geocompute.cluster import store as csm

        monkeypatch.setattr(run_evidence, "session_factory", factory)
        monkeypatch.setattr(reuse_index, "session_factory", factory)
        monkeypatch.setattr(csm, "session_factory", factory)
        from app.services.geocompute.cluster.scheduler import ClusterCoordinator

        def make(name: str, **kw):
            return ClusterCoordinator(
                store=__import__(
                    "app.services.geocompute.cluster.store",
                    fromlist=["ClusterRunStore"]).ClusterRunStore(factory),
                coordinator_id=name, local_slots=kw.pop("local_slots", 1),
                heartbeat_interval_s=0.05, tick_interval_s=0.02,
                leadership_ttl_s=1.0, lease_ttl_s=5.0, **kw)

        yield factory, make
        eng.dispose()

    def _submit_with_envelope(self, factory, resource) -> str:
        from app.services.geocompute.cluster.store import ClusterRunStore

        return ClusterRunStore(factory).create_run(
            plan_snapshot=_plan(), plan_fingerprint="fp-gpu",
            owner_scope="u:x", resource_request=resource,
        )

    def test_gpu_run_holds_without_gpu_worker(self, sched_env):
        factory, make = sched_env
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(factory)
        # 非 eager：worker 视图生效
        from app.services.task_queue import celery_app

        import contextlib

        with contextlib.ExitStack() as stack:
            # tick 里读 celery conf → 用 monkeypatch 不可行（无 fixture），
            # 直接置 conf（测试进程内可接受）
            old = celery_app.conf.task_always_eager
            celery_app.conf.task_always_eager = False
            try:
                store.upsert_worker("w-cpu", profiles={"celery": 1},
                                    capability={"cpu_cores": 4, "mem_mb": 8192,
                                                "gpu_count": 0, "gpu_mem_mb": 0,
                                                "backends": {},
                                                "capabilities": [],
                                                "zone": "default",
                                                "version": "v"})
                rid = self._submit_with_envelope(
                    factory, {"gpu": 1, "min_mem_mb": 0, "min_cpu": 0,
                              "required_profiles": []})
                coord = make("coord-gpu")
                for _ in range(3):
                    coord.tick()
                assert store.get_run(rid)["status"] == "queued"
                # waiting_resource 事件（一次性）
                from app.services.geocompute.cluster.events import RunEventStore

                evs = [e["event"] for e in RunEventStore().window(rid)]
                assert evs.count("waiting_resource") == 1
                # GPU worker 上线 → 派发
                store.upsert_worker("w-gpu", profiles={"celery": 1},
                                    capability={"cpu_cores": 8, "mem_mb": 32768,
                                                "gpu_count": 2, "gpu_mem_mb": 16384,
                                                "backends": {},
                                                "capabilities": ["gpu"],
                                                "zone": "default", "version": "v"})
                # 同一 coordinator（leadership lease 仍在任；worker 视图
                # 每 tick 重读 —— 容量收缩/扩张即时生效）
                stats = coord.tick()
                assert stats["dispatched"] == 1
                assert store.get_run(rid)["status"] in ("leased", "running")
            finally:
                celery_app.conf.task_always_eager = old

    def test_plain_run_dispatches_without_workers(self, sched_env):
        """V6 语义保留：无 profile/envelope 的 run 恒可派发（本地直跑）。"""
        factory, make = sched_env
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(factory)
        from app.services.task_queue import celery_app

        old = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = False
        try:
            rid = store.create_run(plan_snapshot=_plan(),
                                   plan_fingerprint="fp-plain",
                                   owner_scope="u:x")
            coord = make("coord-plain")
            stats = coord.tick()
            assert stats["dispatched"] == 1
        finally:
            celery_app.conf.task_always_eager = old


# ── purge 级联 ───────────────────────────────────────────────────────────

class TestPurgeCascade:
    def test_purge_terminal_deletes_events_same_transaction(self, tmp_path):
        eng = create_engine(f"sqlite:///{tmp_path / 'purge.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        from app.services.geocompute.cluster import store as csm

        old = csm.session_factory
        csm.session_factory = factory
        try:
            from datetime import timedelta

            from app.services.geocompute.cluster.events import RunEventStore
            from app.services.geocompute.cluster.store import (
                ClusterRunStore,
                _utcnow,
            )

            store = ClusterRunStore(factory)
            rid = store.create_run(plan_snapshot=_plan(),
                                   plan_fingerprint="fp", owner_scope="u:x")
            epoch = store.claim_lease(rid, coordinator_id="c", ttl_s=30)
            store.mark_running(rid, epoch=epoch)
            store.finish_run(rid, epoch=epoch,
                             status=__import__(
                                 "app.services.geocompute.cluster.contracts",
                                 fromlist=["ClusterRunStatus"]).ClusterRunStatus.COMPLETED)
            es = RunEventStore()
            es.append(rid, "run_started")
            es.append(rid, "node_completed", node_id="n1")
            # 终态时间回拨 → purge
            with factory() as db:
                from app.models.db_model import GeoComputeClusterRun as Run

                db.query(Run).filter(Run.run_id == rid).update({
                    Run.terminal_at: _utcnow() - timedelta(hours=48)})
                db.commit()
            deleted = store.purge_terminal(older_than_s=3600)
            assert deleted == 1
            assert es.window(rid) == []  # 级联删除
            assert store.get_run(rid) is None
        finally:
            csm.session_factory = old
        eng.dispose()


# ── bridge 并发（结构：并发加速比 > 2）──────────────────────────────────

class TestBridgeConcurrency:
    def test_concurrent_bridging_is_not_serialized(self):
        """结构基准（自相对，抗环境负载）：同线程先实测 8 次**串行**基线，
        再测 8 线程并发 —— V6 `_SERIAL` 下两者相等（进程级串行）；V7 专用
        loop 协作并发 → 并发耗时 < 串行 × 1/2。断言的是**相对加速比**，
        不是绝对墙钟（并发 Epic 资源纪律：结构性预算优先）。"""
        import asyncio

        from app.services.geocompute._async_bridge import run_coro_sync

        N = 8
        SLEEP_S = 0.02

        def _one() -> None:
            async def _coro():
                await asyncio.sleep(SLEEP_S)

            run_coro_sync(_coro())

        _one()  # loop 预热（懒启动成本不进基准）

        serial_start = time.monotonic()
        for _ in range(N):
            _one()
        serial_s = time.monotonic() - serial_start

        threads = [threading.Thread(target=_one) for _ in range(N)]
        concur_start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        concur_s = time.monotonic() - concur_start

        assert concur_s < serial_s * 0.5, (
            f"bridge 并发加速比不足（并发 {concur_s:.3f}s ≥ 串行 "
            f"{serial_s:.3f}s × 1/2）—— 进程级串行回归"
        )

    def test_bridge_same_thread_guard(self):
        import asyncio

        from app.services.geocompute._async_bridge import run_coro_sync

        async def _inner():
            async def _coro():
                pass

            with pytest.raises(RuntimeError):
                run_coro_sync(_coro())

        asyncio.run(_inner())

    def test_bridge_timeout_typed(self):
        import asyncio

        from app.services.geocompute._async_bridge import (
            BridgeTimeoutError,
            run_coro_sync,
        )

        async def _hang():
            await asyncio.sleep(5)

        start = time.monotonic()
        with pytest.raises(BridgeTimeoutError):
            run_coro_sync(_hang(), timeout_s=0.2)
        assert time.monotonic() - start < 3
