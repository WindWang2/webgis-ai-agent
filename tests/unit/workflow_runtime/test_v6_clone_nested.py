"""Workflow V6 —— Phase E/F：clone run / 失败重排 / 嵌套传播测试。

Phase E 验收面：partial rerun（clone + 复用索引跳过未变节点）、分支
工作流（only_nodes）、失败节点预算内重排。
Phase F 验收面：父取消传播到子实例（watcher）、deadline 继承（嵌套链
不突破父时限）。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.service import (
    WorkflowRuntimeError,
    WorkflowRuntimeService,
)
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def factory():
    """临时文件 SQLite（并发节点执行需要多连接；StaticPool 单连接会在
    asyncio.to_thread 并发下触发 sqlite InterfaceError 互相踩踏）。"""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_DAG: Dict[str, Any] = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False},
        {"node_id": "output:zone", "kind": "output", "optional": False},
        {"node_id": "output:alt", "kind": "output", "optional": True},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "output:zone.product"},
        {"from": "transform:buffer:subject.output",
         "to": "output:alt.product"},
    ],
    "primary_output": "output:zone",
}


class Rig:
    """service + driver + 计数执行器测试台。"""

    def __init__(self, factory, *, deadline_s: float = 10.0):
        from types import SimpleNamespace

        from app.services.workflow_runtime.registry import PackageRegistry

        self.factory = factory
        self.store = InstanceStore(factory=factory)
        self.reuse = ReuseIndex(factory=factory)
        self.registry = PackageRegistry(factory=factory)
        self.registry.register(SimpleNamespace(
            package_id="recipe-x", version="1.0.0", schema_version="1",
            compiler_version="4.0.0", methodology_family="m",
            recipe_fingerprint="", methodology_fingerprint="",
            fingerprint="pf" * 16,
            to_bounded_dict=lambda: {"compiled_form": {"typed_dag": _DAG}}),
            owner_scope="u:abc")
        self.svc = WorkflowRuntimeService(store=self.store,
                                          registry=self.registry,
                                          reuse_index=self.reuse)
        self.execs: List[str] = []
        self.deadline_s = deadline_s

    def make_instance(self):
        return self.store.create_instance(
            package_id="recipe-x", package_version="1.0.0",
            package_fingerprint="pf" * 16, owner_scope="u:abc",
            session_id="s1",
            node_specs=[{"node_id": n["node_id"],
                         "optional": bool(n.get("optional", False))}
                        for n in _DAG["nodes"]])

    def bind(self, iid: str):
        self.store.transition_node(iid, "data:subject", C.NodeState.READY,
                                   expected_from=C.NodeState.PENDING,
                                   reason="ROLE_BOUND", event="attach",
                                   patch={"bound_ref": "ref:data-1"})

    def driver(self, *, deadline_s=None):
        async def executor(node, input_refs, params, ctx):
            self.execs.append(node["node_id"])
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(
                ok=True, output_ref=f"ref:out-{node['node_id']}",
                duration_ms=1)

        async def probe(session_id, ref):
            return {"ref_id": ref, "feature_count": 3,
                    "content_hash": "h" * 32, "content_revision": 1,
                    "geometry_types": ["Point"]}

        return Driver(self.store, reuse_index=self.reuse,
                      owner_scope="u:abc",
                      deadline_s=self.deadline_s if deadline_s is None
                      else deadline_s,
                      plan_executor=executor, descriptor_probe=probe)


def _run(rig, iid, **kw):
    return asyncio.run(rig.driver(**kw).run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt-1",
        package_fingerprint="pf" * 16))


# ── Phase E：clone run ───────────────────────────────────────────────────

def test_clone_run_skips_unchanged_nodes_via_reuse(factory):
    """源实例成功 → 克隆重跑：复用命中零重算（skip unchanged 验收）。"""
    rig = Rig(factory)
    src = rig.make_instance()
    rig.bind(src["instance_id"])
    summary = _run(rig, src["instance_id"])
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    assert len(rig.execs) == 1  # buffer 真算一次 + 记录复用

    result = asyncio.run(rig.svc.clone_run(
        src["instance_id"], owner_scope="u:abc"))
    clone = result["instance"]
    assert clone["instance_id"] != src["instance_id"]
    assert clone["package_fingerprint"] == src["package_fingerprint"]
    # 克隆事件溯源
    clone_events = rig.store.get_events(clone["instance_id"])
    assert any(e["kind"] == C.EventKind.CLONE for e in clone_events)
    # data 绑定继承
    row = rig.store.get_node(clone["instance_id"], "data:subject")
    assert row["bound_ref"] == "ref:data-1"
    # 克隆重跑：buffer 复用命中（不重算），数据节点绑定透传
    rig.execs.clear()
    summary2 = _run(rig, clone["instance_id"])
    assert summary2["status"] == C.InstanceStatus.SUCCEEDED
    assert rig.execs == []  # 复用命中 → 零重算
    buf = rig.store.get_node(clone["instance_id"], "transform:buffer:subject")
    assert buf["state"] == C.NodeState.SUCCEEDED
    assert (buf.get("reuse") or {}).get("reused") is True


def test_clone_run_branch_workflow_with_only_nodes(factory):
    """only_nodes：keep-set（祖先∪白名单∪后代）外的分支转 SKIPPED。"""


    rig = Rig(factory)
    src = rig.make_instance()
    rig.bind(src["instance_id"])
    # 白名单 output:zone → keep = {data, buffer, output:zone}；分支
    # output:alt 不在 keep-set → SKIPPED
    result = asyncio.run(rig.svc.clone_run(
        src["instance_id"], owner_scope="u:abc",
        only_nodes=["output:zone"], skip_nodes=[]))
    clone = result["instance"]
    states = rig.store.get_node_states(clone["instance_id"])
    assert states["output:alt"] == C.NodeState.SKIPPED
    # data 节点被 CLONE_BOUND 继承为 READY（白名单 keep-set 的必需输入）
    assert states["data:subject"] == C.NodeState.READY
    assert states["output:zone"] == C.NodeState.PENDING
    # 未知节点 → typed 错误
    with pytest.raises(WorkflowRuntimeError):
        asyncio.run(rig.svc.clone_run(
            src["instance_id"], owner_scope="u:abc",
            skip_nodes=["nope:node"]))


def test_retry_failed_nodes_within_budget(factory):
    """FAILED→READY 重排（预算内）→ 重驱完成；耗尽的诚实拒绝。"""
    rig = Rig(factory)
    inst = rig.make_instance()
    rig.bind(inst["instance_id"])
    iid = inst["instance_id"]
    # 手工制造 FAILED 节点（attempts=1 < 预算 2）
    store = rig.store
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-x")
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.FAILED, require_claim=True,
                          claimed_by="rt-x", complete=True,
                          reason="EXEC_FAIL:NODE_TIMEOUT",
                          patch={"attempts_increment": True})
    store.update_instance(iid, fields={"status": C.InstanceStatus.FAILED,
                                       "terminal_at": _dt.datetime.utcnow()})
    result = asyncio.run(rig.svc.retry_failed_nodes(iid,
                                                    owner_scope="u:abc"))
    assert result["requeued"] == ["transform:buffer:subject"]
    assert result["exhausted"] == []
    assert store.get_instance(iid)["status"] == C.InstanceStatus.RUNNING
    # 重驱完成
    summary = _run(rig, iid)
    assert summary["status"] == C.InstanceStatus.SUCCEEDED


def test_retry_failed_nodes_exhausted_stays_failed(factory):
    rig = Rig(factory)
    inst = rig.make_instance()
    iid = inst["instance_id"]
    store = rig.store
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-x")
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.FAILED, require_claim=True,
                          claimed_by="rt-x", complete=True,
                          reason="EXEC_FAIL:NODE_TIMEOUT",
                          patch={"attempts_increment": True,
                                 "attempt_log": {"attempt": 1},
                                 })
    # 第二次失败（attempts=2 = 默认预算上限）
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.READY, expected_from=C.NodeState.FAILED,
                          reason="RETRY_SCHEDULED")
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-y")
    store.transition_node(iid, "transform:buffer:subject",
                          C.NodeState.FAILED, require_claim=True,
                          claimed_by="rt-y", complete=True,
                          reason="EXEC_FAIL:NODE_TIMEOUT",
                          patch={"attempts_increment": True})
    result = asyncio.run(rig.svc.retry_failed_nodes(iid,
                                                    owner_scope="u:abc"))
    assert result["requeued"] == []
    assert result["exhausted"] == ["transform:buffer:subject"]
    assert store.get_node(iid, "transform:buffer:subject")["state"] == \
        C.NodeState.FAILED
    # 无 FAILED 对象的实例 → typed NOTHING_TO_RETRY
    fresh = rig.make_instance()
    with pytest.raises(WorkflowRuntimeError):
        asyncio.run(rig.svc.retry_failed_nodes(fresh["instance_id"],
                                               owner_scope="u:abc"))


# ── Phase F：嵌套传播 ────────────────────────────────────────────────────

def test_parent_cancel_propagates_to_running_child(factory, monkeypatch):
    """父取消旗标（子 run 在飞中置位）→ watcher → 子实例持久取消。

    确定性时序：慢执行器等 release 事件 —— 子节点 RUNNING 由测试观察确认
    后置父取消旗标，等 watcher 周期（0.5s）落地再放行执行。不依赖
    wall-clock 竞速（全量套件重载下依旧稳定）。
    """
    from app.services.workflow_runtime.subworkflow import SubworkflowExecutor

    rig = Rig(factory)
    svc = rig.svc
    parent = rig.make_instance()
    rig.bind(parent["instance_id"])
    store = svc.store
    release = asyncio.Event()

    async def gated_executor(node, input_refs, params, ctx):
        await release.wait()  # 在飞窗口由测试控制
        from app.services.workflow_runtime.adapters_geocompute import (
            GeoComputeNodeOutcome,
        )

        return GeoComputeNodeOutcome(ok=True,
                                     output_ref=f"ref:out-{node['node_id']}",
                                     duration_ms=1)

    async def probe(session_id, ref):
        return {"ref_id": ref, "feature_count": 3, "content_hash": "h" * 32,
                "content_revision": 1, "geometry_types": ["Point"]}

    executor = SubworkflowExecutor(svc, owner_scope="u:abc",
                                   deadline_s=15.0)
    from types import SimpleNamespace

    rig.registry.register(SimpleNamespace(
        package_id="recipe-child", version="1.0.0", schema_version="1",
        compiler_version="4.0.0", methodology_family="m",
        recipe_fingerprint="", methodology_fingerprint="",
        fingerprint="cf" * 16,
        to_bounded_dict=lambda: {"compiled_form": {"typed_dag": _DAG}}),
        owner_scope="u:abc")

    # executor 内部自建 Driver（无 plan_executor → 真实 geocompute 路径，
    # 空 session 数据必失败）—— 注入 gated executor 使子执行确定性
    import app.services.workflow_runtime.driver as _driver_mod

    _real_driver = _driver_mod.Driver

    def _gated_driver(*a, **kw):
        kw.setdefault("plan_executor", gated_executor)
        kw.setdefault("descriptor_probe", probe)
        return _real_driver(*a, **kw)

    async def scenario():
        monkeypatch.setattr(_driver_mod, "Driver", _gated_driver)
        task = asyncio.create_task(executor(
            {"node_id": "cap:sw", "subworkflow_package_id": "recipe-child"},
            parent={"instance_id": parent["instance_id"],
                    "package_id": "recipe-x", "remaining_s": 15.0},
            parent_visited=[], session_id="s1",
            input_refs=["ref:data-1"]))
        # 等子实例展开且 buffer 在飞（RUNNING）
        child_id = ""
        for _ in range(200):
            rows = [r for r in store.list_owner_instances("u:abc")
                    if r.get("parent_instance_id") == parent["instance_id"]]
            if rows:
                child_id = rows[0]["instance_id"]
                if store.get_node_states(child_id).get(
                        "transform:buffer:subject") == C.NodeState.RUNNING:
                    break
            await asyncio.sleep(0.05)
        assert child_id, "child instance never started"
        # 父取消 → watcher（0.5s 周期）应传播到子实例
        store.update_instance(parent["instance_id"],
                              fields={"cancel_requested": True})
        await asyncio.sleep(0.7)  # > PARENT_POLL_S：旗标必达子实例
        release.set()
        result = await task
        return result, child_id

    result, child_id = asyncio.run(scenario())
    assert result["ok"] is False
    assert result["error_code"] == "SUBWORKFLOW_CANCELLED"
    assert store.get_instance(child_id)[
        "status"] == C.InstanceStatus.CANCELLED
    child_states = store.get_node_states(child_id)
    assert C.NodeState.CANCELLED in set(child_states.values())


def test_child_deadline_inherits_parent_remaining(factory, monkeypatch):
    """子 deadline = min(自身, 父剩余)（嵌套链不重置时钟验收）。"""
    from app.services.workflow_runtime.subworkflow import (
        SubworkflowExecutor,
    )

    rig = Rig(factory)
    svc = rig.svc
    # 注册一个最小 child package 供 instantiate
    from types import SimpleNamespace

    from app.services.workflow_runtime.registry import PackageRegistry

    reg = PackageRegistry(factory=factory)
    child_dag = {"nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False},
    ], "edges": [], "primary_output": "data:subject"}
    reg.register(SimpleNamespace(
        package_id="recipe-child", version="1.0.0", schema_version="1",
        compiler_version="4.0.0", methodology_family="m",
        recipe_fingerprint="", methodology_fingerprint="",
        fingerprint="cf" * 16,
        to_bounded_dict=lambda: {"compiled_form": {"typed_dag": child_dag}}),
        owner_scope="u:abc")
    svc.registry = reg
    seen_deadlines: List[float] = []

    class SpyDriver:
        def __init__(self, *a, **kw):
            seen_deadlines.append(float(kw.get("deadline_s", -1)))
            self.store = kw.get("store")

        async def run(self, *a, **kw):
            return {"status": "succeeded", "states": {}}

    monkeypatch.setattr(
        "app.services.workflow_runtime.driver.Driver",
        SpyDriver, raising=False)
    executor = SubworkflowExecutor(svc, owner_scope="u:abc",
                                   deadline_s=30.0)
    node = {"node_id": "cap:sw", "subworkflow_package_id": "recipe-child"}
    result = asyncio.run(executor(
        node, parent={"instance_id": "wi-parent", "package_id": "recipe-x",
                      "remaining_s": 2.0},
        parent_visited=["recipe-x"], session_id="s1", input_refs=[]))
    assert result["ok"] is True
    assert seen_deadlines and seen_deadlines[-1] == 2.0  # 继承父剩余
    # 无 remaining → 自身 deadline
    result2 = asyncio.run(executor(
        node, parent={"instance_id": "wi-parent",
                      "package_id": "recipe-x"},
        parent_visited=["recipe-x"], session_id="s1", input_refs=[]))
    assert result2["ok"] is True
    assert seen_deadlines[-1] == 30.0
