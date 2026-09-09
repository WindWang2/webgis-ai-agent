"""V6 round2 review 修复回归 —— C1/C2（抢占 livelock 与账本泄漏）+ M1 retention。

背景：round2 review 发现（a）yield 旗标在 requeue 后残留 → 心跳再点燃 →
无限抢占自噬；（b）PREEMPTED 转移跳过账本归还 + requeue 覆写 reserved_* →
enforcing 模式下抢占循环泄漏配额直至全集群死锁；（c）preempts 保险丝
（contracts 承诺的 PREEMPT_EXHAUSTED）未实现。本文件锁定这三个语义。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.contracts import ClusterRunStatus
from app.services.geocompute.cluster.scheduler import ClusterCoordinator
from app.services.geocompute.cluster.store import (
    ClusterLedger,
    ClusterRunStore,
    _utcnow,
)


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-r2.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)

    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        return {"features": params.get("features") or [], "metadata": {}}

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)

    store = ClusterRunStore(factory)
    yield store, factory
    eng.dispose()


def _plan_dict(unique: str) -> dict:
    return {
        "plan_id": f"r2-{unique}",
        "nodes": [
            {"node_id": "scan", "category": "source_scan", "operation": "inline",
             "inputs": [],
             "parameters": {"features": [
                 {"type": "Feature", "geometry": {"type": "Point",
                                                  "coordinates": [1.0, 2.0]},
                  "properties": {"u": unique}},
             ]}},
        ],
        "budget": {},
    }


def _submit(store, unique: str, nodes_extra: str = "", **kw) -> str:
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    plan = _plan_dict(unique)
    if nodes_extra:
        plan["nodes"].append({
            "node_id": nodes_extra, "category": "filter", "operation": "eq",
            "inputs": ["scan"],
            "parameters": {"predicate": {"op": "eq", "field": "u",
                                         "value": unique}},
        })
    po = ExecutionPlan.model_validate(plan)
    graph.validate_plan(po)
    return store.create_run(plan_snapshot=po.model_dump(),
                            plan_fingerprint=po.graph_fingerprint(),
                            owner_scope="u:r2", **kw)


class TestC1YieldFlagLifecycle:
    def test_requeue_clears_yield_flag(self, env):
        """C1：PREEMPTED → QUEUED 必须清除 yield 旗标（下一 attempt 不自噬）。"""
        store, _ = env
        rid = _submit(store, "c1")
        epoch = store.claim_lease(rid, coordinator_id="c", ttl_s=30)
        store.mark_running(rid, epoch=epoch)
        store.request_yield(rid)
        assert store.finish_run(rid, epoch=epoch, status=ClusterRunStatus.PREEMPTED)
        store.requeue_preempted(rid, epoch=epoch)
        row = store.get_run(rid)
        assert row["status"] == "queued"
        assert row["yield_requested_at"] is None, (
            "yield 旗标残留会让下一 attempt 心跳立即再让出（livelock）"
        )

    def test_preempt_fuse_terminates_run(self, env):
        """C1 保险丝：preempts 达到 max → 终态 failed[PREEMPT_EXHAUSTED]。"""
        store, _ = env
        from app.services.geocompute.cluster.contracts import MAX_PREEMPTS

        rid = _submit(store, "fuse")
        # 手工推进 preempts 到上界-1，再让最后一次 PREEMPTED 触发保险丝
        from sqlalchemy import update

        from app.models.db_model import GeoComputeClusterRun as Run

        with store._factory() as db:
            db.execute(update(Run).where(Run.run_id == rid)
                       .values(preempts=MAX_PREEMPTS - 1))
            db.commit()
        epoch = store.claim_lease(rid, coordinator_id="c", ttl_s=30)
        store.mark_running(rid, epoch=epoch)
        assert store.finish_run(rid, epoch=epoch, status=ClusterRunStatus.PREEMPTED)
        row = store.get_run(rid)
        assert row["status"] == "failed"
        assert row["error_code"] == "PREEMPT_EXHAUSTED"


class TestC2LedgerConservation:
    def test_preempt_cycle_conserves_ledger(self, env, monkeypatch):
        """C2：抢占循环（claim→PREEMPT→claim→…→terminal）后账本归零 ——
        enforcing 限额 1 单位下循环能完整走通即证明无泄漏。"""
        store, factory = env
        ledger = ClusterLedger(enforcing=True, limits={
            "global": {"rows": None, "bytes": None, "units": 1},
        })
        # 两节点 plan + 阻塞 scan：保证 yield 在节点边界被观察到
        import threading as _threading

        from app.services.geocompute import ops as ops_mod

        started, release = _threading.Event(), _threading.Event()
        real_execute = ops_mod.execute_node

        def slow_execute(ctx, node, upstream):
            if node.node_id == "scan":
                started.set()
                release.wait(timeout=10)
            return real_execute(ctx, node, upstream)

        monkeypatch.setattr(ops_mod, "execute_node", slow_execute)
        try:
            rid = _submit(store, "c2", nodes_extra="filt")
            coord = ClusterCoordinator(
                store=store, coordinator_id="c2-coord", local_slots=1,
                ledger=ledger, heartbeat_interval_s=0.05,
            )
            coord.tick()  # 首次认领（units=1 占满全局闸）
            assert started.wait(timeout=5), "scan 节点应已开始"
            store.request_yield(rid)
            # 确定性：等心跳线程观察到旗标并点燃 yield_event，再放行 scan
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                exec_state = coord._inflight.get(rid)
                if exec_state is not None and exec_state.yield_event.is_set():
                    break
                time.sleep(0.01)
            assert coord._inflight[rid].yield_event.is_set()
            release.set()  # scan 完成 → engine 下一循环在安全点让出
        finally:
            release.set()
            monkeypatch.setattr(ops_mod, "execute_node", real_execute)
        deadline = time.monotonic() + 5
        row = None
        while time.monotonic() < deadline:
            row = store.get_run(rid)
            if row["status"] == "queued" and row["preempts"] >= 1:
                break
            time.sleep(0.02)
        assert row is not None and row["status"] == "queued" and row["preempts"] == 1
        snap = store.ledger_snapshot()
        assert snap and snap[0]["usage_units"] == 0, (
            "PREEMPTED 转移必须归还预留 —— 否则 enforcing 下抢占循环漏配额"
        )
        # 没有泄漏 ⇒ 下一轮认领仍能拿到 units=1 的全局闸
        epoch2 = store.claim_lease(rid, coordinator_id="c2-coord", ttl_s=30,
                                   ledger=ledger)
        assert epoch2 is not None
        snap = store.ledger_snapshot()
        assert snap[0]["usage_units"] == 1
        store.finish_run(rid, epoch=epoch2, status=ClusterRunStatus.COMPLETED,
                         ledger=ledger)
        assert store.ledger_snapshot()[0]["usage_units"] == 0

    def test_claim_refuses_cancel_flagged_run(self, env):
        """m3：已请求取消的排队 run 不再被认领（cancel sweep 负责收敛）。"""
        store, _ = env
        rid = _submit(store, "m3")
        store.request_cancel(rid)
        assert store.claim_lease(rid, coordinator_id="c", ttl_s=30) is None
        assert store.get_run(rid)["status"] == "queued"


class TestM1Retention:
    def test_purge_terminal_removes_only_old_terminal_rows(self, env):
        store, _ = env

        from sqlalchemy import update

        from app.models.db_model import GeoComputeClusterRun as Run

        rid_old = _submit(store, "old")
        rid_new = _submit(store, "new")
        from datetime import timedelta

        # 直接构造终态行（terminal_at 一旧一新）
        with store._factory() as db:
            db.execute(update(Run).where(Run.run_id == rid_old).values(
                status="completed",
                terminal_at=_utcnow() - timedelta(hours=48)))
            db.execute(update(Run).where(Run.run_id == rid_new).values(
                status="completed", terminal_at=_utcnow()))
            db.commit()
        deleted = store.purge_terminal(older_than_s=24 * 3600.0)
        assert deleted == 1
        assert store.get_run(rid_old) is None
        assert store.get_run(rid_new) is not None  # 新终态行保留
