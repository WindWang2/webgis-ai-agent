"""失效桥端到端测试（真实 InstanceStore/ChangeApplier/PackageRegistry）。

P1 修复红测：dataset 版本变化必须让**真正消费该 ref 的节点及其后代**
转 STALE——此前 PendingChange(target_kind=subject_type) 永远无法命中
seed（结构性 no-op，被 FakeWorkflowSvc 测试掩盖）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.mission  # noqa: F401
import app.models.spatial_events  # noqa: F401
from app.services.spatial_events import contracts as C
from app.services.spatial_events.invalidation_bridge import InvalidationBridge
from app.services.workflow_runtime.contracts import NodeState
from app.services.workflow_runtime.registry import PackageRegistry
from app.services.workflow_runtime.service import WorkflowRuntimeService
from app.services.workflow_runtime.store import InstanceStore

_DAG = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False,
         "outputs": [{"name": "data", "artifact_type": ""}]},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False,
         "inputs": [{"name": "input", "artifact_type": ""}],
         "outputs": [{"name": "output", "artifact_type": ""}]},
        {"node_id": "output:zone", "kind": "output", "optional": False,
         "inputs": [{"name": "product", "artifact_type": ""}]},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


@pytest.fixture()
def wf_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = InstanceStore(factory=factory)
    registry = PackageRegistry(factory=factory)
    svc = WorkflowRuntimeService(store=store, registry=registry)
    # 直接落包行（typed_dag 与生产一致的最小形态）
    from app.models.db_model import WorkflowPackageRow
    from datetime import datetime as _dt

    with factory() as db:
        db.add(WorkflowPackageRow(
            package_id="pkg-ev",
            version="1.0.0",
            schema_version="1",
            compiler_version="t",
            methodology_family="t",
            recipe_fingerprint="r" * 32,
            methodology_fingerprint="m" * 32,
            environment_fingerprint="e" * 32,
            compiled_form={"typed_dag": _DAG},
            fingerprint="f" * 32,
            status="published",
            owner_scope="u:a",
            org_id="org-a",
            created_at=_dt.utcnow(),
        ))
        db.commit()
    inst = store.create_instance(
        package_id="pkg-ev", package_version="1.0.0",
        package_fingerprint="f" * 32, owner_scope="u:a", org_id="org-a",
        session_id="s1", project_id="proj-1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]],
    )
    # 数据节点绑定真实 ref（生产由 _prefill_role_bindings 写入），
    # 随 PENDING→READY 转移的 patch 写入（同态转移不落 patch）
    store.transition_node(
        inst["instance_id"], "data:subject", NodeState.READY,
        expected_from=NodeState.PENDING, reason="TEST_BIND",
        patch={"bound_ref": "ref:dataset-1"},
    )
    for nid in ("transform:buffer:subject", "output:zone"):
        store.transition_node(inst["instance_id"], nid, NodeState.READY,
                              expected_from=NodeState.PENDING, reason="T")
    # 模拟已执行完成：READY→RUNNING（认领）→SUCCEEDED（合法转移链）
    for nid in ("data:subject", "transform:buffer:subject", "output:zone"):
        store.transition_node(inst["instance_id"], nid, NodeState.RUNNING,
                              expected_from=NodeState.READY, reason="T",
                              claim=True, claimed_by="rt-x")
        store.transition_node(inst["instance_id"], nid, NodeState.SUCCEEDED,
                              expected_from=NodeState.RUNNING, reason="T",
                              claimed_by="rt-x")
    return factory, store, svc, inst


def _event(ref="ref:dataset-1"):
    return {
        "kind": "dataset.version_changed",
        "org_id": "org-a",
        "subject_type": "dataset",
        "subject_key": "dataset:one",
        "project_id": "proj-1",
        "session_id": "s1",
        "payload": {"revision": "v2", "ref": ref},
    }


class TestInvalidationEndToEnd:
    def test_descendants_marked_stale_by_ref(self, wf_env):
        factory, store, svc, inst = wf_env
        bridge = InvalidationBridge(workflow_service=svc, factory=factory)
        iid = inst["instance_id"]
        results = asyncio.run(bridge.apply_to_workflow(_event()))
        assert len(results) == 1
        r = results[0]
        assert r["ok"] is True
        assert r.get("seeded_nodes") == ["data:subject"]
        states = store.get_node_states(iid)
        # 种子及其后代全部 STALE……
        assert states["data:subject"] == NodeState.STALE
        assert states["transform:buffer:subject"] == NodeState.STALE
        assert states["output:zone"] == NodeState.STALE

    def test_unrelated_ref_no_stale(self, wf_env):
        factory, store, svc, inst = wf_env
        bridge = InvalidationBridge(workflow_service=svc, factory=factory)
        iid = inst["instance_id"]
        results = asyncio.run(
            bridge.apply_to_workflow(_event(ref="ref:other-dataset"))
        )
        assert results[0].get("skipped") == "no_matching_nodes"
        states = store.get_node_states(iid)
        assert all(s == NodeState.SUCCEEDED for s in states.values())

    def test_no_ref_honest_skip(self, wf_env):
        factory, store, svc, inst = wf_env
        bridge = InvalidationBridge(workflow_service=svc, factory=factory)
        ev = _event()
        ev["payload"] = {"revision": "v2"}
        results = asyncio.run(bridge.apply_to_workflow(ev))
        assert results[0].get("skipped") is True
        assert results[0]["reason"] == "no_ref"
        states = store.get_node_states(inst["instance_id"])
        assert all(s == NodeState.SUCCEEDED for s in states.values())

    def test_via_service_drain_full_pipeline(self, wf_env, monkeypatch):
        """经 SpatialEventService.drain 的全链路：ingest → drain → STALE。"""
        from app.services.spatial_events.governor_gate import GovernorGate
        from app.services.spatial_events.ledger import SpatialEventLedger
        from app.services.spatial_events.service import SpatialEventService

        factory, store, svc, inst = wf_env
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        monkeypatch.setenv("GIS_SPATIAL_EVENT_INVALIDATION", "1")
        monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "0")
        ledger = SpatialEventLedger(factory=factory)
        service = SpatialEventService(
            ledger, mission_runtime=None, workflow_service=svc,
            session_store=None, gate=GovernorGate(),
            invalidation_bridge=InvalidationBridge(
                workflow_service=svc, factory=factory),
        )
        env = C.SpatialEventEnvelope(
            kind="dataset.version_changed", org_id="org-a",
            subject_type="dataset", subject_key="dataset:one",
            occurred_at=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            payload={"revision": "v2", "ref": "ref:dataset-1"},
            project_id="proj-1", session_id="s1",
        )
        assert service.ingest_sync(env).status == "appended"
        summary = asyncio.run(service.drain_once("w1"))
        assert summary["processed"] == 1
        states = store.get_node_states(inst["instance_id"])
        assert states["data:subject"] == NodeState.STALE
        assert states["output:zone"] == NodeState.STALE
