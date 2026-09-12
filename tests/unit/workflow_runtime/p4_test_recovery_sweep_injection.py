"""Workflow V6 — 恢复清扫：取消消费 / 孤儿复位 / finalize 愈合（P4 补强 W1）。

崩溃注入时序对齐真实故障：持约 → 回拨过期 → 清扫接管。重复清扫幂等。
"""
from __future__ import annotations

import datetime as _dt

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowInstanceNodeRow
from app.services.workflow_runtime import recovery as RC
from app.services.workflow_runtime import store as ST


@pytest.fixture()
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return ST.InstanceStore(factory=sessionmaker(bind=engine))


def _make(store):
    inst = store.create_instance(
        package_id="recipe-rc", package_version="1.0.0",
        package_fingerprint="rc" * 16, owner_scope="u:recovery",
        session_id="s-recovery", node_specs=[
            {"node_id": "n:1", "optional": False},
            {"node_id": "n:2", "optional": False},
        ])
    return inst["instance_id"]


def _backdate_lease(store, iid: str, node_id: str, seconds: float = 1.0) -> None:
    with store._factory() as db:
        db.execute(
            sa.update(WorkflowInstanceNodeRow)
            .where(WorkflowInstanceNodeRow.instance_id == iid,
                   WorkflowInstanceNodeRow.node_id == node_id)
            .values(lease_expires_at=_dt.datetime.utcnow()
                    - _dt.timedelta(seconds=seconds))
        )
        db.commit()


def test_sweep_reports_empty_world(store) -> None:
    report = RC.sweep_recoverable(store)
    assert report["scanned"] == 0
    assert report["errors"] == 0


def test_sweep_consumes_leftover_cancel(store) -> None:
    iid = _make(store)
    # 实例行武装 cancel_requested（取消旗标置位后 driver 死亡的时序）。
    store.update_instance(iid, fields={"cancel_requested": True})
    report = RC.sweep_recoverable(store)
    assert report["cancelled"] >= 1
    inst = store.get_instance(iid)
    assert inst["status"] == "cancelled"


def test_sweep_resets_orphaned_lease_expired_nodes(store) -> None:
    iid = _make(store)
    # 崩溃时序：driver 持 run 租约 → 节点 RUNNING → driver 死亡（租约回拨过期）。
    assert store.acquire_run_lease(iid, owner_scope="u:recovery", token="rt-dead", ttl_s=30) is True
    # PENDING→READY→RUNNING（claim=True 写节点租约，调度 CAS 纪律）。
    r0 = store.transition_node(iid, "n:1", "READY", reason="test")
    assert r0.ok, getattr(r0, "code", "")
    r = store.transition_node(iid, "n:1", "RUNNING", reason="test", claim=True, claimed_by="rt-dead")
    assert r.ok, getattr(r, "code", "")
    _backdate_lease(store, iid, "n:1")
    with store._factory() as db:
        import sqlalchemy as sa2
        from app.models.db_model import WorkflowInstanceRow
        db.execute(sa2.update(WorkflowInstanceRow)
                   .where(WorkflowInstanceRow.instance_id == iid)
                   .values(run_lease_expires_at=_dt.datetime.utcnow() - _dt.timedelta(seconds=1)))
        db.commit()
    report = RC.sweep_recoverable(store)
    assert report["orphan_reset"] >= 1
    states = store.get_node_states(iid)
    assert states["n:1"] == "READY"


def test_sweep_is_idempotent(store) -> None:
    iid = _make(store)
    store.request_node_cancel(iid, ["n:1"], actor="user")
    first = RC.sweep_recoverable(store)
    second = RC.sweep_recoverable(store)
    # 第二轮不再产生新的取消/复位（CAS 收敛幂等）。
    assert second["cancelled"] == 0
    assert second["orphan_reset"] == 0 or first["cancelled"] >= 1


def test_recovery_interval_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RECOVERY_INTERVAL_S", "0")
    assert RC.recovery_interval_s() == 0.0
    monkeypatch.setenv("GIS_WORKFLOW_RECOVERY_INTERVAL_S", "5.5")
    assert RC.recovery_interval_s() == 5.5
    monkeypatch.setenv("GIS_WORKFLOW_RECOVERY_INTERVAL_S", "garbage")
    assert RC.recovery_interval_s() == 60.0
