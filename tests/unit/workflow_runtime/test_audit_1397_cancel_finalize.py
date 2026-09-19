"""#1397: cancelled durable → CANCELLED; finalize/CAS guards; no shared-token abort."""
from __future__ import annotations

import pytest

from app.lib.cancellation import OperationCancelled
from app.services.geocompute.errors import FailureClass, classify_failure
from app.services.workflow_runtime.adapters_geocompute import GeoComputeNodeOutcome
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.driver import Driver


def test_operation_cancelled_classifies_as_cancelled():
    assert classify_failure(OperationCancelled("stop")) is FailureClass.CANCELLED


def test_fail_or_cancel_treats_failure_class_cancelled(monkeypatch):
    """_fail_or_cancel must land CANCELLED when failure_class=cancelled even if
    error_code is DURABLE_JOB_FAILED (pre-#1397 durable path)."""
    transitions = []

    class FakeStore:
        def get_node(self, *a, **k):
            return {"attempts": 0}

        def transition_node(self, *a, **k):
            transitions.append((a, k))
            class R:
                ok = True
            return R()

        def append_event(self, *a, **k):
            return None

    driver = Driver(FakeStore(), owner_scope="u:t", deadline_s=5.0)
    outcome = GeoComputeNodeOutcome(
        ok=False, error_code="DURABLE_JOB_FAILED",
        failure_class=FailureClass.CANCELLED.value,
    )

    import asyncio

    async def _run():
        await driver._fail_or_cancel("inst", "n1", "s1", "tok", outcome)

    asyncio.run(_run())
    assert transitions, "expected a transition"
    # to_state is 3rd positional arg
    assert transitions[0][0][2] == C.NodeState.CANCELLED


@pytest.mark.asyncio
async def test_finalize_skips_terminal_instance(factory=None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.services.workflow_runtime.store import InstanceStore

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    store = InstanceStore(factory=fac)
    inst = store.create_instance(
        package_id="p", package_version="1",
        package_fingerprint="f" * 32, owner_scope="u:a",
        session_id="s",
        node_specs=[{"node_id": "n1", "optional": False}])
    iid = inst["instance_id"]
    # Mark superseded
    store.update_instance(
        iid, owner_scope="u:a",
        fields={"status": C.InstanceStatus.SUPERSEDED})
    driver = Driver(store, owner_scope="u:a", deadline_s=5.0)
    await driver._finalize_instance(
        iid, {"n1": C.NodeState.SUCCEEDED}, {}, C.InstanceStatus.SUCCEEDED)
    fresh = store.get_instance(iid, "u:a")
    assert fresh["status"] == C.InstanceStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_durable_dispatch_maps_operation_cancelled(monkeypatch):
    from app.services.workflow_runtime.dispatch import DurableDispatcher

    class Boom:
        def get(self, *a, **k):
            raise OperationCancelled("user cancel")

    def fake_dispatch(*a, **k):
        return {"job_id": "1"}

    def fake_await(*a, **k):
        raise OperationCancelled("user cancel")

    monkeypatch.setattr(
        "app.services.workflow_runtime.dispatch._dispatch_sync", fake_dispatch)
    monkeypatch.setattr(
        "app.services.workflow_runtime.dispatch._await_job_sync", fake_await)

    d = DurableDispatcher(owner_scope="u:a")
    # Bypass slot semaphore by calling _await_job directly
    from types import SimpleNamespace
    outcome = await d._await_job(
        {"node_id": "n"}, SimpleNamespace(), SimpleNamespace(),
        "sess", cancel_token=None)
    assert outcome.ok is False
    assert outcome.error_code == "CANCELLED"
    assert outcome.failure_class == FailureClass.CANCELLED.value
