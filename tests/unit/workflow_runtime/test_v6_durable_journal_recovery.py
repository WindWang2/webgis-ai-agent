"""Workflow V6 —— durable journal / node lease / recovery sweep 测试。

覆盖 Phase B 验收面：
- 状态转移与事件日志同事务（journal 完整、因果序、分页有界）；
- 节点租约 heartbeat（fencing：非持有人/非 RUNNING 拒绝续期）；
- 节点级取消旗标（终态不追改、条件更新幂等）；
- 恢复清扫：遗留取消消费、孤儿复位、finalize 愈合；
- heartbeat 不入 journal（liveness 不是事实）。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import recovery as RC
from app.services.workflow_runtime import store as ST


def _sqlite_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def factory():
    return _sqlite_factory()


@pytest.fixture
def store(factory):
    return ST.InstanceStore(factory=factory)


def _make(store, owner="u:abc", session="s1"):
    return store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="fp" * 16, owner_scope=owner,
        session_id=session, node_specs=[
            {"node_id": "data:subject", "optional": False},
            {"node_id": "cap:buffer", "optional": False},
            {"node_id": "output:zone", "optional": False},
        ])


def _backdate(store, iid: str, node_id: str, seconds: float = 1.0,
              field: str = "lease_expires_at") -> None:
    import datetime as _dt

    import sqlalchemy as sa

    from app.models.db_model import WorkflowInstanceNodeRow

    with store._factory() as db:
        db.execute(
            sa.update(WorkflowInstanceNodeRow)
            .where(WorkflowInstanceNodeRow.instance_id == iid,
                   WorkflowInstanceNodeRow.node_id == node_id)
            .values(**{field: _dt.datetime.utcnow()
                       - _dt.timedelta(seconds=seconds)})
        )
        db.commit()


def _expire_run_lease(store, iid: str, token: str = "rt-dead") -> None:
    """模拟 driver 持约后崩溃：先持约再回拨过期（真实崩溃时序）。"""
    import datetime as _dt

    import sqlalchemy as sa

    from app.models.db_model import WorkflowInstanceRow

    assert store.acquire_run_lease(iid, owner_scope="u:abc", token=token)
    with store._factory() as db:
        db.execute(
            sa.update(WorkflowInstanceRow)
            .where(WorkflowInstanceRow.instance_id == iid)
            .values(run_lease_expires_at=_dt.datetime.utcnow()
                    - _dt.timedelta(seconds=1))
        )
        db.commit()


# ── 事件日志 ─────────────────────────────────────────────────────────────

def test_transition_writes_journal_event_in_same_transaction(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="DEPS_OK", event="driver")
    events = store.get_events(iid)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == C.EventKind.STATE_TRANSITION
    assert ev["from_state"] == C.NodeState.PENDING
    assert ev["to_state"] == C.NodeState.READY
    assert ev["reason"] == "DEPS_OK"
    assert ev["actor"] == "driver"
    assert ev["id"] > 0
    # 非法转移（PENDING→RUNNING 不在合法表）不写 journal（原子性：转移
    # 与事件同事务）
    fresh = _make(store)
    r = store.transition_node(fresh["instance_id"], "data:subject",
                              C.NodeState.RUNNING)
    assert not r.ok
    assert len(store.get_events(fresh["instance_id"])) == 0
    assert len(store.get_events(iid)) == 1


def test_journal_records_error_payload_and_attempt(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "cap:buffer", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "cap:buffer", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-1")
    store.transition_node(iid, "cap:buffer", C.NodeState.FAILED,
                          require_claim=True, claimed_by="rt-1",
                          complete=True, reason="EXEC_FAIL:TIMEOUT",
                          event="driver",
                          patch={"error_code": "NODE_TIMEOUT",
                                 "attempts_increment": True})
    events = store.get_events(iid, kind=C.EventKind.STATE_TRANSITION)
    fail_ev = events[-1]
    assert fail_ev["to_state"] == C.NodeState.FAILED
    assert fail_ev["payload"]["error_code"] == "NODE_TIMEOUT"
    assert fail_ev["attempt"] == 1


def test_get_events_pagination_and_kind_filter(store):
    inst = _make(store)
    iid = inst["instance_id"]
    for _ in range(5):
        store.append_event(iid, kind=C.EventKind.RETRY_SCHEDULED,
                           reason="BACKOFF")
    store.append_event(iid, kind=C.EventKind.COMPENSATION, reason="CLEANUP")
    page1 = store.get_events(iid, limit=3)
    assert len(page1) == 3
    page2 = store.get_events(iid, limit=3, after_id=page1[-1]["id"])
    assert len(page2) == 3
    assert page2[0]["id"] > page1[-1]["id"]
    only_retry = store.get_events(iid, kind=C.EventKind.RETRY_SCHEDULED)
    assert len(only_retry) == 5
    # 上界钳制
    assert len(store.get_events(iid, limit=10**6)) == 6


# ── 节点租约与心跳 ───────────────────────────────────────────────────────

def test_heartbeat_renews_lease_only_for_claim_owner(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "cap:buffer", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "cap:buffer", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-1", lease_ttl_s=30)
    row = store.get_node(iid, "cap:buffer")
    assert row["lease_expires_at"]
    assert store.heartbeat_node(iid, "cap:buffer", token="rt-wrong") is False
    assert store.heartbeat_node(iid, "cap:buffer", token="rt-1",
                                ttl_s=60) is True
    # 非 RUNNING（已完成）不可续
    store.transition_node(iid, "cap:buffer", C.NodeState.SUCCEEDED,
                          require_claim=True, claimed_by="rt-1",
                          complete=True, reason="EXEC_OK")
    assert store.heartbeat_node(iid, "cap:buffer", token="rt-1") is False
    row = store.get_node(iid, "cap:buffer")
    assert row["lease_expires_at"] == ""  # 终态清租约


def test_heartbeat_does_not_pollute_journal(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "cap:buffer", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "cap:buffer", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-1")
    for _ in range(5):
        store.heartbeat_node(iid, "cap:buffer", token="rt-1")
    # liveness 不是事实：journal 只有状态转移
    assert all(e["kind"] == C.EventKind.STATE_TRANSITION
               for e in store.get_events(iid))


# ── 节点级取消 ───────────────────────────────────────────────────────────

def test_request_node_cancel_skips_terminal_and_is_idempotent(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "cap:buffer", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "cap:buffer", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-1")
    store.transition_node(iid, "output:zone", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "output:zone", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-2")
    store.transition_node(iid, "output:zone", C.NodeState.SUCCEEDED,
                          require_claim=True, claimed_by="rt-2",
                          complete=True, reason="X")
    got = store.request_node_cancel(
        iid, ["data:subject", "cap:buffer", "output:zone", "cap:buffer"],
        actor="api")
    # 终态 output:zone 不追改；重复 nid 去重（第二次 rowcount=0）
    assert sorted(got) == ["cap:buffer", "data:subject"]
    for nid in ("data:subject", "cap:buffer"):
        assert store.get_node(iid, nid)["cancel_requested"] is True
    assert store.get_node(iid, "output:zone")["cancel_requested"] is False
    kinds = [e["kind"] for e in store.get_events(iid)
             if e["kind"] == C.EventKind.NODE_CANCEL_REQUESTED]
    assert len(kinds) == 2


def test_clear_node_cancel(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.request_node_cancel(iid, ["data:subject"])
    assert store.clear_node_cancel(iid, "data:subject") is True
    assert store.get_node(iid, "data:subject")["cancel_requested"] is False


# ── 恢复清扫 ─────────────────────────────────────────────────────────────

def test_sweep_resets_orphan_and_emits_event(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "data:subject", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-dead")
    _backdate(store, iid, "data:subject")
    _expire_run_lease(store, iid)  # driver 崩溃时序：持约 → 过期
    report = RC.sweep_recoverable(store, ttl_s=0.0)
    assert report["orphan_reset"] == 1
    assert store.get_node_states(iid)["data:subject"] == C.NodeState.READY
    resets = [e for e in store.get_events(iid)
              if e["kind"] == C.EventKind.RECOVERY_ORPHAN_RESET]
    assert len(resets) == 1 and resets[0]["node_id"] == "data:subject"
    # 幂等：再扫无事可做
    report2 = RC.sweep_recoverable(store, ttl_s=0.0)
    assert report2["orphan_reset"] == 0


def test_sweep_consumes_leftover_cancel_flag(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.update_instance(iid, fields={"cancel_requested": True})
    report = RC.sweep_recoverable(store, ttl_s=0.0)
    assert report["cancelled"] == 3
    assert store.get_instance(iid)["status"] == C.InstanceStatus.CANCELLED
    assert all(s == C.NodeState.CANCELLED
               for s in store.get_node_states(iid).values())


def test_sweep_heals_crash_before_finalize(store):
    """全部节点已决但实例行 RUNNING（crash 在 finalize 边界）→ 补终态。"""
    inst = _make(store)
    iid = inst["instance_id"]
    now_states = {"data:subject": C.NodeState.SUCCEEDED,
                  "cap:buffer": C.NodeState.SUCCEEDED,
                  "output:zone": C.NodeState.SUCCEEDED}
    for nid, target in now_states.items():
        store.transition_node(iid, nid, C.NodeState.READY,
                              expected_from=C.NodeState.PENDING)
        store.transition_node(iid, nid, C.NodeState.RUNNING,
                              expected_from=C.NodeState.READY, claim=True,
                              claimed_by="rt-1")
        store.transition_node(iid, nid, C.NodeState.SUCCEEDED,
                              require_claim=True, claimed_by="rt-1",
                              complete=True, reason="EXEC_OK",
                              patch={"output_ref": "ref:x"})
    # 手工把实例打回 RUNNING（模拟 finalize 前崩溃）
    store.update_instance(iid, fields={"status": C.InstanceStatus.RUNNING,
                                       "terminal_at": None})
    _expire_run_lease(store, iid)  # driver 死亡 → 租约过期 → 恢复面接管
    report = RC.sweep_recoverable(store, ttl_s=0.0)
    assert report["finalized"] == 1
    assert store.get_instance(iid)["status"] == C.InstanceStatus.SUCCEEDED
    heals = [e for e in store.get_events(iid)
             if e["kind"] == C.EventKind.RECOVERY_FINALIZE]
    assert heals and heals[0]["reason"] == "CRASH_BEFORE_FINALIZE"


def test_sweep_skips_fresh_leaseless_instances(store):
    """刚创建、从未驱动的实例不在恢复列（租约门）。"""
    _make(store)
    report = RC.sweep_recoverable(store, ttl_s=120.0)
    assert report["scanned"] == 0


def test_periodic_sweep_respects_interval_gate():
    """interval<=0 关闭；周期函数立即返回。"""

    async def _run():
        return await asyncio.wait_for(
            RC.periodic_recovery_sweep(interval_seconds=0), timeout=1.0)

    asyncio.run(_run())
