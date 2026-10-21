"""Workflow V6 — journal 顺序读/分页有界/越界安全（P4 补强：W10）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
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
        package_id="recipe-jr", package_version="1.0.0",
        package_fingerprint="jr" * 16, owner_scope="u:journal",
        session_id="s-journal",
        node_specs=[{"node_id": "n:1", "optional": False}])
    return inst["instance_id"]


def test_append_then_sequential_read_preserves_causal_order(store) -> None:
    iid = _make(store)
    for i in range(5):
        store.append_event(iid, kind=f"EV_{i}", node_id="n:1", reason=f"r{i}")
    events = store.get_events(iid)
    kinds = [e["kind"] for e in events]
    assert kinds == sorted(kinds)  # id 升序 = 因果序
    assert [e["reason"] for e in events if e["kind"].startswith("EV_")] == [f"r{i}" for i in range(5)]


def test_pagination_by_after_id_is_stable(store) -> None:
    iid = _make(store)
    for i in range(7):
        store.append_event(iid, kind=f"PG_{i}")
    page1 = store.get_events(iid, limit=3)
    page2 = store.get_events(iid, limit=3, after_id=page1[-1]["id"])
    ids1 = [e["id"] for e in page1]
    ids2 = [e["id"] for e in page2]
    assert ids1 and ids2 and min(ids2) > max(ids1)
    assert len(page1) == 3


def test_kind_filter_narrows_without_breaking_order(store) -> None:
    iid = _make(store)
    for i in range(4):
        store.append_event(iid, kind="A" if i % 2 == 0 else "B")
    only_a = store.get_events(iid, kind="A")
    assert {e["kind"] for e in only_a} == {"A"}
    assert len(only_a) == 2


def test_unknown_instance_read_is_empty(store) -> None:
    assert store.get_events("no-such-instance") == []


def test_payload_roundtrip(store) -> None:
    iid = _make(store)
    store.append_event(iid, kind="CLONE", payload={"source": "inst-src"})
    events = store.get_events(iid, kind="CLONE")
    assert events[0]["payload"]["source"] == "inst-src"
