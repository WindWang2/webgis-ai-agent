"""V6 wave 2 — cluster CAS 存储层：lease 认领/fencing epoch/reclaim/取消旗标/
leadership/资源账本守恒。

并发模型说明（诚实边界）：CAS 用单语句条件 UPDATE（SQLite/PG 同语义）；
测试用**两个独立 store 实例共享同一临时库**模拟双进程竞争 —— 验证的是
条件更新的互斥语义，不是真实多进程调度（那是 wave 3/11 的职责）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.models.db_model import GeoComputeResourceUsage
from app.services.geocompute.cluster.contracts import ClusterRunStatus
from app.services.geocompute.cluster.store import (
    ClusterBackpressureError,
    ClusterLedger,
    ClusterRunStore,
    PlanSnapshotTooLargeError,
    _utcnow,
)
from datetime import timedelta

S = ClusterRunStatus


def _plan_snapshot(nodes: int = 1, rows: int = 100) -> dict:
    return {
        "plan_id": "p1",
        "nodes": [
            {
                "node_id": f"n{i}",
                "category": "filter",
                "operation": "op",
                "inputs": [],
                "parameters": {},
                "estimate": {"rows": rows, "confidence": "high"},
            }
            for i in range(nodes)
        ],
        "budget": {},
    }


@pytest.fixture()
def env(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-store.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    store_a = ClusterRunStore(factory)
    store_b = ClusterRunStore(factory)
    yield store_a, store_b, factory
    eng.dispose()


class TestCreateAndList:
    def test_create_get_list(self, env):
        a, _, _ = env
        rid = a.create_run(
            plan_snapshot=_plan_snapshot(), plan_fingerprint="fp1",
            owner_scope="u:abc", tenant_raw="org1",
        )
        assert rid.startswith("gexec-")
        row = a.get_run(rid)
        assert row["status"] == "queued"
        assert row["owner_scope"] == "u:abc"
        assert row["priority"] == 5
        listed = a.list_runs("u:abc")
        assert [r["run_id"] for r in listed] == [rid]

    def test_owner_isolation(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:me")
        assert a.get_run_owned(rid, "u:me") is not None
        assert a.get_run_owned(rid, "u:other") is None

    def test_snapshot_too_large_rejected(self, env):
        a, _, _ = env
        big = _plan_snapshot(nodes=2)
        big["nodes"] = big["nodes"] * 5000  # 远超 256KB
        with pytest.raises(PlanSnapshotTooLargeError):
            a.create_run(plan_snapshot=big, plan_fingerprint="fp",
                         owner_scope="u:abc")

    def test_tenant_backpressure(self, env):
        a, _, _ = env
        for _ in range(3):
            a.create_run(plan_snapshot=_plan_snapshot(), plan_fingerprint="fp",
                         owner_scope="u:abc", tenant_raw="org1",
                         max_queued_per_tenant=3)
        with pytest.raises(ClusterBackpressureError):
            a.create_run(plan_snapshot=_plan_snapshot(), plan_fingerprint="fp",
                         owner_scope="u:abc", tenant_raw="org1",
                         max_queued_per_tenant=3)


class TestLeaseAndFencing:
    def test_claim_is_exclusive_across_stores(self, env):
        """双 coordinator 竞争同一 run：恰好一方成功，epoch 单调。"""
        a, b, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch_a = a.claim_lease(rid, coordinator_id="coord-a", ttl_s=30)
        assert epoch_a == 1
        assert b.claim_lease(rid, coordinator_id="coord-b", ttl_s=30) is None
        row = a.get_run(rid)
        assert row["status"] == "leased"
        # coordinator_id 是控制面键（用户投影剔除）→ internal 投影
        assert a.get_run_internal(rid)["coordinator_id"] == "coord-a"
        assert row["lease_epoch"] == 1

    def test_finish_requires_current_epoch(self, env):
        a, b, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="coord-a", ttl_s=30)
        a.mark_running(rid, epoch=epoch)
        # 旧 epoch（僵尸 coordinator）无法写终态
        assert not a.finish_run(rid, epoch=epoch - 1, status=S.COMPLETED)
        assert a.get_run(rid)["status"] == "running"
        assert a.finish_run(rid, epoch=epoch, status=S.COMPLETED)
        assert a.get_run(rid)["status"] == "completed"
        # terminal 无出边：再 finish/claim 全部拒绝
        assert not a.finish_run(rid, epoch=epoch, status=S.FAILED)
        assert b.claim_lease(rid, coordinator_id="coord-b", ttl_s=30) is None

    def test_heartbeat_fencing(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="coord-a", ttl_s=30)
        assert a.heartbeat_run(rid, epoch=epoch, ttl_s=30)
        assert not a.heartbeat_run(rid, epoch=epoch + 5, ttl_s=30)

    def test_mark_running_epoch_gated(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="coord-a", ttl_s=30)
        assert not a.mark_running(rid, epoch=epoch + 1)
        assert a.mark_running(rid, epoch=epoch)
        assert a.get_run(rid)["status"] == "running"


class TestReclaim:
    def _expired(self, store, rid):
        from app.models.db_model import GeoComputeClusterRun as Run

        with store._factory() as db:
            db.execute(
                update(Run)
                .where(Run.run_id == rid)
                .values(lease_expires_at=_utcnow() - timedelta(seconds=1))
            )
            db.commit()

    def test_reclaim_requeues_then_fails_after_budget(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        # 3 次执行机会：初始 + 2 次 reclaim 重试；第 3 次丢失即 failed
        for expected in ("requeued", "requeued", "failed"):
            epoch = a.claim_lease(rid, coordinator_id="c", ttl_s=30)
            assert epoch is not None
            self._expired(a, rid)
            outcomes = a.reclaim_expired()
            assert len(outcomes) == 1
            assert outcomes[0]["run_id"] == rid
            assert outcomes[0]["outcome"] == expected
        assert a.get_run(rid)["status"] == "failed"
        assert a.get_run(rid)["error_code"] == "WORKER_LOSS"

    def test_reclaim_skips_fresh_leases(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        a.claim_lease(rid, coordinator_id="c", ttl_s=300)
        assert a.reclaim_expired() == []

    def test_reclaim_releases_ledger_exactly_once(self, env):
        """reclaim 归还账本且 exactly-once：并发第二次 reclaim 不再归还。"""
        a, b, factory = env
        ledger = ClusterLedger(enforcing=True, limits={
            "global": {"rows": 1000, "bytes": None, "units": 2},
        })
        rid = a.create_run(plan_snapshot=_plan_snapshot(rows=400),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="c", ttl_s=30, ledger=ledger)
        assert epoch is not None
        snap = ledger_usage(factory)
        assert snap["global"]["usage_rows"] == 400
        assert snap["global"]["usage_units"] == 1
        self._expired(a, rid)
        first = a.reclaim_expired(ledger=ledger)
        second = b.reclaim_expired(ledger=ledger)
        assert [o["outcome"] for o in first] == ["requeued"]
        assert second == []
        snap = ledger_usage(factory)
        assert snap["global"]["usage_rows"] == 0
        assert snap["global"]["usage_units"] == 0
        # reclaim 归还后 reserved_* 清零（崩溃清理的幂等证明）
        with factory() as db:
            r = db.execute(
                select_from_run(rid)
            ).scalar_one()
            assert r.reserved_rows == 0 and r.reserved_units == 0


def select_from_run(rid):
    from app.models.db_model import GeoComputeClusterRun as Run

    return select(Run).where(Run.run_id == rid)


def ledger_usage(factory) -> dict:
    with factory() as db:
        rows = db.execute(select(GeoComputeResourceUsage)).scalars().all()
        return {
            u.scope_key: {
                "usage_rows": u.usage_rows, "usage_bytes": u.usage_bytes,
                "usage_units": u.usage_units,
            }
            for u in rows
        }


class TestCancelAndYield:
    def test_cancel_flag_idempotent_and_terminal_noop(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        changed, status = a.request_cancel(rid)
        assert changed and status == "queued"
        changed2, _ = a.request_cancel(rid)
        assert changed2 is False  # 幂等：旗标已存在
        # m3（round1）：已请求取消的 run 不再被认领 —— cancel sweep 负责把
        # 排队 run 直接收敛为终态（执行侧心跳只处理在跑 run 的取消）。
        assert a.claim_lease(rid, coordinator_id="c", ttl_s=30) is None
        assert a.cancel_flagged() == [rid]
        changed3, status3 = a.request_cancel(rid)
        assert changed3 is False and status3 == "cancelled"  # 终态 no-op

    def test_yield_flag_idempotent(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        assert a.request_yield(rid)
        assert not a.request_yield(rid)
        assert a.consume_yield_if_requested(rid)
        assert not a.consume_yield_if_requested("gexec-nonexistent")

    def test_cancel_unknown_run(self, env):
        a, _, _ = env
        changed, status = a.request_cancel("gexec-nonexistent")
        assert changed is False and status is None


class TestLeadership:
    def test_single_leader_then_failover(self, env):
        a, b, _ = env
        epoch_a = a.acquire_leadership("coord-a", ttl_s=30)
        assert epoch_a == 1
        # 在任者未过期 → standby
        assert b.acquire_leadership("coord-b", ttl_s=30) is None
        # 续约（带 epoch fencing）
        assert a.renew_leadership("coord-a", epoch=epoch_a, ttl_s=30)
        assert not a.renew_leadership("coord-a", epoch=99, ttl_s=30)
        # coord-a 失联 → lease 过期 → coord-b 当选，epoch 单调递增
        from app.models.db_model import GeoComputeClusterWorker as W

        with a._factory() as db:
            db.execute(
                update(W).where(W.worker_id == "coord-a").values(
                    lease_expires_at=_utcnow() - timedelta(seconds=1)
                )
            )
            db.commit()
        epoch_b = b.acquire_leadership("coord-b", ttl_s=30)
        assert epoch_b is not None  # 当选（per-row epoch；fencing 只用于本行续约）
        # 旧 leader 复活续约 → 有人在任 → False（split-brain 防护：必须卸任）
        assert not a.renew_leadership("coord-a", epoch=epoch_a, ttl_s=30)
        # 新 leader 续约正常
        assert b.renew_leadership("coord-b", epoch=epoch_b, ttl_s=30)


class TestLedger:
    def test_enforcing_denies_over_limit(self, env):
        a, _, factory = env
        ledger = ClusterLedger(enforcing=True, limits={
            "global": {"rows": 500, "bytes": None, "units": 8},
        })
        rid1 = a.create_run(plan_snapshot=_plan_snapshot(rows=400),
                            plan_fingerprint="fp", owner_scope="u:abc")
        rid2 = a.create_run(plan_snapshot=_plan_snapshot(rows=400),
                            plan_fingerprint="fp", owner_scope="u:abc")
        assert a.claim_lease(rid1, coordinator_id="c", ttl_s=30, ledger=ledger)
        # 400+400 > 500 → 拒绝认领（OOM 防护：绝不超容量拉起）
        assert a.claim_lease(rid2, coordinator_id="c", ttl_s=30, ledger=ledger) is None
        assert a.get_run(rid2)["status"] == "queued"

    def test_advisory_allows_but_records(self, env):
        a, _, factory = env
        ledger = ClusterLedger(enforcing=False, limits={
            "global": {"rows": 500, "bytes": None, "units": 8},
        })
        rid1 = a.create_run(plan_snapshot=_plan_snapshot(rows=400),
                            plan_fingerprint="fp", owner_scope="u:abc")
        rid2 = a.create_run(plan_snapshot=_plan_snapshot(rows=400),
                            plan_fingerprint="fp", owner_scope="u:abc")
        assert a.claim_lease(rid1, coordinator_id="c", ttl_s=30, ledger=ledger)
        assert a.claim_lease(rid2, coordinator_id="c", ttl_s=30, ledger=ledger)
        snap = ledger_usage(factory)
        assert snap["global"]["usage_rows"] == 800  # 诚实暴露超卖

    def test_release_clamps_to_zero(self, env):
        a, _, factory = env
        ledger = ClusterLedger()
        rid = a.create_run(plan_snapshot=_plan_snapshot(rows=100),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="c", ttl_s=30, ledger=ledger)
        a.finish_run(rid, epoch=epoch, status=S.COMPLETED, ledger=ledger)
        snap = ledger_usage(factory)
        assert snap["global"]["usage_rows"] == 0
        assert snap["global"]["usage_units"] == 0
        # 双重归还（异常路径）也被钳零 —— 无负数、无泄漏
        from app.services.geocompute.cluster.store import _reserved_claims_from_row

        with factory() as db:
            row = db.execute(select_from_run(rid)).scalar_one()
            ledger.release_claims(db, _reserved_claims_from_row(row))
            db.commit()
        assert ledger_usage(factory)["global"]["usage_units"] == 0

    def test_finish_releases_ledger(self, env):
        a, _, factory = env
        ledger = ClusterLedger(enforcing=True, limits={
            "global": {"rows": 1000, "bytes": None, "units": 1},
        })
        rid = a.create_run(plan_snapshot=_plan_snapshot(rows=100),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="c", ttl_s=30, ledger=ledger)
        a.mark_running(rid, epoch=epoch)
        assert a.finish_run(rid, epoch=epoch, status=S.COMPLETED, ledger=ledger)
        assert ledger_usage(factory)["global"]["usage_units"] == 0
        # 释放后下一个 run 可认领（units=1 全局闸）
        rid2 = a.create_run(plan_snapshot=_plan_snapshot(),
                            plan_fingerprint="fp", owner_scope="u:abc")
        assert a.claim_lease(rid2, coordinator_id="c", ttl_s=30, ledger=ledger)


class TestWorkers:
    def test_upsert_heartbeat_prune_live(self, env):
        a, _, _ = env
        a.upsert_worker("w-raster", profiles={"raster": 2, "heavy_cpu": 1})
        a.upsert_worker("w-raster", profiles={"raster": 3})  # 更新能力
        live = a.live_workers()
        assert len(live) == 1
        assert live[0]["profiles"] == {"raster": 3}
        assert a.worker_heartbeat("w-raster")
        assert not a.worker_heartbeat("w-missing")
        # 失联清理
        with a._factory() as db:
            from app.models.db_model import GeoComputeClusterWorker as W

            db.execute(
                update(W).where(W.worker_id == "w-raster").values(
                    heartbeat_at=_utcnow() - timedelta(seconds=999)
                )
            )
            db.commit()
        assert a.prune_workers() == 1
        assert a.live_workers() == []

    def test_preempted_requeue_and_counts(self, env):
        a, _, _ = env
        rid = a.create_run(plan_snapshot=_plan_snapshot(),
                           plan_fingerprint="fp", owner_scope="u:abc")
        epoch = a.claim_lease(rid, coordinator_id="c", ttl_s=30)
        a.mark_running(rid, epoch=epoch)
        assert a.finish_run(rid, epoch=epoch, status=S.PREEMPTED)
        row = a.get_run(rid)
        assert row["status"] == "preempted"
        assert row["preempts"] == 1
        # PREEMPTED 可重派（新 epoch），attempts 不因抢占增长
        epoch2 = a.claim_lease(rid, coordinator_id="c", ttl_s=30)
        assert epoch2 == epoch + 1
        assert a.get_run(rid)["attempts"] == 0
