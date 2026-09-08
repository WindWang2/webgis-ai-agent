"""V6 wave 3-5/8/10 — cluster coordinator：持久调度 / 恢复 / 分布式取消 /
抢占 / 公平 / 通道匹配。

worker simulation（与 V5 geocompute 测试同一诚实声明）：Celery eager +
临时 SQLite；「双 coordinator」用两个实例共享同一临时库模拟 —— 验证
CAS/leadership 语义，不覆盖真实多进程 broker 投递。
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from app.services.geocompute import ops
from app.services.geocompute.cluster.scheduler import ClusterCoordinator
from app.services.geocompute.cluster.store import (
    ClusterLedger,
    ClusterRunStore,
    _utcnow,
)
from datetime import timedelta

S = "ClusterRunStatusPlaceholder"


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture(autouse=True)
def scan_stub(monkeypatch):
    """SOURCE_SCAN 测试桩（与 test_geocompute_executor_v4 同惯例）：
    parameters.features 内联返回。"""
    from app.services.geocompute.errors import NodeExecutionError
    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        if params.get("_boom"):
            raise NodeExecutionError(
                str(params["_boom"]), retry_safe=False, node_id=node.node_id,
            )
        return {
            "features": params.get("features") or [],
            "metadata": {"via": "scan_stub"},
        }

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)


def _features(n: int = 2, unique: str = "a") -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0]},
            "properties": {"kind": "a", "v": i, "unique": unique},
        }
        for i in range(n)
    ]


def _plan_dict(nodes: list[dict], plan_id: str = "v6-cluster") -> dict:
    return {"plan_id": plan_id, "nodes": nodes, "budget": {}}


def _simple_plan(unique: str = "a") -> dict:
    # unique 进 features：语义指纹随测试变化 —— 隔离进程内复用缓存的
    # 跨测试命中（复用是既有正确行为，测试需要真实执行路径）。
    scan = {
        "node_id": "scan", "category": "source_scan", "operation": "inline",
        "inputs": [], "parameters": {"features": _features(2, unique=unique)},
    }
    filt = {
        "node_id": "filt", "category": "filter", "operation": "eq",
        "inputs": ["scan"],
        "parameters": {"predicate": {"op": "eq", "field": "kind", "value": "a"}},
    }
    return _plan_dict([scan, filt])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-sched.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    # 证据快照/复用索引指向同一临时库（与 v5_env 同惯例）
    from app.services.geocompute import reuse_index, run_evidence

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)

    def make_coordinator(name: str, **kw) -> ClusterCoordinator:
        return ClusterCoordinator(
            store=ClusterRunStore(factory),
            coordinator_id=name,
            local_slots=kw.pop("local_slots", 2),
            heartbeat_interval_s=kw.pop("heartbeat_interval_s", 0.05),
            tick_interval_s=0.02,
            leadership_ttl_s=kw.pop("leadership_ttl_s", 1.0),
            lease_ttl_s=kw.pop("lease_ttl_s", 5.0),
            preempt_wait_s=kw.pop("preempt_wait_s", 0.2),
            **kw,
        )

    store = ClusterRunStore(factory)
    yield store, make_coordinator, factory
    eng.dispose()


def _submit(store: ClusterRunStore, plan: dict, **kw) -> str:
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    plan_obj = ExecutionPlan.model_validate(plan)
    graph.validate_plan(plan_obj)
    return store.create_run(
        plan_snapshot=plan_obj.model_dump(),
        plan_fingerprint=plan_obj.graph_fingerprint(),
        owner_scope=kw.pop("owner_scope", "u:test"),
        **kw,
    )


def _wait_terminal_ticked(store, coord, run_id: str, timeout: float = 10.0) -> str:
    """轮询终态的同时持续 tick（重排/重试需要调度循环继续推进）。"""
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


def _wait_terminal(store: ClusterRunStore, run_id: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_run(run_id)
        if row and row["status"] in {"completed", "failed", "cancelled"}:
            return row["status"]
        time.sleep(0.02)
    row = store.get_run(run_id)
    return (row or {}).get("status") or "timeout"


class TestDurableScheduling:
    def test_submit_then_execute_to_terminal(self, env):
        """端到端：submit → tick 认领并执行 → 终态持久化 + 证据可读。"""
        store, make_coordinator, _ = env
        coord = make_coordinator("coord-a")
        rid = _submit(store, _simple_plan(unique="e2e"))
        stats = coord.tick()
        assert stats["leader"] is True
        assert stats["dispatched"] >= 1
        assert _wait_terminal(store, rid) == "completed"
        row = store.get_run(rid)
        assert row["status"] == "completed"
        assert row["terminal_at"] is not None
        # engine 内存注册表可读（同 run_id 注入生效）
        from app.services.geocompute.executor import engine

        run = engine.get_run(rid, owner_scope="u:test")
        assert run is not None and run.status.value == "completed"
        # 终态证据快照已落库（fail-open 但此处应成功）
        from app.services.geocompute import run_evidence

        snap = run_evidence.load_snapshot(rid, owner_scope="u:test")
        assert snap is not None

    def test_standby_coordinator_never_double_executes(self, env):
        """双 coordinator：同一时刻只有一个 leader；standby tick 不派发。"""
        store, make_coordinator, _ = env
        a = make_coordinator("coord-a")
        b = make_coordinator("coord-b")
        rid = _submit(store, _simple_plan(unique="dual"))
        stats_a = a.tick()
        assert stats_a["leader"] and stats_a["dispatched"] == 1
        stats_b = b.tick()
        assert stats_b["leader"] is False
        assert stats_b["dispatched"] == 0
        assert _wait_terminal(store, rid) == "completed"
        row = store.get_run(rid)
        assert row["coordinator_id"] == "coord-a"

    def test_leader_failover_recovers_inflight_run(self, env):
        """coordinator A 崩溃（lease 过期）→ B 当选 → reclaim → 重执行完成。"""
        store, make_coordinator, factory = env
        a = make_coordinator("coord-a", local_slots=1)
        _submit(store, _simple_plan(unique="fo1"))
        a.tick()
        a.wait_idle(timeout=5.0)  # 第一个 run 正常完成
        # 提交第二个 run 并模拟「A 认领后崩溃」：run lease 过期 + A leadership 过期
        rid2 = _submit(store, _simple_plan(unique="fo2"))
        from app.models.db_model import GeoComputeClusterRun as Run
        from app.models.db_model import GeoComputeClusterWorker as W

        with factory() as db:
            db.execute(
                update(Run).where(Run.run_id == rid2).values(
                    status="running",
                    coordinator_id="coord-a",
                    lease_epoch=1,
                    attempts=0,
                    lease_expires_at=_utcnow() - timedelta(seconds=1),
                    started_at=_utcnow(),
                )
            )
            db.execute(
                update(W).where(W.worker_id == "coord-a").values(
                    lease_expires_at=_utcnow() - timedelta(seconds=1)
                )
            )
            db.commit()
        b = make_coordinator("coord-b")
        stats = b.tick()
        assert stats["leader"] is True
        assert stats["reclaimed"] == 1
        assert _wait_terminal(store, rid2) == "completed"
        row = store.get_run(rid2)
        assert row["attempts"] == 1  # 一次 lease 丢失被诚实记账

    def test_attempt_budget_exhaustion_fails_honestly(self, env):
        """attempt 预算耗尽 → failed[WORKER_LOSS]（绝不无限重试）。"""
        store, make_coordinator, factory = env
        rid = _submit(store, _simple_plan(unique="budget"))
        from app.models.db_model import GeoComputeClusterRun as Run

        b = make_coordinator("coord-b")
        for _ in range(3):
            with factory() as db:
                db.execute(
                    update(Run).where(Run.run_id == rid).values(
                        status="running",
                        coordinator_id="ghost",
                        lease_epoch=Run.lease_epoch + 1,
                        lease_expires_at=_utcnow() - timedelta(seconds=1),
                    )
                )
                db.commit()
            b.tick()
        row = store.get_run(rid)
        assert row["status"] == "failed"
        assert row["error_code"] == "WORKER_LOSS"


class TestDistributedCancellation:
    def test_cancel_flag_from_any_store_kills_running_run(self, env):
        """任意进程写入的取消旗标 → 执行侧心跳观察 → run 收敛为 cancelled。"""
        store, make_coordinator, _ = env
        started = threading.Event()
        release = threading.Event()
        real_execute = ops.execute_node

        def slow_execute(ctx, node, upstream):
            if node.node_id == "scan":
                started.set()
                release.wait(timeout=10)
            return real_execute(ctx, node, upstream)

        ops.execute_node = slow_execute
        try:
            coord = make_coordinator("coord-a", local_slots=1)
            rid = _submit(store, _simple_plan(unique="cancel1"))
            coord.tick()
            assert started.wait(timeout=5), "节点应已开始执行"
            # 「另一个进程」写取消旗标
            changed, status = store.request_cancel(rid)
            assert changed
            # 确定性：等执行侧心跳线程点燃本地 token（被测对象就是这条链路）
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                exec_state = coord._inflight.get(rid)
                if exec_state is not None and exec_state.token.cancelled:
                    break
                time.sleep(0.01)
            assert coord._inflight[rid].token.cancelled
            release.set()
            assert _wait_terminal(store, rid) == "cancelled"
            # 终态幂等：重复取消 no-op
            changed2, _ = store.request_cancel(rid)
            assert changed2 is False
        finally:
            release.set()
            ops.execute_node = real_execute

    def test_cancel_queued_run_never_executes(self, env):
        store, make_coordinator, _ = env
        coord = make_coordinator("coord-a", local_slots=1)
        blocker_started = threading.Event()
        release = threading.Event()
        real_execute = ops.execute_node

        def slow_execute(ctx, node, upstream):
            if node.node_id == "scan":
                blocker_started.set()
                release.wait(timeout=10)
            return real_execute(ctx, node, upstream)

        ops.execute_node = slow_execute
        try:
            rid1 = _submit(store, _simple_plan(unique="cq1"))
            rid2 = _submit(store, _simple_plan(unique="cq2"))
            coord.tick()  # rid1 占唯一槽位；rid2 留队
            assert blocker_started.wait(timeout=5)
            changed, _ = store.request_cancel(rid2)
            assert changed
            release.set()
            assert _wait_terminal(store, rid1) == "completed"
            # rid1 落定后的下一次 tick：cancel sweep 把排队中的 rid2 收敛为终态
            coord.tick()
            assert _wait_terminal(store, rid2) == "cancelled"
            # rid2 从未被任何 coordinator 执行
            assert store.get_run(rid2)["coordinator_id"] is None
        finally:
            release.set()
            ops.execute_node = real_execute


class TestPreemption:
    def test_preemption_at_safe_point_requeues_then_completes(self, env):
        """低优先级在跑 → 高优先级等待 → 安全点让出 → PREEMPTED 回队 → 重执行完成。"""
        store, make_coordinator, _ = env
        started = threading.Event()
        release = threading.Event()
        real_execute = ops.execute_node

        def slow_execute(ctx, node, upstream):
            if node.node_id == "scan" and not release.is_set():
                started.set()
                release.wait(timeout=10)
            return real_execute(ctx, node, upstream)

        ops.execute_node = slow_execute
        try:
            coord = make_coordinator("coord-a", local_slots=1,
                                     preempt_wait_s=0.1)
            low = _submit(store, _simple_plan(unique="pre1"), priority=0)
            coord.tick()
            assert started.wait(timeout=5)
            high = _submit(store, _simple_plan(unique="pre2"), priority=10)
            # tick 直到抢占链路完整生效：请求旗标 → 心跳观察 → yield_event
            deadline = time.monotonic() + 5
            yielded = False
            while time.monotonic() < deadline:
                coord.tick()
                exec_state = coord._inflight.get(low)
                if exec_state is not None and exec_state.yield_event.is_set():
                    yielded = True
                    break
                time.sleep(0.05)
            assert yielded, "coordinator 应对低优先级在跑 run 请求让出"
            release.set()  # 节点完成 → engine 下一轮循环在安全点收敛
            # PREEMPTED 可驻留一瞬后立即回队（preempts=1 记账）
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row = store.get_run(low)
                if row["status"] == "queued" and row["preempts"] >= 1:
                    break
                time.sleep(0.02)
            assert row["status"] == "queued" and row["preempts"] == 1
            # 重排后两个 run 都完成（高优先级先获得槽位）
            assert _wait_terminal_ticked(store, coord, high) == "completed"
            assert _wait_terminal_ticked(store, coord, low) == "completed"
            assert store.get_run(low)["preempts"] == 1
            assert store.get_run(low)["attempts"] == 0  # 抢占≠lease 丢失
        finally:
            release.set()
            ops.execute_node = real_execute


class TestChannelMatching:
    def test_missing_profile_channel_holds_run_others_proceed(self, env):
        """broker 模式下无 worker 覆盖 raster：raster run 留队，普通 run 照常
        执行（队头阻塞削减）；worker 注册后 raster run 放行。"""
        store, make_coordinator, _ = env
        from app.services.task_queue import celery_app

        coord = make_coordinator("coord-a")
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(celery_app.conf, "task_always_eager", False)
            rid_raster = _submit(store, _simple_plan(unique="ras"),
                                 required_profiles=["raster"])
            rid_plain = _submit(store, _simple_plan(unique="plain"))
            stats = coord.tick()
            # raster 通道无人覆盖 → 留队；plain 立即派发
            assert stats["dispatched"] == 1
            assert store.get_run(rid_raster)["status"] == "queued"
            assert _wait_terminal(store, rid_plain) == "completed"
            # worker 注册 raster 能力 → 放行
            store.upsert_worker("w-raster", profiles={"raster": 2})
            stats2 = coord.tick()
            assert stats2["dispatched"] == 1
            assert _wait_terminal(store, rid_raster) == "completed"
        # eager 下 required profiles 恒匹配（诚实：durable 本地执行）


class TestFairness:
    def test_round_robin_across_tenants_no_starvation(self, env):
        from app.services.geocompute.cluster.fairness import fair_pick

        candidates = [
            {"id": i, "run_id": f"r{i}", "tenant_key": f"t:{i % 3}",
             "priority": 5, "required_profiles": []}
            for i in range(9)
        ]
        picked = fair_pick(candidates, slots=3, last_dispatch={})
        tenants = [p["tenant_key"] for p in picked]
        assert len(set(tenants)) == 3, "3 槽位应分属 3 个租户（轮转）"

    def test_fair_state_rebuild_from_dispatch_seq(self, env):
        store, make_coordinator, _ = env
        coord = make_coordinator("coord-a")
        rids = [
            _submit(store, _simple_plan(unique=f"fair{i}"), tenant_raw=f"org{i % 2}")
            for i in range(4)
        ]
        for _ in range(4):
            coord.tick()
        last = store.tenant_last_dispatch()
        assert len(last) == 2
        assert all(v > 0 for v in last.values())
