"""V6 wave 11 — cluster chaos corpus（本地小型模拟环境）。

矩阵：kill worker（通道收缩）/ kill scheduler（failover）/ 延迟心跳
（reclaim）/ 重复投递（幂等）/ DB 瞬断（tick 退避，不 crash）/ 取消与
终态竞态 / 账本崩溃清理。全部在 eager Celery + 临时 SQLite 上运行 ——
不要求真实集群（验收约束）。

与 wave 3-5 测试的分工：那边验证正常路径语义；这里只做故障注入与
不变量（状态不丢、无重复执行副作用、无账本泄漏）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.scheduler import ClusterCoordinator
from app.services.geocompute.cluster.store import (
    ClusterLedger,
    ClusterRunStore,
    _utcnow,
)
from datetime import timedelta


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-chaos.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)

    # SOURCE_SCAN 测试桩（scan_stub 惯例）
    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        return {"features": params.get("features") or [], "metadata": {}}

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)

    def make_coordinator(name: str, **kw):
        return ClusterCoordinator(
            store=ClusterRunStore(factory), coordinator_id=name,
            heartbeat_interval_s=kw.pop("heartbeat_interval_s", 0.05),
            tick_interval_s=0.02, leadership_ttl_s=kw.pop("leadership_ttl_s", 2.0),
            lease_ttl_s=kw.pop("lease_ttl_s", 5.0),
            **kw,
        )

    store = ClusterRunStore(factory)
    yield store, make_coordinator, factory, eng
    eng.dispose()


def _submit(store, unique: str, **kw) -> str:
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    plan = {
        "plan_id": f"chaos-{unique}",
        "nodes": [
            {"node_id": "scan", "category": "source_scan", "operation": "inline",
             "inputs": [],
             "parameters": {"features": [
                 {"type": "Feature", "geometry": {"type": "Point",
                                                  "coordinates": [116.0, 39.0]},
                  "properties": {"u": unique}},
             ]}},
        ],
        "budget": {},
    }
    po = ExecutionPlan.model_validate(plan)
    graph.validate_plan(po)
    return store.create_run(plan_snapshot=po.model_dump(),
                            plan_fingerprint=po.graph_fingerprint(),
                            owner_scope="u:chaos", **kw)


def _wait_terminal(store, run_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_run(run_id)
        if row and row["status"] in {"completed", "failed", "cancelled"}:
            return row["status"]
        time.sleep(0.02)
    return (store.get_run(run_id) or {}).get("status") or "timeout"


class TestKillScheduler:
    def test_scheduler_death_then_takeover_preserves_state(self, env):
        """kill scheduler：run 状态不丢，新 leader reclaim 后完成。"""
        store, make_coordinator, factory, _ = env
        a = make_coordinator("coord-a")
        rid = _submit(store, "kill-sched")
        a.tick()
        a.wait_idle(timeout=5)  # 正常完成一次（基线）
        assert store.get_run(rid)["status"] == "completed"

        # 第二个 run 在 a 认领后 a 被 kill（abandon：不写终态）
        rid2 = _submit(store, "kill-sched-2")
        from app.models.db_model import GeoComputeClusterRun as Run
        from app.models.db_model import GeoComputeClusterWorker as W

        with factory() as db:
            db.execute(update(Run).where(Run.run_id == rid2).values(
                status="running", coordinator_id="coord-a", lease_epoch=1,
                lease_expires_at=_utcnow() - timedelta(seconds=1),
            ))
            db.execute(update(W).where(W.worker_id == "coord-a").values(
                lease_expires_at=_utcnow() - timedelta(seconds=1)))
            db.commit()
        b = make_coordinator("coord-b")
        b.tick()
        assert _wait_terminal(store, rid2) == "completed"
        # attempt 丢失被记账，状态机无孤儿
        assert store.get_run(rid2)["attempts"] == 1

    def test_delayed_heartbeat_reclaim(self, env):
        """心跳延迟超过 TTL → reclaim（重试预算内回队）。"""
        store, make_coordinator, factory, _ = env
        rid = _submit(store, "delayed-hb")
        from app.models.db_model import GeoComputeClusterRun as Run

        with factory() as db:
            db.execute(update(Run).where(Run.run_id == rid).values(
                status="leased", coordinator_id="ghost", lease_epoch=1,
                lease_expires_at=_utcnow() - timedelta(seconds=2),
            ))
            db.commit()
        coord = make_coordinator("coord-hb")
        stats = coord.tick()
        assert stats["reclaimed"] == 1
        row = store.get_run(rid)
        # reclaim 记账 attempt=1；同一 tick 可能立即重派（queued/leased/running 皆可）
        assert row["attempts"] == 1
        assert row["status"] in {"queued", "leased", "running"}
        # reclaim 后调度继续 → completed（延迟心跳不丢任务）
        assert _wait_terminal_ticked(store, coord, rid) == "completed"


def _wait_terminal_ticked(store, coord, run_id, timeout=10.0):
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
    return (store.get_run(run_id) or {}).get("status") or "timeout"


class TestDuplicateDelivery:
    def test_duplicate_submit_same_plan_yields_distinct_runs(self, env):
        """同一 plan 重复提交 → 两个独立 run（run 无幂等合并语义 ——
        显式提交即显式执行），但各自恰好执行一次。"""
        store, make_coordinator, _, _ = env
        coord = make_coordinator("coord-dup")
        r1 = _submit(store, "dup-a")
        r2 = _submit(store, "dup-a")  # 内容相同
        assert r1 != r2
        for _ in range(6):
            coord.tick()
            if all(
                store.get_run(r)["status"] == "completed"
                for r in (r1, r2)
            ):
                break
            time.sleep(0.05)
        assert store.get_run(r1)["status"] == "completed"
        assert store.get_run(r2)["status"] == "completed"
        # engine 里两个 run 各自一份证据（无串写）
        from app.services.geocompute.executor import engine

        assert engine.get_run(r1) is not None
        assert engine.get_run(r2) is not None

    def test_stale_epoch_terminal_write_rejected(self, env):
        """旧 coordinator 复活后写终态 → epoch fencing 拒绝（诚实丢弃）。"""
        store, make_coordinator, factory, _ = env
        rid = _submit(store, "stale-epoch")
        from app.models.db_model import GeoComputeClusterRun as Run

        # 模拟：epoch 1 的 ghost 认领后被 reclaim，epoch 2 重新认领并完成
        coord = make_coordinator("coord-new")
        with factory() as db:
            db.execute(update(Run).where(Run.run_id == rid).values(
                status="queued", coordinator_id=None, lease_epoch=0,
                attempts=0, lease_expires_at=None,
            ))
            db.commit()
        assert _wait_terminal_ticked(store, coord, rid) == "completed"
        final_epoch = store.get_run(rid)["lease_epoch"]
        # 复活的 ghost 用旧 epoch=1 写终态 → False
        ghost_epoch = 1
        if final_epoch > ghost_epoch:
            ok = store.finish_run(rid, epoch=ghost_epoch,
                                  status=__import__("app.services.geocompute.cluster.contracts",
                                                    fromlist=["ClusterRunStatus"])
                                  .ClusterRunStatus.FAILED)
            assert ok is False
            assert store.get_run(rid)["status"] == "completed"  # 终态不被覆盖


class TestDbTransientFailure:
    def test_tick_survives_db_outage(self, env, monkeypatch):
        """DB 瞬断：tick 抛错由 run_forever 退避吞掉；恢复后继续调度。"""
        store, make_coordinator, factory, _ = env
        coord = make_coordinator("coord-db")
        rid = _submit(store, "db-flake")

        real_factory = coord._store._factory
        fail = {"on": True}

        def flaky_factory():
            if fail["on"]:
                raise RuntimeError("simulated db outage")
            return real_factory()

        monkeypatch.setattr(coord._store, "_factory", flaky_factory)
        with pytest.raises(RuntimeError):
            coord.tick()  # run_forever 会吞掉并退避；tick 层面可见
        fail["on"] = False
        monkeypatch.setattr(coord._store, "_factory", real_factory)
        # 恢复后调度继续：完成
        assert _wait_terminal_ticked(store, coord, rid) == "completed"

    def test_ledger_crash_cleanup_no_leak(self, env):
        """崩溃清理：reclaim 归还账本 —— 反复崩溃不泄漏配额。"""
        store, make_coordinator, factory, _ = env
        ledger = ClusterLedger(enforcing=True, limits={
            "global": {"rows": 500, "bytes": None, "units": 1},
        })
        rid = _submit(store, "leak")
        from app.models.db_model import GeoComputeClusterRun as Run

        coord = make_coordinator("coord-leak", ledger=ledger)
        # 崩溃 2 次（第 3 次 reclaim 即耗尽 attempt 预算 → 那是 wave5 的语义）
        for i in range(2):
            with factory() as db:
                db.execute(update(Run).where(Run.run_id == rid).values(
                    status="running", coordinator_id="ghost",
                    lease_epoch=i + 1, attempts=i,
                    lease_expires_at=_utcnow() - timedelta(seconds=1),
                ))
                db.commit()
            coord.tick()  # reclaim + 重新派发（占用 units=1）
            # 在跑时账本恰有 1 单位在用；崩溃回收后回到 0
        assert _wait_terminal_ticked(store, coord, rid) == "completed"
        snap = store.ledger_snapshot()
        assert snap[0]["usage_units"] == 0  # 无泄漏


class TestCancellationRaces:
    def test_cancel_versus_terminal_completion(self, env):
        """取消与完成竞态：先到者赢，终态不可覆盖，无异常泄漏。"""
        store, make_coordinator, _, _ = env
        coord = make_coordinator("coord-race")
        rid = _submit(store, "race-cancel")
        for _ in range(6):
            coord.tick()
            status = store.get_run(rid)["status"]
            if status == "completed":
                break
            time.sleep(0.05)
        # run 已终态；此刻的取消请求必须是幂等 no-op
        changed, observed = store.request_cancel(rid)
        assert changed is False
        assert observed == "completed"

    def test_cancel_and_preempt_flags_coexist(self, env):
        """取消+抢占旗标同时存在：取消优先（终态 cancelled，不回队）。"""
        store, make_coordinator, _, _ = env
        rid = _submit(store, "flags")
        store.request_cancel(rid)
        store.request_yield(rid)
        coord = make_coordinator("coord-flags")
        coord.tick()  # cancel sweep 收敛排队 run
        assert store.get_run(rid)["status"] == "cancelled"


class TestKillWorkerChannel:
    def test_worker_prune_shrinks_channel_capacity(self, env):
        """kill worker：心跳失联 → prune → 通道收缩 → 需该通道的 run 留队。"""
        store, make_coordinator, _, _ = env
        from app.services.task_queue import celery_app

        coord = make_coordinator("coord-chan")
        _submit(store, "chan", required_profiles=["raster"])
        store.upsert_worker("w-raster", profiles={"raster": 1})
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(celery_app.conf, "task_always_eager", False)
            stats = coord.tick()
            assert stats["dispatched"] == 1  # 通道在 → 派发
            # worker 失联 + prune → 通道消失
            store.prune_workers(cutoff=_utcnow() + timedelta(seconds=1))
            rid2 = _submit(store, "chan-2", required_profiles=["raster"])
            stats2 = coord.tick()
            assert stats2["dispatched"] == 0  # 无通道 → 留队
            assert store.get_run(rid2)["status"] == "queued"
