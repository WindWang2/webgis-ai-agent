"""GeoCompute V8 fabric — wave 1：能力清单 V8（卡级 GPU/磁盘）+ 账本
mem/gpu 维度 + 按维拒绝定位。

验收锚点：
- 能力探针诚实降级：无 nvidia-smi / 磁盘探测失败 → 空表/0，绝不虚构；
- enforcing 账本五维（rows/bytes/units/mem_mb/gpu）条件 UPDATE 记账与
  精确归还（守恒不变量延续 V6）；
- claim_lease 把 plan 估计（memory_mb 之和）与 resource_request.gpu 写进
  run 行 reserved_*（归还依据）；按维拒绝 → (None, dim)；
- ResourceRequest.fallback_cpu 往返（digest/解析）兼容 V7 投影。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_model import GeoComputeResourceUsage
from app.services.geocompute.cluster.contracts import (
    RESOURCE_DIMENSIONS,
    ResourceClaim,
    ResourceRequest,
)
from app.services.geocompute.cluster.capabilities import (
    GpuCard,
    WorkerCapabilityProfile,
    capability_from_row,
    capability_json,
    probe_capability,
)
from app.services.geocompute.cluster.store import ClusterLedger, ClusterRunStore


def _plan_snapshot(nodes: list[dict] | None = None) -> dict:
    return {
        "plan_id": "p1",
        "nodes": nodes if nodes is not None else [
            {
                "node_id": "n0",
                "category": "filter",
                "operation": "op",
                "inputs": [],
                "parameters": {},
                "estimate": {"rows": 100, "confidence": "high"},
            }
        ],
        "budget": {},
    }


@pytest.fixture()
def env(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v8-fabric.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    store = ClusterRunStore(factory)
    yield store, factory
    eng.dispose()


class TestCapabilityV8:
    def test_probe_honest_without_gpu(self):
        """本机无 nvidia-smi（CI 常态）→ 空卡表 + gpu_count=0，不虚构。"""
        profile = probe_capability()
        if profile.gpu_count == 0:
            assert profile.gpus == []
            assert "gpu" not in profile.capabilities
        else:
            # 真有卡的机器：卡级清单与汇总一致
            assert len(profile.gpus) == profile.gpu_count
            assert profile.gpu_mem_mb == sum(c.mem_mb for c in profile.gpus)
            assert "gpu" in profile.capabilities

    def test_probe_disk_free_non_negative(self):
        profile = probe_capability()
        assert profile.disk_free_mb >= 0

    def test_profile_json_roundtrip_with_cards(self):
        profile = WorkerCapabilityProfile(
            cpu_cores=8, mem_mb=16384,
            gpu_count=2, gpu_mem_mb=49152,
            gpus=[GpuCard(name="A100", mem_mb=40960),
                  GpuCard(name="A100", mem_mb=8192)],
            disk_free_mb=102400,
        )
        raw = capability_json(profile)
        assert raw is not None
        back = capability_from_row(raw)
        assert back is not None
        assert back.gpu_count == 2
        assert [c.name for c in back.gpus] == ["A100", "A100"]
        assert back.gpus[1].mem_mb == 8192
        assert back.disk_free_mb == 102400

    def test_old_row_without_v8_fields_degrades(self):
        """V7 capability JSON（无 gpus/disk_free_mb）→ typed 缺省，不炸。"""
        back = capability_from_row({
            "cpu_cores": 4, "mem_mb": 8192,
            "gpu_count": 1, "gpu_mem_mb": 8192,
            "backends": {}, "capabilities": ["gpu"], "zone": "default",
            "version": "vabc",
        })
        assert back is not None
        assert back.gpus == []
        assert back.disk_free_mb == 0
        # V7 gating 语义不变
        assert back.satisfies(gpu=1)
        assert not back.satisfies(gpu=2)

    def test_resource_request_fallback_cpu_roundtrip(self):
        req = ResourceRequest(gpu=1, fallback_cpu=True)
        assert req.normalized().fallback_cpu is True
        assert ResourceRequest().fallback_cpu is False


class TestLedgerMemGpuDims:
    def test_reserve_release_all_five_dims(self, env):
        store, factory = env
        ledger = ClusterLedger(factory=factory)
        ledger.ensure_scopes(["global"], factory=factory)
        claim = ResourceClaim(scope_key="global", rows=10, bytes=100,
                              units=1, mem_mb=2048, gpu=1)
        with factory() as db:
            assert ledger.reserve_claims_checked(db, {"global": claim}) is None
            db.commit()
        snap = store.ledger_snapshot()
        row = next(s for s in snap if s["scope_key"] == "global")
        assert row["usage_mem_mb"] == 2048
        assert row["usage_gpu"] == 1
        with factory() as db:
            ledger.release_claims(db, {"global": claim})
            db.commit()
        snap = store.ledger_snapshot()
        row = next(s for s in snap if s["scope_key"] == "global")
        assert row["usage_mem_mb"] == 0
        assert row["usage_gpu"] == 0

    def test_enforcing_reject_reports_dimension(self, env):
        _, factory = env
        ledger = ClusterLedger(
            enforcing=True,
            limits={"global": {"rows": None, "bytes": None, "units": None,
                               "mem_mb": 4096, "gpu": 2}},
            factory=factory,
        )
        ledger.ensure_scopes(["global"], factory=factory)
        with factory() as db:
            # 内存维拒绝
            claim = ResourceClaim(scope_key="global", mem_mb=8192)
            assert ledger.reserve_claims_checked(db, {"global": claim}) == "mem_mb"
            db.rollback()
            # GPU 维拒绝
            claim = ResourceClaim(scope_key="global", gpu=3)
            assert ledger.reserve_claims_checked(db, {"global": claim}) == "gpu"
            db.rollback()
            # GPU 恰好等于限额 → 通过
            claim = ResourceClaim(scope_key="global", gpu=2)
            assert ledger.reserve_claims_checked(db, {"global": claim}) is None
            db.rollback()

    def test_advisory_oversubscription_still_records(self, env):
        _, factory = env
        ledger = ClusterLedger(
            enforcing=False,
            limits={"global": {"rows": None, "bytes": None, "units": None,
                               "mem_mb": 100, "gpu": None}},
            factory=factory,
        )
        ledger.ensure_scopes(["global"], factory=factory)
        with factory() as db:
            dim = ledger.reserve_claims_checked(
                db, {"global": ResourceClaim(scope_key="global", mem_mb=500)})
            db.commit()
        # advisory：超限仍记账（诚实暴露超卖）+ 报告维度不阻塞
        assert dim == "mem_mb"
        with factory() as db:
            usage = db.query(GeoComputeResourceUsage).filter_by(
                scope_key="global").one()
            assert usage.usage_mem_mb == 500

    def test_reject_dimension_in_vocabulary(self, env):
        _, factory = env
        ledger = ClusterLedger(
            enforcing=True,
            limits={"global": {"rows": 5, "bytes": None, "units": None,
                               "mem_mb": None, "gpu": None}},
            factory=factory,
        )
        ledger.ensure_scopes(["global"], factory=factory)
        with factory() as db:
            dim = ledger.reserve_claims_checked(
                db, {"global": ResourceClaim(scope_key="global", rows=10)})
        assert dim in RESOURCE_DIMENSIONS


class TestClaimLeaseMemGpu:
    def test_claims_carry_estimates_and_gpu(self, env):
        store, factory = env
        snapshot = _plan_snapshot([
            {"node_id": "n0", "category": "filter", "operation": "op",
             "inputs": [], "parameters": {},
             "estimate": {"rows": 100, "memory_mb": 1500, "confidence": "high"}},
            {"node_id": "n1", "category": "raster_window_operation",
             "operation": "raster_calculator", "inputs": ["n0"],
             "parameters": {},
             "estimate": {"rows": 10, "memory_mb": 800.5, "confidence": "assumption"}},
        ])
        rid = store.create_run(
            plan_snapshot=snapshot, plan_fingerprint="fpv8",
            owner_scope="u:abc", tenant_raw="org1",
            resource_request={"gpu": 2},
        )
        epoch, reject = store.claim_lease(
            rid, coordinator_id="c1", ttl_s=30.0,
            ledger=ClusterLedger(factory=factory), return_detail=True,
        )
        assert epoch is not None and reject is None
        from app.models.db_model import GeoComputeClusterRun

        with factory() as db:
            row = db.query(GeoComputeClusterRun).filter_by(run_id=rid).one()
            assert row.reserved_mem_mb == 2300
            assert row.reserved_gpu == 2

    def test_enforcing_gpu_reject_keeps_run_queued(self, env):
        store, factory = env
        rid = store.create_run(
            plan_snapshot=_plan_snapshot(), plan_fingerprint="fpv8b",
            owner_scope="u:abc",
            resource_request={"gpu": 1},
        )
        ledger = ClusterLedger(
            enforcing=True,
            limits={"global": {"rows": None, "bytes": None, "units": None,
                               "mem_mb": None, "gpu": 0}},
            factory=factory,
        )
        epoch, reject = store.claim_lease(
            rid, coordinator_id="c1", ttl_s=30.0,
            ledger=ledger, return_detail=True,
        )
        assert epoch is None
        assert reject == "gpu"
        # run 留队（未被超卖认领）
        assert store.get_run(rid)["status"] == "queued"

    def test_legacy_return_shape_unchanged(self, env):
        """return_detail 缺省 = V6/V7 返回形状逐字节兼容。"""
        store, factory = env
        rid = store.create_run(
            plan_snapshot=_plan_snapshot(), plan_fingerprint="fpv8c",
            owner_scope="u:abc",
        )
        epoch = store.claim_lease(
            rid, coordinator_id="c1", ttl_s=30.0,
            ledger=ClusterLedger(factory=factory),
        )
        assert isinstance(epoch, int)

    def test_finish_releases_mem_gpu_exactly(self, env):
        store, factory = env
        rid = store.create_run(
            plan_snapshot=_plan_snapshot([
                {"node_id": "n0", "category": "filter", "operation": "op",
                 "inputs": [], "parameters": {},
                 "estimate": {"rows": 1, "memory_mb": 512,
                              "confidence": "high"}},
            ]),
            plan_fingerprint="fpv8d", owner_scope="u:abc",
            resource_request={"gpu": 1},
        )
        ledger = ClusterLedger(factory=factory)
        epoch = store.claim_lease(
            rid, coordinator_id="c1", ttl_s=30.0, ledger=ledger)
        assert epoch is not None
        with factory() as db:
            usage = db.query(GeoComputeResourceUsage).filter_by(
                scope_key="global").one()
            assert usage.usage_mem_mb == 512
            assert usage.usage_gpu == 1
        from app.services.geocompute.cluster.contracts import ClusterRunStatus

        assert store.finish_run(
            rid, epoch=epoch, status=ClusterRunStatus.COMPLETED,
            ledger=ledger,
        )
        with factory() as db:
            usage = db.query(GeoComputeResourceUsage).filter_by(
                scope_key="global").one()
            assert usage.usage_mem_mb == 0
            assert usage.usage_gpu == 0


# ═══════════════ V8 wave 2：稀缺度排序 / reservation 派发 / GPU fallback ═══════════════


class TestFairPickWithinTenantKey:
    def test_default_key_unchanged(self):
        from app.services.geocompute.cluster.fairness import fair_pick

        cands = [
            {"id": 1, "tenant_key": "t", "priority": 5},
            {"id": 2, "tenant_key": "t", "priority": 5},
            {"id": 3, "tenant_key": "t", "priority": 10},
        ]
        picked = fair_pick(cands, slots=2)
        assert [p["id"] for p in picked] == [3, 1]

    def test_scarcity_key_prefers_gpu_run(self):
        """同租户 1 槽位竞争：GPU run（稀缺）先于普通 run 被选中。"""
        from app.services.geocompute.cluster.fairness import fair_pick
        from app.services.geocompute.cluster.placement import scarcity_rank_key
        from app.services.geocompute.cluster.contracts import ResourceRequest

        gpu_req = ResourceRequest(gpu=1)
        plain_req = ResourceRequest()

        def key(r):
            req = gpu_req if r["id"] == 2 else plain_req
            eligible = 1 if r["id"] == 2 else 3
            return scarcity_rank_key(req, eligible) + (
                -int(r.get("priority") or 0), int(r.get("id") or 0))

        cands = [
            {"id": 1, "tenant_key": "t", "priority": 5},
            {"id": 2, "tenant_key": "t", "priority": 5},  # GPU run
            {"id": 3, "tenant_key": "t", "priority": 5},
        ]
        picked = fair_pick(cands, slots=1, within_tenant_key=key)
        assert [p["id"] for p in picked] == [2]

    def test_scarcity_key_zero_for_plain_requests(self):
        """无 envelope 要求 → V6 相对顺序不变。"""
        from app.services.geocompute.cluster.placement import scarcity_rank_key
        from app.services.geocompute.cluster.contracts import ResourceRequest

        assert scarcity_rank_key(ResourceRequest(), 3) == (1, 1, 0)


class TestSchedulerReservationAndFallback:
    """coordinator tick 集成：enforcing 账本按维拒绝可观测留队 + GPU
    fallback 剥离。eager/临时 SQLite + 置 conf 非 eager 模拟 worker 视图
    （与 test_geocompute_v7_cluster 同惯例）。"""

    @pytest.fixture()
    def sched_env(self, tmp_path, monkeypatch):
        eng = create_engine(f"sqlite:///{tmp_path / 'v8-sched.db'}",
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
                store=ClusterRunStore(factory),
                coordinator_id=name, local_slots=kw.pop("local_slots", 1),
                heartbeat_interval_s=0.05, tick_interval_s=0.02,
                leadership_ttl_s=1.0, lease_ttl_s=5.0, **kw)

        yield factory, make
        eng.dispose()

    def _plan(self) -> dict:
        return {"plan_id": "v8", "nodes": [
            {"node_id": "n0", "category": "source_scan", "operation": "inline",
             "inputs": [], "parameters": {"features": []}},
        ], "budget": {}}

    def _submit(self, factory, store, **kw) -> str:
        return store.create_run(
            plan_snapshot=self._plan(), plan_fingerprint="fp-v8-sched",
            owner_scope="u:v8", **kw)

    def _events(self, rid):
        from app.services.geocompute.cluster.events import RunEventStore

        return RunEventStore().window(rid)

    def _metrics_counters(self, monkeypatch):
        """隔离进程内计数器（避免污染其它测试的 metrics 断言）。"""
        import app.services.geocompute.cluster.metrics as m

        monkeypatch.setattr(m, "_resource_rejections", {})
        monkeypatch.setattr(m, "_oom_avoided", 0)
        monkeypatch.setattr(m, "_gpu_fallbacks", 0)
        return m

    def test_enforcing_mem_reject_emits_resource_event(self, sched_env, monkeypatch):
        factory, make = sched_env
        metrics = self._metrics_counters(monkeypatch)
        from app.services.geocompute.cluster.store import ClusterRunStore
        from app.services.task_queue import celery_app

        store = ClusterRunStore(factory)
        # plan 估计峰值内存 8192MiB > 全局账本限额 1024MiB → 预防性拒绝
        plan = {"plan_id": "v8", "nodes": [
            {"node_id": "n0", "category": "source_scan", "operation": "inline",
             "inputs": [], "parameters": {"features": []},
             "estimate": {"rows": 10, "memory_mb": 8192,
                          "confidence": "high"}},
        ], "budget": {}}
        rid = store.create_run(
            plan_snapshot=plan, plan_fingerprint="fp-v8-mem",
            owner_scope="u:v8")
        ledger = ClusterLedger(
            enforcing=True,
            limits={"global": {"rows": None, "bytes": None, "units": None,
                               "mem_mb": 1024, "gpu": None}},
            factory=factory,
        )
        coord = make("coord-v8", ledger=ledger)
        old = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = False
        try:
            store.upsert_worker("w-big", profiles={"celery": 1},
                                capability={"cpu_cores": 8, "mem_mb": 32768,
                                            "gpu_count": 0, "gpu_mem_mb": 0,
                                            "backends": {}, "capabilities": [],
                                            "zone": "default", "version": "v"})
            for _ in range(2):
                coord.tick()
            # run 留队（内存超限被预防性拒绝 —— 不启动再 OOM）
            assert store.get_run(rid)["status"] == "queued"
            evs = self._events(rid)
            waiting = [e for e in evs if e["event"] == "waiting_resource"]
            assert len(waiting) == 1
            assert waiting[0]["status"] == "resource:mem_mb"
            # 观测：按维拒绝计数 + OOM 避免量化
            snap = metrics.ClusterMetrics(store).snapshot()
            assert snap["resource_rejections"].get("mem_mb", 0) >= 1
            assert snap["oom_avoided"] >= 1
        finally:
            celery_app.conf.task_always_eager = old

    def test_gpu_fallback_strips_gpu_request(self, sched_env, monkeypatch):
        factory, make = sched_env
        self._metrics_counters(monkeypatch)
        from app.services.geocompute.cluster.store import ClusterRunStore
        from app.services.task_queue import celery_app

        store = ClusterRunStore(factory)
        rid = self._submit(factory, store, resource_request={
            "gpu": 1, "fallback_cpu": True, "min_mem_mb": 0, "min_cpu": 0,
            "required_profiles": []})
        coord = make("coord-fb", gpu_fallback_wait_s=0.0)
        old = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = False
        try:
            store.upsert_worker("w-cpu", profiles={"celery": 1},
                                capability={"cpu_cores": 4, "mem_mb": 8192,
                                            "gpu_count": 0, "gpu_mem_mb": 0,
                                            "backends": {}, "capabilities": [],
                                            "zone": "default", "version": "v"})
            stats = coord.tick()
            # 立即回退（wait=0）：gpu 要求剥离 → gpu_fallback 事件；
            # 本 tick 该 run 仍以旧 envelope 评估留队，下一 tick 派发。
            assert stats.get("dispatched", 0) == 0
            evs = self._events(rid)
            assert any(e["event"] == "gpu_fallback" for e in evs)
            row = store.get_run(rid)
            assert row["resource_request"]["gpu"] == 0
            assert row["resource_request"]["fallback_from_gpu"] is True
            stats = coord.tick()
            assert stats["dispatched"] == 1
            assert store.get_run(rid)["status"] in ("leased", "running")
        finally:
            celery_app.conf.task_always_eager = old

    def test_gpu_hold_unchanged_without_fallback_flag(self, sched_env, monkeypatch):
        """V7 语义保留：未声明 fallback_cpu 的 GPU run 无限期留队。"""
        factory, make = sched_env
        self._metrics_counters(monkeypatch)
        from app.services.geocompute.cluster.store import ClusterRunStore
        from app.services.task_queue import celery_app

        store = ClusterRunStore(factory)
        rid = self._submit(factory, store, resource_request={
            "gpu": 1, "min_mem_mb": 0, "min_cpu": 0,
            "required_profiles": []})
        coord = make("coord-hold", gpu_fallback_wait_s=0.0)
        old = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = False
        try:
            store.upsert_worker("w-cpu", profiles={"celery": 1},
                                capability={"cpu_cores": 4, "mem_mb": 8192,
                                            "gpu_count": 0, "gpu_mem_mb": 0,
                                            "backends": {}, "capabilities": [],
                                            "zone": "default", "version": "v"})
            for _ in range(2):
                coord.tick()
            row = store.get_run(rid)
            assert row["status"] == "queued"
            assert row["resource_request"]["gpu"] == 1
        finally:
            celery_app.conf.task_always_eager = old


class TestDowngradeGpuRequest:
    def test_cas_and_idempotence(self, env):
        store, _ = env
        rid = store.create_run(
            plan_snapshot=_plan_snapshot(), plan_fingerprint="fp-dg",
            owner_scope="u:abc", resource_request={"gpu": 2, "fallback_cpu": True},
        )
        assert store.downgrade_gpu_request(rid) is True
        row = store.get_run(rid)
        assert row["resource_request"]["gpu"] == 0
        assert row["resource_request"]["fallback_from_gpu"] is True
        # 幂等：已无 gpu 要求 → False
        assert store.downgrade_gpu_request(rid) is False
        # 终态 run 不可改写
        from app.services.geocompute.cluster.contracts import ClusterRunStatus

        epoch = store.claim_lease(rid, coordinator_id="c", ttl_s=30.0)
        store.finish_run(rid, epoch=epoch, status=ClusterRunStatus.COMPLETED)
        assert store.downgrade_gpu_request(rid) is False
