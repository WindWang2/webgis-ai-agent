"""side_effect 执行纪律 —— at-least-once / at-most-once 裁决（方向 5 E3）。

覆盖：typed DAG 副作用派生与校验、driver STALE 重入队跳过 destructive、
destructive 节点失败不自动重试（即使错误码可重试）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowNodeReuseRow  # noqa: F401
from app.services.gis_harness.workflow_v4.typed_dag import (
    SIDE_EFFECT_CLASSES,
    TypedWorkflowNode,
    validate_typed_dag,
)
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore


# ── typed DAG 派生与校验 ─────────────────────────────────────────────────

def test_side_effect_derived_by_kind():
    node = TypedWorkflowNode(node_id="data:x", kind="data_input", role="x")
    assert node.effective_side_effect() == "derived_external"
    node = TypedWorkflowNode(node_id="cap:y", kind="analysis")
    assert node.effective_side_effect() == "pure"


def test_side_effect_explicit_destructive_and_vocabulary():
    assert set(SIDE_EFFECT_CLASSES) == {"pure", "derived_external",
                                        "destructive"}
    node = TypedWorkflowNode(node_id="output:z", kind="output",
                             side_effect="destructive")
    assert node.effective_side_effect() == "destructive"
    assert node.to_bounded_dict()["side_effect"] == "destructive"


def test_side_effect_unknown_class_is_violation():
    node = TypedWorkflowNode(node_id="cap:y", kind="analysis",
                             side_effect="magic")
    graph = type(node.__class__.__mro__[0])  # 占位，不参与断言
    from app.services.gis_harness.workflow_v4.typed_dag import TypedWorkflowGraph

    violations = validate_typed_dag(TypedWorkflowGraph(nodes=[node]))
    assert any(v.startswith("TYPED_DAG_UNKNOWN_SIDE_EFFECT") for v in violations)


# ── driver 纪律 ──────────────────────────────────────────────────────────

@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _dag_with_destructive() -> Dict[str, Any]:
    return {
        "nodes": [
            {"node_id": "data:x", "kind": "data_input", "role": "x",
             "side_effect": "derived_external", "optional": False,
             "outputs": [{"name": "data", "artifact_type": ""}]},
            {"node_id": "cap:publish", "kind": "analysis",
             "capability": "publish_product",
             "side_effect": "destructive", "optional": False,
             "inputs": [{"name": "input", "artifact_type": ""}],
             "outputs": [{"name": "output", "artifact_type": ""}]},
        ],
        "edges": [
            {"from": "data:x.data", "to": "cap:publish.input"},
        ],
        "primary_output": "cap:publish",
    }


def _env(factory, dag, exec_log: List[str]):
    store = InstanceStore(factory=factory)
    reuse = ReuseIndex(factory=factory)
    inst = store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in dag["nodes"]])

    from app.services.workflow_runtime.adapters_geocompute import (
        GeoComputeNodeOutcome,
    )

    async def plan_executor(node, input_refs, params, ctx):
        exec_log.append(node["node_id"])
        if exec_log.count(node["node_id"]) == 1 and node["node_id"] == "cap:publish":
            # 首次执行失败：NODE_TIMEOUT 属可重试错误码
            return GeoComputeNodeOutcome(ok=False, error_code="NODE_TIMEOUT",
                                         error_message="boom")
        return GeoComputeNodeOutcome(ok=True,
                                     output_ref=f"ref:out-{node['node_id']}",
                                     duration_ms=5, rows_emitted=1)

    async def probe(session_id, ref):
        return {"ref_id": ref, "feature_count": 3, "content_hash": "h" * 32,
                "content_revision": 1, "geometry_types": ["Point"]}

    driver = Driver(store, reuse_index=reuse, owner_scope="u:abc",
                    deadline_s=5.0, plan_executor=plan_executor,
                    descriptor_probe=probe)
    store.transition_node(inst["instance_id"], "data:x", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": "ref:data-1"})
    return store, driver, inst


def test_destructive_failure_is_not_auto_retried(factory):
    """destructive 节点可重试错误也不自动重试（at-most-once）。"""
    exec_log: List[str] = []
    dag = _dag_with_destructive()
    store, driver, inst = _env(factory, dag, exec_log)
    summary = asyncio.run(driver.run(
        inst["instance_id"], dag, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    states = store.get_node_states(inst["instance_id"])
    assert states["cap:publish"] == C.NodeState.FAILED
    assert exec_log.count("cap:publish") == 1  # 零自动重试
    # 显式指令通道（retry_failed_nodes）后可重驱 —— 本测试只钉住"不自动"


def test_destructive_stale_not_requeued_by_driver(factory):
    """destructive 节点 STALE 只作披露 —— driver 不自动重算。"""
    dag = _dag_with_destructive()
    exec_log: List[str] = []
    store = InstanceStore(factory=factory)
    reuse = ReuseIndex(factory=factory)
    inst = store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in dag["nodes"]])

    from app.services.workflow_runtime.adapters_geocompute import (
        GeoComputeNodeOutcome,
    )

    async def probe(session_id, ref):
        return {"ref_id": ref, "feature_count": 3, "content_hash": "h" * 32,
                "content_revision": 1, "geometry_types": ["Point"]}

    async def plan_executor(node, input_refs, params, ctx):
        exec_log.append(node["node_id"])
        return GeoComputeNodeOutcome(ok=True,
                                     output_ref=f"ref:out-{node['node_id']}",
                                     duration_ms=5, rows_emitted=1)

    driver = Driver(store, reuse_index=reuse, owner_scope="u:abc",
                    deadline_s=3.0, plan_executor=plan_executor,
                    descriptor_probe=probe)
    store.transition_node(inst["instance_id"], "data:x", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": "ref:data-1"})
    # 第一轮：全链正常结算（cap:publish 执行一次）
    asyncio.run(driver.run(
        inst["instance_id"], dag, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    assert store.get_node_states(
        inst["instance_id"])["cap:publish"] == C.NodeState.SUCCEEDED
    calls_after_first = list(exec_log)

    # 变更标 STALE（ChangeApplier 同语义）→ 再 run：destructive 不重算
    r = store.transition_node(inst["instance_id"], "cap:publish",
                              C.NodeState.STALE, reason="RECOMPUTE_SEED:data")
    assert r.ok, r.code
    asyncio.run(driver.run(
        inst["instance_id"], dag, node_params={}, session_id="s1",
        run_token="rt-2", package_fingerprint="pf" * 16))
    states = store.get_node_states(inst["instance_id"])
    assert states["cap:publish"] == C.NodeState.STALE  # 保持披露
    assert exec_log == calls_after_first  # 零新增执行
