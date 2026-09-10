"""Workflow Runtime V5 —— 子工作流运行时测试（Wave 13）。

嵌套展开（真 child instance + 父子指针）、深度/环守卫、每 owner 上限、
义务继承链 provenance、取消传播、子终态 → 父节点状态映射。
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime import subworkflow as SW
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.registry import PackageRegistry
from app.services.workflow_runtime.store import InstanceStore


def _pkg_fp(compiled: dict) -> str:
    return hashlib.sha256(
        _json.dumps(compiled, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")).hexdigest()[:64]


def _dag(child_pkg: str = "") -> dict:
    nodes = [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False, "outputs": [{"name": "data"}]},
    ]
    edges = []
    if child_pkg:
        nodes.append({"node_id": "sub:child", "kind": "subworkflow",
                      "optional": False, "subworkflow_package_id": child_pkg,
                      "inputs": [{"name": "input"}]})
        edges.append({"from": "data:subject.data", "to": "sub:child.input"})
    nodes.append({"node_id": "output:final", "kind": "output",
                  "optional": False, "inputs": [{"name": "product"}]})
    edges.append({"from": ("sub:child.output" if child_pkg
                           else "data:subject.data"),
                  "to": "output:final.product"})
    return {"nodes": nodes, "edges": edges, "primary_output": "output:final"}


class Env:
    def __init__(self, fac):
        self.svc = SV.WorkflowRuntimeService(
            store=InstanceStore(factory=fac),
            registry=PackageRegistry(factory=fac),
            reuse_index=ReuseIndex(factory=fac))
        self.fac = fac

    def register(self, package_id: str, dag: dict) -> str:
        from app.services.gis_harness.workflow_v4.package import (
            WorkflowPackage,
        )

        compiled = {"typed_dag": dag, "parameters": []}
        pkg = WorkflowPackage(
            package_id=package_id, version="1.0.0",
            methodology_family="proximity", recipe_fingerprint="t",
            methodology_fingerprint="t", compiled_form=compiled)
        pkg.fingerprint = _pkg_fp(compiled)
        self.svc.registry.register(pkg, owner_scope=_OWNER, project_id="")
        return pkg.fingerprint


_OWNER = "u:sw"


async def _store_points(session: str) -> str:
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"i": i},
         "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}}
        for i in range(2)]}
    return await session_data_manager.store(session, fc, prefix="sw-test")


async def _bind_data(svc, iid: str, session: str) -> str:
    ref = await _store_points(session)
    svc.store.transition_node(
        iid, "data:subject", C.NodeState.READY,
        expected_from=C.NodeState.PENDING, reason="ROLE_BOUND",
        event="attach", patch={"bound_ref": ref})
    return ref


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Env(sessionmaker(bind=engine))


def test_self_reference_blocks_as_cycle(env):
    """自引用（chain→chain）：环守卫优先拒绝（不递归爆炸）。"""
    e = env
    e.register("chain_3", _dag("chain_3"))
    inst = e.svc.instantiate("chain_3", owner_scope=_OWNER, session_id="s1")
    executor = SW.SubworkflowExecutor(e.svc, owner_scope=_OWNER)
    node = {"node_id": "sub:child", "subworkflow_package_id": "chain_3"}
    result = asyncio.run(executor(
        node, parent={"instance_id": inst["instance_id"],
                      "package_id": "chain_3"},
        parent_visited=[], session_id="s1"))
    assert not result["ok"]
    assert result["error_code"] == "SUBWORKFLOW_CYCLE"


def test_depth_guard_blocks_distinct_chain_at_limit(env):
    """互异包链 a1→a2→a3→a4：第 4 层展开越界 → SUBWORKFLOW_DEPTH。"""
    e = env
    e.register("a1", _dag("a2"))
    e.register("a2", _dag("a3"))
    e.register("a3", _dag("a4"))
    executor = SW.SubworkflowExecutor(e.svc, owner_scope=_OWNER)
    # 模拟已在 a3 层（visited=[a1,a2,a3]，depth=3）再展开 a4
    result = asyncio.run(executor(
        {"node_id": "sub:child", "subworkflow_package_id": "a4"},
        parent={"instance_id": "wi-x", "package_id": "a3"},
        parent_visited=["a1", "a2"], session_id="s1"))
    assert not result["ok"]
    assert result["error_code"] == "SUBWORKFLOW_DEPTH"


def test_cycle_guard_blocks_package_loop(env):
    """包 A→B→A 环：visited 命中 → SUBWORKFLOW_CYCLE。"""
    e = env
    e.register("pkg_a", _dag("pkg_b"))
    e.register("pkg_b", _dag("pkg_a"))
    inst = e.svc.instantiate("pkg_a", owner_scope=_OWNER, session_id="s1")
    executor = SW.SubworkflowExecutor(e.svc, owner_scope=_OWNER)
    node = {"node_id": "sub:child", "subworkflow_package_id": "pkg_b"}
    result = asyncio.run(executor(
        node, parent={"instance_id": inst["instance_id"],
                      "package_id": "pkg_a"},
        parent_visited=[], session_id="s1"))
    assert not result["ok"]
    # A→B→A：环在第二层展开时被守卫捕获 → 子实例失败；父层映射失败
    # （错误码向上传播，见 executor 的 SUBWORKFLOW_* 透传）
    assert result["error_code"].startswith("SUBWORKFLOW_")


def test_cap_guard_blocks_over_limit(env, monkeypatch):
    """每 owner 活跃子实例 ≥32 → SUBWORKFLOW_CAP。"""
    e = env
    e.register("pkg_cap_parent", _dag("pkg_cap"))
    e.register("pkg_cap", _dag())
    monkeypatch.setattr(
        e.svc.store, "count_active_subworkflows", lambda owner: 32)
    inst = e.svc.instantiate("pkg_cap_parent", owner_scope=_OWNER,
                             session_id="s1")
    executor = SW.SubworkflowExecutor(e.svc, owner_scope=_OWNER)
    node = {"node_id": "sub:child", "subworkflow_package_id": "pkg_cap"}
    result = asyncio.run(executor(
        node, parent={"instance_id": inst["instance_id"],
                      "package_id": "pkg_cap_parent"},
        parent_visited=[], session_id="s1"))
    assert not result["ok"]
    assert result["error_code"] == "SUBWORKFLOW_CAP"


def test_expansion_creates_child_with_parent_pointers(env):
    """合法展开：子实例带 parent 指针 + visited 链 + 义务链摘要。"""
    e = env
    e.register("parent_pkg", _dag("child_pkg"))
    e.register("child_pkg", _dag())
    inst = e.svc.instantiate("parent_pkg", owner_scope=_OWNER, session_id="s1")
    data_ref = asyncio.run(_bind_data(e.svc, inst["instance_id"], "s1"))
    executor = SW.SubworkflowExecutor(e.svc, owner_scope=_OWNER)
    node = {"node_id": "sub:child", "subworkflow_package_id": "child_pkg"}
    result = asyncio.run(executor(
        node, parent={"instance_id": inst["instance_id"],
                      "package_id": "parent_pkg"},
        parent_visited=[], session_id="s1", input_refs=[data_ref]))
    assert result["ok"], result
    child = e.svc.store.get_instance(result["child_instance_id"], _OWNER)
    assert child["parent_instance_id"] == inst["instance_id"]
    assert child["parent_node_id"] == "sub:child"
    assert "child_pkg" in child["visited_packages"]
    assert result["obligation_chain"]["obligation_chain_fp"]
    # 父索引可见子实例（含终态——parent 指针即证据）
    subs = e.svc.store.list_owner_instances(_OWNER)
    assert any(r["parent_instance_id"] == inst["instance_id"] for r in subs)


def test_parent_run_drives_subworkflow_to_terminal(env):
    """父 run：subworkflow 节点 → 子实例展开执行 → 父节点 SUCCEEDED。"""
    e = env
    e.register("parent2", _dag("child2"))
    e.register("child2", _dag())
    inst = e.svc.instantiate("parent2", owner_scope=_OWNER, session_id="s2")
    asyncio.run(_bind_data(e.svc, inst["instance_id"], "s2"))
    result = asyncio.run(e.svc.run_instance(
        inst["instance_id"], owner_scope=_OWNER, deadline_s=30.0))
    assert result["run"]["status"] == "succeeded"
    states = e.svc.store.get_node_states(inst["instance_id"])
    assert states["sub:child"] == C.NodeState.SUCCEEDED
    node = e.svc.store.get_node(inst["instance_id"], "sub:child")
    assert node["output_ref"].startswith("wi:")
    chain = (node["binding"] or {}).get("obligation_chain") or {}
    assert chain.get("obligation_chain_fp")
    # 深度链：parent2 → child2（visited 含两包）
    rows = [r for r in e.svc.store.list_owner_instances(_OWNER)
            if r["parent_instance_id"]]
    assert len(rows) == 1


def test_depth_2_nesting_succeeds_depth_3_fails(env):
    """嵌套 2 层成功；3 层深度守卫拒绝（父节点 FAILED 证据）。"""
    e = env
    e.register("lvl1", _dag("lvl2"))
    e.register("lvl2", _dag("lvl3"))
    e.register("lvl3", _dag("lvl4"))
    e.register("lvl4", _dag())
    inst = e.svc.instantiate("lvl1", owner_scope=_OWNER, session_id="s3")
    asyncio.run(_bind_data(e.svc, inst["instance_id"], "s3"))
    result = asyncio.run(e.svc.run_instance(
        inst["instance_id"], owner_scope=_OWNER, deadline_s=30.0))
    # lvl4 的展开将超出深度 → lvl3 的 sub:child FAILED → 逐层向上 FAILED
    assert result["run"]["status"] == "failed"
    states = e.svc.store.get_node_states(inst["instance_id"])
    assert states["sub:child"] == C.NodeState.FAILED
    node = e.svc.store.get_node(inst["instance_id"], "sub:child")
    assert (node["binding"] or {}).get("detail") or node["error_code"]


def test_obligation_merge_uses_v4_inheritance(env):
    """义务合并走 V4 inherit_obligations（无 profile → 空义务诚实保留）。"""
    chain = SW.merged_obligation_fingerprint("parent_x", "child_x", depth=1)
    assert chain["sources"] == 2
    assert chain["obligations"] == 0  # 无 recipe profile → 空义务（诚实）
    assert chain["obligation_chain_fp"]
