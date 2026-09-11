"""Workflow V6 — driver 重试退避门与取消 pre-claim 门（P4 补强：W2/W3）。

_split_retry_gates 二分语义：next_ready_at 未到的节点回等待集；
被取消旗标标记的 ready 节点在派发前即收敛 CANCELLED（pre-claim 门）。
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
from app.services.workflow_runtime import store as ST
from app.services.workflow_runtime.driver import Driver


@pytest.fixture()
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return ST.InstanceStore(factory=sessionmaker(bind=engine))


def _make(store, node_ids):
    inst = store.create_instance(
        package_id="recipe-gate", package_version="1.0.0",
        package_fingerprint="gt" * 16, owner_scope="u:gate",
        session_id="s-gate",
        node_specs=[{"node_id": n, "optional": False} for n in node_ids])
    iid = inst["instance_id"]
    store.update_instance(iid, fields={"status": "running"})
    return iid


def _driver(store) -> Driver:
    return Driver(store, owner_scope="u:gate", deadline_s=5.0)


def _gate(store, iid: str, node_id: str, seconds: float) -> None:
    with store._factory() as db:
        db.execute(
            sa.update(WorkflowInstanceNodeRow)
            .where(WorkflowInstanceNodeRow.instance_id == iid,
                   WorkflowInstanceNodeRow.node_id == node_id)
            .values(next_ready_at=_dt.datetime.utcnow()
                    + _dt.timedelta(seconds=seconds))
        )
        db.commit()


@pytest.mark.asyncio
async def test_ready_without_gates_all_dispatchable(store) -> None:
    iid = _make(store, ["n:1", "n:2"])
    for n in ("n:1", "n:2"):
        store.transition_node(iid, n, "READY")
    driver = _driver(store)
    dispatchable, gated = await driver._split_retry_gates(iid, ["n:1", "n:2"])
    assert dispatchable == ["n:1", "n:2"]
    assert gated == []


@pytest.mark.asyncio
async def test_backoff_gate_holds_node_back(store) -> None:
    iid = _make(store, ["n:1", "n:2"])
    for n in ("n:1", "n:2"):
        store.transition_node(iid, n, "READY")
    _gate(store, iid, "n:1", seconds=120)
    driver = _driver(store)
    dispatchable, gated = await driver._split_retry_gates(iid, ["n:1", "n:2"])
    assert dispatchable == ["n:2"]
    assert gated == ["n:1"]
    # gated_earliest 暴露给等待循环（确定性等待而非轮询空转）。
    assert driver._gated_earliest is not None


@pytest.mark.asyncio
async def test_cancel_flag_pre_claim_converges(store) -> None:
    iid = _make(store, ["n:1"])
    store.transition_node(iid, "n:1", "READY")
    store.request_node_cancel(iid, ["n:1"], actor="user")
    driver = _driver(store)
    dispatchable, gated = await driver._split_retry_gates(iid, ["n:1"])
    assert dispatchable == []
    assert gated == []
    assert store.get_node(iid, "n:1")["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_empty_ready_is_noop(store) -> None:
    driver = _driver(store)
    assert await driver._split_retry_gates("wi-any", []) == ([], [])


@pytest.mark.asyncio
async def test_unknown_node_in_ready_is_dropped(store) -> None:
    iid = _make(store, ["n:1"])
    driver = _driver(store)
    dispatchable, gated = await driver._split_retry_gates(iid, ["ghost"])
    assert dispatchable == [] and gated == []
