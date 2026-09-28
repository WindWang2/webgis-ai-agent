"""workflow_instances turn↔workflow 因果捕获（H04 桥接列）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.lib.runtime.context import bind_runtime_context
from app.services.workflow_runtime import store as ST


@pytest.fixture()
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return ST.InstanceStore(factory=sessionmaker(bind=engine))


def _specs():
    return [{"node_id": "n:1", "optional": False}]


def test_create_instance_captures_turn_context(store) -> None:
    with bind_runtime_context(turn_id="turn-abc", run_id="run-xyz",
                              session_id="s-link"):
        inst = store.create_instance(
            package_id="recipe-link", package_version="1.0.0",
            package_fingerprint="ln" * 16, owner_scope="u:link",
            session_id="s-link", node_specs=_specs())
    assert inst["turn_id"] == "turn-abc"
    assert inst["run_id"] == "run-xyz"


def test_create_instance_without_turn_context_stays_null(store) -> None:
    """REST 直启/恢复扫描（无 turn 上下文）：NULL，语义不变。"""
    inst = store.create_instance(
        package_id="recipe-link", package_version="1.0.0",
        package_fingerprint="ln" * 16, owner_scope="u:link",
        session_id="s-link", node_specs=_specs())
    assert inst.get("turn_id") in (None, "")
    assert inst.get("run_id") in (None, "")


def test_get_instance_returns_turn_link(store) -> None:
    with bind_runtime_context(turn_id="turn-get", run_id="run-get"):
        inst = store.create_instance(
            package_id="recipe-link", package_version="1.0.0",
            package_fingerprint="ln" * 16, owner_scope="u:link",
            session_id="s-link", node_specs=_specs())
    loaded = store.get_instance(inst["instance_id"], "u:link")
    assert loaded["turn_id"] == "turn-get"
    assert loaded["run_id"] == "run-get"
