"""Workflow V6 — run 级租约互斥（P4 补强：两级租约的 run 级 CAS 面）。

W1 缺口：driver 崩溃后的租约抢占路径 —— 过期持有者的互斥让位、
非持有人拒绝、终态实例不可再驱动。
"""
from __future__ import annotations

import datetime as _dt

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowInstanceRow
from app.services.workflow_runtime import store as ST


@pytest.fixture()
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return ST.InstanceStore(factory=sessionmaker(bind=engine))


def _make(store, owner="u:lease", session="s-lease"):
    inst = store.create_instance(
        package_id="recipe-lease", package_version="1.0.0",
        package_fingerprint="lf" * 16, owner_scope=owner,
        session_id=session, node_specs=[{"node_id": "n:only", "optional": False}])
    return inst["instance_id"]


def _acquire(store, iid, token, owner="u:lease", ttl=30.0):
    return store.acquire_run_lease(iid, owner_scope=owner, token=token, ttl_s=ttl)


def test_first_acquire_grants_and_renew_by_same_token(store) -> None:
    iid = _make(store)
    assert _acquire(store, iid, "rt-1") is True
    # 同 token 续期合法（heartbeat 等价）。
    assert _acquire(store, iid, "rt-1") is True


def test_second_holder_rejected_while_held(store) -> None:
    iid = _make(store)
    assert _acquire(store, iid, "rt-1") is True
    assert _acquire(store, iid, "rt-2") is False


def test_expired_lease_is_preemptible(store) -> None:
    iid = _make(store)
    assert _acquire(store, iid, "rt-dead", ttl=1.0) is True
    # 模拟崩溃：时间回拨使租约过期。
    with store._factory() as db:
        db.execute(
            sa.update(WorkflowInstanceRow)
            .where(WorkflowInstanceRow.instance_id == iid)
            .values(run_lease_expires_at=_dt.datetime.utcnow() - _dt.timedelta(seconds=1))
        )
        db.commit()
    assert _acquire(store, iid, "rt-next") is True


def test_release_only_by_holder(store) -> None:
    iid = _make(store)
    assert _acquire(store, iid, "rt-1") is True
    assert store.release_run_lease(iid, owner_scope="u:lease", token="rt-other") is False
    assert store.release_run_lease(iid, owner_scope="u:lease", token="rt-1") is True
    # 释放后新持有人可获得。
    assert _acquire(store, iid, "rt-2") is True


def test_cancelled_instance_never_grants_lease(store) -> None:
    from app.services.workflow_runtime import recovery as RC

    iid = _make(store)
    # 真实取消收敛路径：节点旗标 → recovery 消费 → 实例落 CANCELLED。
    store.request_node_cancel(iid, ["n:only"], actor="test")
    assert RC._consume_cancel(store, iid) >= 1
    assert _acquire(store, iid, "rt-1") is False


def test_owner_scope_isolation(store) -> None:
    iid = _make(store, owner="u:island")
    # 他域查询/持有均不可见（owner_scope 过滤）。
    assert _acquire(store, iid, "rt-1", owner="u:other") is False
    assert _acquire(store, iid, "rt-1", owner="u:island") is True
