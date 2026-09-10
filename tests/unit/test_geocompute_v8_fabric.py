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
