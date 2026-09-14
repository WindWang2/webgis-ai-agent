"""apply_intent_facts 集成测试 —— 意图差异事实 → V5 执行侧（方向 5 E5-E6）。

真实持久层（临时 SQLite）+ 真实 CAS/machine；registry/recipe 依赖经
monkeypatch 桩化（_instance_dag 返回测试 DAG；role 映射返回测试 role 节点）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowNodeReuseRow  # noqa: F401 — 注册模型
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.service import WorkflowRuntimeService
from app.services.workflow_runtime.store import InstanceStore


_DAG: Dict[str, Any] = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False,
         "outputs": [{"name": "data", "artifact_type": ""}]},
        {"node_id": "data:boundary", "kind": "data_input", "role": "boundary",
         "optional": False,
         "outputs": [{"name": "data", "artifact_type": ""}]},
        {"node_id": "cap:aggregate", "kind": "analysis",
         "capability": "aggregate_by_district", "optional": False,
         "inputs": [{"name": "input", "artifact_type": ""}],
         "outputs": [{"name": "output", "artifact_type": ""}]},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "cap:aggregate.input"},
        {"from": "data:boundary.data", "to": "cap:aggregate.input"},
    ],
    "primary_output": "cap:aggregate",
}

_NODE_SPECS = [{"node_id": n["node_id"], "optional": False}
               for n in _DAG["nodes"]]


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _service(factory) -> WorkflowRuntimeService:
    svc = WorkflowRuntimeService(
        store=InstanceStore(factory=factory),
        reuse_index=object(),  # 本测试不触复用索引
    )

    async def _dag(inst):
        return _DAG

    async def _roles(inst, capability):
        # fetch_* 数据 capability → 其供给的 data:role 节点（recipe
        # wf_profile 的桩投影）
        return {
            "fetch_subject_data": ["data:subject"],
            "fetch_boundary_data": ["data:boundary"],
        }.get(capability, [])

    svc._instance_dag = _dag  # type: ignore[method-assign]
    svc._role_nodes_for_capability = _roles  # type: ignore[method-assign]
    return svc


def _instance(store: InstanceStore, session_id: str) -> Dict[str, Any]:
    return store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc",
        session_id=session_id, node_specs=_NODE_SPECS)


def _settle(store: InstanceStore, instance_id: str, node_id: str) -> None:
    """PENDING→READY→RUNNING→SUCCEEDED（驱动器等价的最短结算链）。"""
    store.transition_node(instance_id, node_id, C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="TEST_BOUND", event="test")
    store.transition_node(instance_id, node_id, C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="test", reason="TEST_DISPATCH",
                          event="test")
    r = store.transition_node(instance_id, node_id, C.NodeState.SUCCEEDED,
                              require_claim=True, claimed_by="test",
                              complete=True, reason="TEST_DONE",
                              event="test",
                              patch={"output_ref": f"ref:out-{node_id}"})
    assert r.ok, r.code


def test_lost_facts_mark_minimal_stale(factory):
    """lost → 节点 STALE（只污染失效 capability 的节点，最小集）。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s1")
    _settle(store, inst["instance_id"], "cap:aggregate")
    _settle(store, inst["instance_id"], "data:boundary")

    summary = asyncio.run(svc.apply_intent_facts(
        "s1", owner_scope="u:abc",
        facts={"lost": [{"capability": "aggregate_by_district",
                         "dimension": "parameter",
                         "detail": "params changed"}],
               "carried": {}}))
    assert summary is not None
    states = store.get_node_states(inst["instance_id"])
    assert states["cap:aggregate"] == C.NodeState.STALE
    # 未受影响节点零触碰
    assert states["data:boundary"] == C.NodeState.SUCCEEDED


def test_lost_data_capability_targets_role_nodes(factory):
    """数据 capability 失效 → 经 role 映射标 data:role 节点 STALE。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s2")
    _settle(store, inst["instance_id"], "data:subject")

    summary = asyncio.run(svc.apply_intent_facts(
        "s2", owner_scope="u:abc",
        facts={"lost": [{"capability": "fetch_subject_data",
                         "dimension": "data", "detail": "subject swap"}],
               "carried": {}}))
    states = store.get_node_states(inst["instance_id"])
    assert states["data:subject"] == C.NodeState.STALE
    assert "data:subject" in (summary.get("stale") or [])


def test_carried_facts_complete_bound_nodes(factory):
    """carried → 预绑 READY 节点直推 SUCCEEDED（复用，零执行）。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s3")
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": "ref:data-1"})

    summary = asyncio.run(svc.apply_intent_facts(
        "s3", owner_scope="u:abc",
        facts={"lost": [], "carried": {"fetch_subject_data": "ref:data-1"}}))
    states = store.get_node_states(inst["instance_id"])
    assert states["data:subject"] == C.NodeState.SUCCEEDED
    assert "data:subject" in (summary.get("carried") or [])


def test_carried_stale_node_stays_disclosed_not_washed(factory):
    """review P2-3：V5 闭包把 chapter 级携带节点标 STALE（边集差异）——
    旧 ref 携带直推会洗白 STALE → 现语义：保持披露（skipped_stale），
    重算交给 driver / 带新 receipt 的工具结果通道。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s4")
    _settle(store, inst["instance_id"], "data:boundary")
    store.transition_node(inst["instance_id"], "data:boundary",
                          C.NodeState.STALE,
                          expected_from=C.NodeState.SUCCEEDED,
                          reason="RECOMPUTE_SEED:data", event="apply_changes")

    summary = asyncio.run(svc.apply_intent_facts(
        "s4", owner_scope="u:abc",
        facts={"lost": [],
               "carried": {"fetch_boundary_data": "ref:bd-1"}}))
    states = store.get_node_states(inst["instance_id"])
    assert states["data:boundary"] == C.NodeState.STALE  # 不洗白
    assert "data:boundary" not in (summary.get("carried") or [])
    s4 = [i for i in summary["instances"] if i["instance_id"] ==
          inst["instance_id"]][0]
    assert s4.get("carried_skipped_stale") == ["data:boundary"]


def test_chat_complete_stale_node_with_new_receipt(factory):
    """带新 receipt（工具结果 ref）的 STALE 节点经 chat 通道重入队结算
    （CHAT_RECOMPUTE —— 与 driver 的 STALE_RECOMPUTE 同语义）。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s4b")
    _settle(store, inst["instance_id"], "data:boundary")
    store.transition_node(inst["instance_id"], "data:boundary",
                          C.NodeState.STALE,
                          expected_from=C.NodeState.SUCCEEDED,
                          reason="RECOMPUTE_SEED:data", event="apply_changes")

    outcome = asyncio.run(svc._chat_complete_node(
        inst["instance_id"], "data:boundary", "ref:bd-2", _DAG))
    assert outcome["ok"] is True
    states = store.get_node_states(inst["instance_id"])
    assert states["data:boundary"] == C.NodeState.SUCCEEDED


def test_carried_upstream_pending_is_refused_honestly(factory):
    """上游未结算 → UPSTREAM_PENDING 诚实拒绝（不虚构携带完成）。"""
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    inst = _instance(store, "s5")
    _settle(store, inst["instance_id"], "data:subject")
    # data:boundary 未结算 → cap:aggregate 上游不满足

    summary = asyncio.run(svc.apply_intent_facts(
        "s5", owner_scope="u:abc",
        facts={"lost": [],
               "carried": {"aggregate_by_district": "ref:agg-1"}}))
    states = store.get_node_states(inst["instance_id"])
    assert states["cap:aggregate"] == C.NodeState.PENDING
    assert "cap:aggregate" not in (summary.get("carried") or [])


def test_no_active_instances_is_noop(factory):
    store = InstanceStore(factory=factory)
    svc = _service(factory)
    _instance(store, "s-other")  # 不同会话
    summary = asyncio.run(svc.apply_intent_facts(
        "s6", owner_scope="u:abc",
        facts={"lost": [{"capability": "x", "dimension": "data",
                         "detail": ""}],
               "carried": {}}))
    assert summary is None


def test_empty_facts_is_noop(factory):
    svc = _service(factory)
    assert asyncio.run(svc.apply_intent_facts(
        "s7", owner_scope="u:abc", facts={"lost": [], "carried": {}})) is None
