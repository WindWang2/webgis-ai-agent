"""Workflow Runtime V5 —— driver/recompute/service 集成测试（Wave 7-10）。

真实持久层（临时 SQLite）+ 真实复用索引 + fake 节点执行器（确定性，
无 GeoCompute I/O）。GeoCompute 真执行链路另见 test_e2e_realform.py。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowNodeReuseRow  # noqa: F401 — 注册模型
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import fingerprints as F
from app.services.workflow_runtime import recompute as RC
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


#: 三节点测试 DAG（compiled typed_dag 形态，边带端口后缀）。
_DAG: Dict[str, Any] = {
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


@pytest.fixture
def env(factory):
    store = InstanceStore(factory=factory)
    reuse = ReuseIndex(factory=factory)
    exec_counter: List[str] = []
    inst = store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]])

    from app.services.workflow_runtime.driver import Driver

    async def plan_executor(node, input_refs, params, ctx):
        exec_counter.append(node["node_id"])
        from app.services.workflow_runtime.adapters_geocompute import (
            GeoComputeNodeOutcome,
        )

        return GeoComputeNodeOutcome(
            ok=True, output_ref=f"ref:out-{node['node_id']}",
            duration_ms=5, rows_emitted=3)

    descriptors = {
        "ref:data-1": {"ref_id": "ref:data-1", "feature_count": 3,
                       "content_hash": "d" * 32, "content_revision": 1,
                       "geometry_types": ["Point"]},
    }

    async def probe(session_id, ref):
        return descriptors.get(ref, {
            "ref_id": ref, "feature_count": 3, "content_hash": "h" * 32,
            "content_revision": 1, "geometry_types": ["Point"]})

    driver = Driver(store, reuse_index=reuse, owner_scope="u:abc",
                    deadline_s=5.0, plan_executor=plan_executor,
                    descriptor_probe=probe)
    # 数据节点预绑（role binding 语义）
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": "ref:data-1"})
    return {"store": store, "reuse": reuse, "exec": exec_counter,
            "inst": inst, "driver": driver, "probe": probe,
            "descriptors": descriptors}
    return {"store": store, "reuse": reuse, "exec": exec_counter,
            "inst": inst, "driver": driver, "probe": probe}


# ── 全链执行 ─────────────────────────────────────────────────────────────

def test_full_run_executes_ready_chain(env):
    """PENDING 数据节点 → transform → output：真执行一次全链。"""
    store, driver, inst = env["store"], env["driver"], env["inst"]
    summary = asyncio.run(driver.run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    states = store.get_node_states(inst["instance_id"])
    assert all(s == C.NodeState.SUCCEEDED for s in states.values())
    # 数据节点零执行（绑定态）、transform + output 执行数 ≤2
    assert "data:subject" not in env["exec"]
    out_ref = store.get_node(inst["instance_id"], "output:zone")["output_ref"]
    assert out_ref.startswith("ref:out-")


def test_double_execution_produces_reuse_hit(env):
    """同输入第二次 run：先重置为重算态 → 复用命中，零真执行（Wave 8）。"""
    store, reuse, driver, inst = env["store"], env["reuse"], env["driver"], env["inst"]
    asyncio.run(driver.run(inst["instance_id"], _DAG, node_params={},
                           session_id="s1", run_token="rt-1",
                           package_fingerprint="pf" * 16))
    calls_after_first = list(env["exec"])
    assert calls_after_first  # 首轮真执行
    # SUCCEEDED → STALE（模拟上游数据变化标记）→ 再 run：STALE 复用解除
    for nid in ("transform:buffer:subject", "output:zone"):
        store.transition_node(inst["instance_id"], nid, C.NodeState.STALE,
                              reason="TEST")
    summary = asyncio.run(driver.run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-2", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    states = store.get_node_states(inst["instance_id"])
    assert all(s == C.NodeState.SUCCEEDED for s in states.values())
    # 复用命中：无新增真执行
    assert env["exec"] == calls_after_first
    node = store.get_node(inst["instance_id"], "transform:buffer:subject")
    assert node["reuse"]["reused"] is True
    assert node["reuse"]["fingerprint_level"] == "content"


def test_shape_level_inputs_record_but_never_reuse(env):
    """输入降级为 shape 级指纹 → 复用必 miss（假命中红线 [R1-M3]）。"""
    store, driver, inst = env["store"], env["driver"], env["inst"]
    asyncio.run(driver.run(inst["instance_id"], _DAG, node_params={},
                           session_id="s1", run_token="rt-1",
                           package_fingerprint="pf" * 16))
    calls_first = list(env["exec"])
    assert calls_first
    # 输入 descriptor 降级：无 content hash、revision 变化
    env["descriptors"]["ref:data-1"] = {
        "ref_id": "ref:data-1", "feature_count": 3,
        "content_revision": 9, "geometry_types": ["Point"]}
    for nid in ("transform:buffer:subject", "output:zone"):
        store.transition_node(inst["instance_id"], nid, C.NodeState.STALE,
                              reason="TEST")
    asyncio.run(driver.run(inst["instance_id"], _DAG, node_params={},
                           session_id="s1", run_token="rt-2",
                           package_fingerprint="pf" * 16))
    # 复用必 miss → 真执行发生
    assert len(env["exec"]) > len(calls_first)
    node = store.get_node(inst["instance_id"], "transform:buffer:subject")
    assert not (node["reuse"] or {}).get("reused")


# ── 增量重算闭环 + 差分 oracle ───────────────────────────────────────────

def test_apply_changes_param_marks_exact_subtree_stale(env):
    """参数变化 → 精确子树 STALE；差分 oracle == V4 纯函数 plan。"""
    store, inst = env["store"], env["inst"]
    asyncio.run(env["driver"].run(inst["instance_id"], _DAG, node_params={},
                                  session_id="s1", run_token="rt-1",
                                  package_fingerprint="pf" * 16))
    applier = RC.ChangeApplier(store, owner_scope="u:abc")
    # 参数 owner = transform 节点（parameters 词表注入）
    dag = dict(_DAG)
    dag["nodes"] = [dict(n) for n in _DAG["nodes"]]
    dag["nodes"][1] = {**dag["nodes"][1], "parameters": [{"name": "distance"}]}
    result = asyncio.run(applier.apply(
        inst["instance_id"], dag,
        [C.PendingChange(dimension="parameter", target_kind="parameter",
                         target="distance")], seq=1))
    assert result["applied"]
    states = store.get_node_states(inst["instance_id"])
    plan = RC.plan_for_changes(dag, [C.PendingChange(
        dimension="parameter", target_kind="parameter", target="distance")])
    # 差分 oracle：STALE == plan.recompute ∩ {SUCCEEDED|READY|STALE}
    expected = {n for n in plan.recompute
                if n in ("transform:buffer:subject", "output:zone",
                         "data:subject")}
    got = {n for n, s in states.items() if s == C.NodeState.STALE}
    assert got == expected, (got, expected, plan.recompute)


def test_apply_changes_style_only_zero_science_touch(env):
    """style-only：科学节点零触碰；决策 style_only=True（Epic §16）。"""
    store, inst = env["store"], env["inst"]
    asyncio.run(env["driver"].run(inst["instance_id"], _DAG, node_params={},
                                  session_id="s1", run_token="rt-1",
                                  package_fingerprint="pf" * 16))
    before = store.get_node_states(inst["instance_id"])
    applier = RC.ChangeApplier(store, owner_scope="u:abc")
    result = asyncio.run(applier.apply(
        inst["instance_id"], _DAG,
        [C.PendingChange(dimension="style", target_kind="style",
                         target="output:zone")], seq=1))
    assert result["applied"] and result["decision"]["style_only"] is True
    after = store.get_node_states(inst["instance_id"])
    for nid, st in before.items():
        assert after[nid] == st, f"style 变更不得改变 {nid} 状态"


def test_running_instance_defers_changes(env):
    """RUNNING 在飞 → deferred 入 pending（quiescence 门 [R1-C1]）。"""
    store, inst = env["store"], env["inst"]
    # 制造 RUNNING 在飞态
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.RUNNING, expected_from=C.NodeState.READY,
                          claim=True, claimed_by="rt-x")
    applier = RC.ChangeApplier(store, owner_scope="u:abc")
    result = asyncio.run(applier.apply(
        inst["instance_id"], _DAG,
        [C.PendingChange(dimension="data", target_kind="data_role",
                         target="subject")], seq=1))
    assert result["deferred"] is True
    inst2 = store.get_instance(inst["instance_id"])
    assert len(inst2["pending_changes"]) == 1


def test_drain_pending_applies_after_quiescence(env):
    store, inst = env["store"], env["inst"]
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.RUNNING, expected_from=C.NodeState.READY,
                          claim=True, claimed_by="rt-x")
    applier = RC.ChangeApplier(store, owner_scope="u:abc")
    asyncio.run(applier.apply(
        inst["instance_id"], _DAG,
        [C.PendingChange(dimension="data", target_kind="data_role",
                         target="subject")], seq=1))
    # 完成在飞节点 → quiescence → drain 生效
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.SUCCEEDED, require_claim=True,
                          claimed_by="rt-x", complete=True,
                          patch={"output_ref": "ref:data-1"})
    decision = asyncio.run(applier.drain_pending(
        inst["instance_id"], _DAG))
    assert decision is not None and decision["applied"] is True
    states = store.get_node_states(inst["instance_id"])
    assert states["data:subject"] == C.NodeState.STALE


def test_cancel_propagates_to_descendants(env):
    """取消：非终态节点沿转移表 → CANCELLED；下游不执行（Epic §16）。"""
    store, inst = env["store"], env["inst"]
    store.update_instance(inst["instance_id"], owner_scope="u:abc",
                          fields={"cancel_requested": True})
    summary = asyncio.run(env["driver"].run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.CANCELLED
    assert env["exec"] == []  # 取消后零执行
    states = store.get_node_states(inst["instance_id"])
    assert all(s == C.NodeState.CANCELLED for s in states.values())


def test_recovery_resets_orphans_and_completes(env):
    """崩溃恢复：孤儿 RUNNING（无租约）复位 READY 后可继续执行。"""
    store, inst = env["store"], env["inst"]
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    store.transition_node(inst["instance_id"], "data:subject",
                          C.NodeState.RUNNING, expected_from=C.NodeState.READY,
                          claim=True, claimed_by="rt-dead")
    assert store.find_orphan_running_nodes(inst["instance_id"]) == \
        ["data:subject"]
    summary = asyncio.run(env["driver"].run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-live", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.SUCCEEDED


# ── projection / explain ─────────────────────────────────────────────────

def test_projection_and_explain(env):
    store, inst = env["store"], env["inst"]
    asyncio.run(env["driver"].run(inst["instance_id"], _DAG, node_params={},
                                  session_id="s1", run_token="rt-1",
                                  package_fingerprint="pf" * 16))
    from app.services.workflow_runtime.projection import explain, instance_projection

    nodes = store.get_nodes(inst["instance_id"])
    inst2 = store.get_instance(inst["instance_id"])
    proj = instance_projection(inst2, nodes)
    assert proj["status"] == "succeeded"
    assert proj["counts"].get("SUCCEEDED") == 3
    exp = explain(nodes, inst2.get("decisions") or [])
    assert isinstance(exp["why_recomputed"], list)
    assert isinstance(exp["blocked"], list)


# ── fingerprints 参数 canonical 化（Epic §16）───────────────────────────

def test_parameter_canonicalization_fingerprint_stability():
    fp1 = F.node_reuse_fingerprint(
        package_fingerprint="p", node_id="n", algorithm_id="a",
        params={"distance": 100, "unit": "m"}, inputs={}, env_fp="e")
    fp2 = F.node_reuse_fingerprint(
        package_fingerprint="p", node_id="n", algorithm_id="a",
        params={"unit": "m", "distance": 100.0}, inputs={}, env_fp="e")
    assert fp1 == fp2  # 键序 + int/float 归一


# ── service 门面（compile/register/instantiate，临时库）─────────────────

def test_service_compile_register_instantiate(factory, monkeypatch):
    """compile→register→instantiate 全链 + 同 plan 重建 supersede [R1-M6]。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    svc = SV.WorkflowRuntimeService(
        store=InstanceStore(factory=fac),
        registry=SV.PackageRegistry(factory=fac),
        reuse_index=ReuseIndex(factory=fac))
    reg = svc.compile_and_register(
        "对工厂污染源周边做缓冲区分析并评估影响范围", owner_scope="u:abc")
    pkg = reg["package"]
    assert pkg["status"] == "draft" and pkg["fingerprint"]
    inst = svc.instantiate(pkg["package_id"], owner_scope="u:abc",
                           session_id="s1")
    assert inst["instance_id"].startswith("wi-")
    # 同 plan 同包重建 → 旧实例 superseded
    inst2 = svc.instantiate(pkg["package_id"], owner_scope="u:abc",
                            session_id="s1")
    old = svc.store.get_instance(inst["instance_id"], "u:abc")
    assert old["status"] == C.InstanceStatus.SUPERSEDED
    # 重复注册同指纹幂等；不同指纹冲突
    svc.compile_and_register(
        "对工厂污染源周边做缓冲区分析并评估影响范围", owner_scope="u:abc")
    from app.services.workflow_runtime.registry import PackageConflict

    with pytest.raises(PackageConflict):
        svc.registry.register(
            type("P", (), {
                "package_id": pkg["package_id"], "version": pkg["version"],
                "schema_version": "1.0.0", "compiler_version": "4.0.0",
                "methodology_family": "x", "recipe_fingerprint": "r",
                "methodology_fingerprint": "m",
                "fingerprint": "different" * 8,
                "to_bounded_dict": lambda self: {"compiled_form": {}},
            })(), owner_scope="u:abc")


def test_instance_status_failed_on_blockade(factory):
    """非 optional BLOCKED → failed（诚实终态，不悬挂 [R1-MINOR-3]）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    store = InstanceStore(factory=fac)
    inst = store.create_instance(
        package_id="p", package_version="1.0.0", package_fingerprint="f",
        owner_scope="u:abc",
        node_specs=[{"node_id": "cap:x", "optional": False}])
    store.transition_node(inst["instance_id"], "cap:x", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(inst["instance_id"], "cap:x", C.NodeState.BLOCKED,
                          expected_from=C.NodeState.READY,
                          reason="NODE_NOT_EXECUTABLE")
    states = store.get_node_states(inst["instance_id"])
    assert C.instance_status_from_nodes(states, optional_nodes={
        "cap:x": False}) == C.InstanceStatus.FAILED
