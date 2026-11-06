"""Behavioral regressions for the workflow Low review findings."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.fingerprints import canonicalize_parameters
from app.services.workflow_runtime.store import InstanceStore, SubworkflowCapacityExceeded
from app.services.workflow_runtime.subworkflow_inputs import child_input_bindings


def test_large_integer_parameters_retain_identity():
    assert canonicalize_parameters(2**53) != canonicalize_parameters(2**53 + 1)
    assert canonicalize_parameters(1) == canonicalize_parameters(1.0)


def test_child_roles_bind_by_names_despite_reversed_node_order():
    parent = {"nodes": [
        {"node_id": "data:left", "role": "subject"},
        {"node_id": "data:right", "role": "reference"},
        {"node_id": "sub:child"}], "edges": [
            {"from": "data:left.data", "to": "sub:child.subject"},
            {"from": "data:right.data", "to": "sub:child.reference"}]}
    child = {"nodes": [
        {"node_id": "data:a", "kind": "data_input", "role": "reference"},
        {"node_id": "data:z", "kind": "data_input", "role": "subject"}]}
    rows = [{"node_id": "data:a", "state": C.NodeState.PENDING},
            {"node_id": "data:z", "state": C.NodeState.PENDING}]
    bindings = child_input_bindings(parent, [
        {"node_id": "data:left", "bound_ref": "ds:subject"},
        {"node_id": "data:right", "bound_ref": "ds:reference"}],
        "sub:child", child, rows, ["ds:subject", "ds:reference"])
    assert bindings == {"data:a": "ds:reference", "data:z": "ds:subject"}
    with pytest.raises(ValueError, match="Ambiguous"):
        child_input_bindings({}, [], "sub:child", child, rows, ["one", "two"])


def test_child_capacity_reservations_are_atomic(tmp_path, monkeypatch):
    from app.models.db_model import Organization
    from app.services.workflow_runtime import subworkflow

    monkeypatch.setattr(subworkflow, "MAX_ACTIVE_SUBINSTANCES", 1)
    engine = create_engine(f"sqlite:///{tmp_path / 'capacity.sqlite'}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(Organization(id=1, name="capacity", slug="capacity"))
        db.commit()
    store = InstanceStore(factory)
    def create(_):
        try:
            return store.create_instance(package_id="p", package_version="1", package_fingerprint="f",
                owner_scope="owner", org_id="1", parent_instance_id="parent",
                node_specs=[{"node_id": "output", "optional": False}])["instance_id"]
        except SubworkflowCapacityExceeded:
            return "capacity"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(4)))
    assert results.count("capacity") == 3
    assert store.count_active_subworkflows("owner") == 1
    assert store.count_active_subworkflows("other") == 0


def test_dispatch_loop_entries_do_not_retain_closed_loops():
    import gc
    import weakref
    from app.services.workflow_runtime import dispatch

    loop = asyncio.new_event_loop()
    async def bind():
        sem = dispatch.get_slots_semaphore()
        assert dispatch.get_slots_semaphore() is sem
        return weakref.ref(sem)
    sem_ref = loop.run_until_complete(bind())
    loop_ref = weakref.ref(loop)
    loop.close()
    del loop
    gc.collect()
    assert loop_ref() is None
    assert sem_ref() is None


@pytest.mark.asyncio
async def test_cancel_rechecks_terminal_status_after_cas_conflict():
    from types import SimpleNamespace
    from app.services.workflow_runtime.service import WorkflowRuntimeService

    inst = {"revision": 1, "status": C.InstanceStatus.RUNNING}
    events = []
    def update(iid, *, owner_scope, expected_revision, fields):
        assert expected_revision == 1
        inst.update(revision=2, status=C.InstanceStatus.SUCCEEDED)
        return None
    store = SimpleNamespace(get_instance=lambda *args: dict(inst), update_instance=update,
                            append_event=lambda *args, **kw: events.append(kw))
    service = WorkflowRuntimeService(store=store)
    assert await service.cancel_instance("i", owner_scope="owner") == {"cancelled": False, "status": C.InstanceStatus.SUCCEEDED}
    assert not events


@pytest.mark.asyncio
async def test_retry_does_not_clear_a_pending_cancellation():
    from types import SimpleNamespace
    from app.services.workflow_runtime.service import WorkflowRuntimeService, InstanceBusy

    store = SimpleNamespace(get_instance=lambda *args: {
        "revision": 3, "status": C.InstanceStatus.RUNNING, "cancel_requested": True})
    service = WorkflowRuntimeService(store=store)
    with pytest.raises(InstanceBusy, match="clone"):
        await service.retry_failed_nodes("i", owner_scope="owner")


@pytest.mark.asyncio
async def test_failed_run_requires_explicit_retry():
    from types import SimpleNamespace
    from app.services.workflow_runtime.service import WorkflowRuntimeService, WorkflowRuntimeError

    service = WorkflowRuntimeService(store=SimpleNamespace(get_instance=lambda *args: {"status": C.InstanceStatus.FAILED}))
    with pytest.raises(WorkflowRuntimeError, match="RETRY_REQUIRED"):
        await service.run_instance("i", owner_scope="owner")


def test_dispatch_gauges_expose_shared_slot_counters():
    from prometheus_client import REGISTRY
    from app.services.workflow_runtime import dispatch

    assert REGISTRY.get_sample_value("workflow_dispatch_slots_in_use") == dispatch.slots_in_use()
    assert REGISTRY.get_sample_value("workflow_dispatch_slots_per_loop") == dispatch.dispatch_slots()


@pytest.mark.asyncio
async def test_recompute_decision_retries_cas_without_dropping_new_ring_entries():
    from types import SimpleNamespace
    from app.services.workflow_runtime.recompute import ChangeApplier

    writes = []
    def update(iid, *, owner_scope, expected_revision, fields):
        writes.append((expected_revision, fields["decisions"]))
        return None if len(writes) == 1 else {"revision": 3}
    fresh = {"revision": 2, "decisions": [{"seq": 8, "changes_fp": "concurrent"}]}
    store = SimpleNamespace(update_instance=update, get_instance=lambda *args: fresh)
    await ChangeApplier(store, owner_scope="owner")._append_decision("i", {"revision": 1, "decisions": []},
        C.RecomputeDecision(seq=1, changes_fingerprint="new"))
    assert [revision for revision, _ in writes] == [1, 2]
    assert writes[-1][1][0]["changes_fp"] == "concurrent"
    assert writes[-1][1][1]["seq"] == 9


def test_long_owner_scopes_reject_before_creating_colliding_rows():
    from app.services.workflow_runtime.registry import PackageRegistry

    owner = "u:" + "a" * 39
    with pytest.raises(ValueError, match="owner_scope"):
        InstanceStore().create_instance(package_id="p", package_version="1", package_fingerprint="f",
            owner_scope=owner, node_specs=[{"node_id": "output", "optional": False}])
    with pytest.raises(ValueError, match="owner_scope"):
        PackageRegistry().register(None, owner_scope=owner)
    with pytest.raises(ValueError, match="owner_scope"):
        PackageRegistry().publish("p", "1", owner_scope=owner)
